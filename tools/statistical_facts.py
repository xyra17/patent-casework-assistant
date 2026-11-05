"""查询执行时固定指标定义与范围，不能从回答或别名猜测业务含义。"""
from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import re
from sqlglot import exp
from tools.sql_ast import bound_tree


def decimal_value(value):
    if value is None or isinstance(value, bool): return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError): return None


def normalize_expression(node):
    node = node.copy()
    for col in node.find_all(exp.Column): col.set('table', None)
    return node.sql(dialect='mysql').replace('`', '').replace(' ', '').lower()


def year_scope(year):
    year = int(year)
    return {'start': date(year, 1, 1).isoformat(), 'end': date(year + 1, 1, 1).isoformat()}


def month_scope(year, month):
    year, month = int(year), int(month)
    return {'start': date(year, month, 1).isoformat(),
            'end': date(year + (month == 12), month % 12 + 1, 1).isoformat()}


def _conditions(node):
    if isinstance(node, exp.And): return _conditions(node.this) + _conditions(node.expression)
    if isinstance(node, exp.Paren): return _conditions(node.this)
    return [node]


def _literal(node):
    if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal): return '-' + node.this.this
    return node.this if isinstance(node, exp.Literal) else None


def _time_field(node, schema):
    if isinstance(node, (exp.Year, exp.Month, exp.Quarter)): return True
    names = [c.name for c in node.find_all(exp.Column)]
    return any((schema.get(name) or {}).get('role') == 'time' or
               re.search(r'(date|time|year|month|quarter|_at$)', name, re.I) for name in names)


def _finite_filter(node):
    """Only explicit literal predicates; subqueries/functions stay unsupported."""
    if isinstance(node, exp.Paren): return _finite_filter(node.this)
    if isinstance(node, (exp.Or, exp.And)):
        left, right = _finite_filter(node.this), _finite_filter(node.expression)
        if left is not None and right is not None: return left + right
    if isinstance(node, (exp.EQ, exp.NEQ)) and isinstance(node.this, exp.Column) and _literal(node.expression) is not None:
        return [_literal(node.expression)]
    if isinstance(node, exp.Not) and isinstance(node.this, exp.In): return _finite_filter(node.this)
    if isinstance(node, exp.In) and isinstance(node.this, exp.Column) and node.expressions and not node.args.get('query'):
        values = [_literal(v) for v in node.expressions]
        if all(v is not None for v in values): return values
    return None


def query_scope(select, schema):
    """支持 AND 的绝对年份/日期区间；相对时间、OR、复杂谓词如实拒绝推断。"""
    where = select.args.get('where')
    scope = {'status': 'unbounded', 'start': None, 'end': None, 'field': None}
    filters = []
    if not where: return scope, filters
    start, end, fields, unknown = None, None, set(), False
    for condition in _conditions(where.this):
        columns = list(condition.find_all(exp.Column))
        if not columns: continue
        if isinstance(condition, exp.Not) and isinstance(condition.this, exp.Is) and isinstance(condition.this.this, exp.Column) and isinstance(condition.this.expression, exp.Null):
            # NOT NULL only removes undated records; bounds below remain valid.
            if _time_field(condition, schema): continue
            filters.append({'field': condition.this.this.name, 'op': 'not_null'})
            continue
        if isinstance(condition, exp.Is) and isinstance(condition.this, exp.Column) and isinstance(condition.expression, exp.Null):
            field = condition.this.name
            filters.append({'field': field, 'label': (schema.get(field) or {}).get('label', field), 'op': 'is_null'})
            continue
        if not _time_field(condition, schema):
            if isinstance(condition, exp.EQ) and isinstance(condition.this, exp.Column) and _literal(condition.expression) is not None:
                filters.append({'field': condition.this.name, 'value': _literal(condition.expression)})
            elif isinstance(condition, exp.In) and isinstance(condition.this, exp.Column) and condition.expressions and all(_literal(v) is not None for v in condition.expressions) and not condition.args.get('query'):
                filters.append({'field': condition.this.name, 'values': [_literal(v) for v in condition.expressions], 'op': 'in'})
            elif (values := _finite_filter(condition)) is not None and len(values) <= 64:
                filters.append({'field': '', 'values': values, 'predicate': condition.sql(dialect='mysql')})
            else:
                filters.append({'field': '', 'value': None, 'unsupported': condition.sql(dialect='mysql')})
            continue
        fields.update(c.sql(dialect='mysql') for c in columns)
        left, right = condition.this, condition.args.get('expression')
        value = _literal(right)
        try:
            if isinstance(condition, exp.EQ) and (isinstance(left, exp.Year) or isinstance(left, exp.Column) and left.name.lower() in ('year', 'sale_year')):
                interval = year_scope(value)
                lo, hi = interval['start'], interval['end']
            elif isinstance(condition, exp.Between) and isinstance(left, exp.Column):
                lo = date.fromisoformat(str(_literal(condition.args['low']))).isoformat()
                hi = (date.fromisoformat(str(_literal(condition.args['high']))) + timedelta(days=1)).isoformat()
            elif isinstance(left, exp.Column) and value is not None:
                day = date.fromisoformat(str(value))
                if isinstance(condition, exp.EQ): lo, hi = day.isoformat(), (day + timedelta(days=1)).isoformat()
                elif isinstance(condition, exp.GTE): lo, hi = day.isoformat(), None
                elif isinstance(condition, exp.GT): lo, hi = (day + timedelta(days=1)).isoformat(), None
                elif isinstance(condition, exp.LT): lo, hi = None, day.isoformat()
                elif isinstance(condition, exp.LTE): lo, hi = None, (day + timedelta(days=1)).isoformat()
                else: raise ValueError('unsupported time operator')
            else: raise ValueError('unsupported time condition')
            start = max(start, lo) if start and lo else start or lo
            end = min(end, hi) if end and hi else end or hi
        except (TypeError, ValueError, OverflowError): unknown = True
    scope.update(start=start, end=end, field=','.join(sorted(fields)) or None)
    if fields:
        scope['status'] = 'bounded' if (start or end) and (not start or not end or start < end) and not unknown and len(fields) == 1 else 'unsupported'
    return scope, filters


def build_contract(result, metrics=(), schema=None, *, trusted_semantic=False):
    """合同只有定义与范围，值始终按来源原始行/列读取。"""
    schema = schema or {}
    columns = result.get('columns') or []
    meta = result.get('semantic') or {}
    definitions = []
    for metric in metrics:
        try: expression = normalize_expression(bound_tree('SELECT ' + metric['expression']).expressions[0])
        except Exception: expression = None
        definitions.append((metric, expression))
    try:
        tree = bound_tree(result.get('sql', ''), result.get('params') or [])
        selects = list(tree.find_all(exp.Select))
    except Exception:
        selects = []
    items = []
    for index, column in enumerate(columns):
        candidates = [(select, expression.unalias()) for select in selects for expression in select.expressions
                      if expression.alias_or_name == column or
                      len(select.expressions) == len(columns) and select is selects[0] and select.expressions[index] is expression]
        cfg = next((m for m, _ in definitions if trusted_semantic and m.get('key') == column), None)
        selected = None
        if cfg:
            selected = next(((select, expression) for select, expression in candidates if not isinstance(expression, exp.Column)), None)
        else:
            selected = candidates[0] if candidates else None
            if selected:
                normalized = normalize_expression(selected[1])
                # 同名别名不能给另一表达式授予该指标含义。
                tables = {table.name for table in selected[0].find_all(exp.Table)}
                aliases = {table.alias_or_name: table.name for table in selected[0].find_all(exp.Table)}
                lineage = {aliases.get(col.table) if col.table else (next(iter(tables)) if len(tables) == 1 else None) for col in selected[1].find_all(exp.Column)}
                cfg = next((m for m, expression in definitions if normalized == expression and m.get('table') in tables
                            and (not lineage or lineage == {m.get('table')})), None)
        expression = selected[1] if selected else None
        role = (meta.get(column) or {}).get('role')
        aggregate = expression is not None and any(True for _ in expression.find_all(exp.AggFunc))
        if not cfg and not aggregate and role != 'metric':
            numeric = any(index < len(row) and decimal_value(row[index]) is not None for row in result.get('rows', []))
            if trusted_semantic or role in ('id', 'foreign_key', 'time') or not numeric: continue
        unit, names, label = None, [], column
        if cfg:
            label, unit = cfg.get('label') or column, cfg.get('unit')
            names = [cfg['key'], label, *cfg.get('synonyms', [])]
            if cfg.get('format') == 'integer' and not unit: unit = 'count'
        elif isinstance(expression, exp.Count) and isinstance(expression.this, exp.Star):
            label, unit, names = '记录数', 'count', ['记录数', '记录数量', '数量', 'COUNT(*)']
        elif isinstance(expression, exp.Column) and (schema.get(expression.name) or {}).get('role') == 'metric':
            info = schema[expression.name]
            label, unit = info.get('label', expression.name), info.get('unit')
            names = [label, expression.name, *info.get('synonyms', [])]
        time, filters = query_scope(selected[0], schema) if selected else ({'status': 'unsupported'}, [])
        # 相关子查询、窗口和复合谓词的作用域不靠猜测。
        if selected and (list(selected[1].find_all(exp.Subquery)) or list(selected[1].find_all(exp.Window))
                         or list(selected[1].find_all(exp.Case)) and _time_field(selected[1], schema)):
            time = {'status': 'unsupported'}
        items.append({'column': column, 'index': index, 'label': label, 'names': list(dict.fromkeys(names)),
                      'unit': unit, 'definition_verified': bool(names), 'time': time, 'filters': filters})
    dimensions = []
    for index, column in enumerate(columns):
        if column in {m['column'] for m in items}: continue
        expressions = [expression.unalias() for select in selects for expression in select.expressions
                       if expression.alias_or_name == column]
        expression = next((e for e in expressions if not isinstance(e, exp.Column)), expressions[0] if expressions else None)
        temporal_columns = list(expression.find_all(exp.Column)) if expression is not None else []
        dimensions.append({'column': column, 'index': index, **(meta.get(column) or {}),
                           'time_field': temporal_columns[0].name if len(temporal_columns) == 1 else None})
    return {'version': 1, 'metrics': items, 'dimensions': dimensions}
