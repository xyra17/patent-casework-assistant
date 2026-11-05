"""语义查询执行器：指标表达式 + 关系 join + 层级下钻。

只接受语义层已定义的指标/字段，生成参数化只读 SQL；不接收用户原始 SQL。
"""
from __future__ import annotations

import re
import json
from collections import deque
from typing import Any

from api import context, evidence, source_policy
from rules import semantic as rule_semantic
from tools.chart_utils import build_chart_spec
from tools.mysql_tools import run_read_query
from tools.semantic_layer import SemanticLayer, semantic_layer

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WRITE_WORDS = re.compile(r"\b(DROP|DELETE|UPDATE|INSERT|TRUNCATE|ALTER|GRANT|REVOKE)\b", re.I)
_SQL_WORDS = {
    "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "LIMIT", "COUNT", "SUM", "AVG",
    "MIN", "MAX", "DISTINCT", "AS", "AND", "OR", "NULL", "CASE", "WHEN", "THEN",
    "ELSE", "END", "YEAR", "QUARTER", "MONTH", "DAY", "DATE_FORMAT", "CONCAT", "CAST",
}


class SemanticQueryError(ValueError):
    pass


def _split_table_column(value: str) -> tuple[str, str]:
    if "." not in value:
        raise SemanticQueryError(f"关系字段必须是 table.column：{value}")
    table, column = value.split(".", 1)
    return table.strip(), column.strip()


class SemanticQueryEngine:
    def __init__(self, model: SemanticLayer | None = None) -> None:
        self.model = model or semantic_layer

    def _relationships(self) -> list[dict]:
        return list(self.model._config.get("relationships", []))

    def _graph(self) -> dict[str, list[dict]]:
        graph: dict[str, list[dict]] = {}
        for rel in self._relationships():
            a_table, a_col = _split_table_column(str(rel.get("from", "")))
            b_table, b_col = _split_table_column(str(rel.get("to", "")))
            edge = {"a": a_table, "a_col": a_col, "b": b_table, "b_col": b_col}
            graph.setdefault(a_table, []).append(edge)
            graph.setdefault(b_table, []).append(edge)
        return graph

    def _path(self, start: str, target: str) -> list[dict]:
        if start == target:
            return []
        graph = self._graph()
        queue = deque([(start, [])])
        seen = {start}
        while queue:
            table, path = queue.popleft()
            for edge in graph.get(table, []):
                neighbor = edge["b"] if edge["a"] == table else edge["a"]
                if neighbor in seen:
                    continue
                next_path = path + [edge]
                if neighbor == target:
                    return next_path
                seen.add(neighbor)
                queue.append((neighbor, next_path))
        raise SemanticQueryError(f"表 {start} 与 {target} 之间没有语义关系路径")

    def _columns_for(self, table: str) -> set[str]:
        cfg = self.model._tables.get(table, {})
        return set((cfg.get("columns") or {}).keys())

    def _qualify(self, expression: str, table: str, alias: str) -> str:
        if not expression or not table:
            return expression
        columns = self._columns_for(table)
        if not columns:
            return expression

        def repl(match: re.Match) -> str:
            token = match.group(0)
            if token.upper() in _SQL_WORDS or token.lower() not in columns:
                return token
            start, end = match.span()
            # 已有表别名或紧邻函数名时不重复加前缀
            if start > 0 and expression[start - 1] == ".":
                return token
            if expression[end:].lstrip().startswith("("):
                return token
            return f"{alias}.{token}"

        return _IDENT.sub(repl, expression)

    def _resolve_dimension(self, name: str) -> dict:
        field = self.model.resolve_field(name)
        if not field:
            raise SemanticQueryError(f"语义层没有字段：{name}")
        return field

    def _plan(
        self,
        metric: dict,
        dimensions: list[dict],
        filters: list[dict],
        limit: int = 100,
        query: str = "",
        extra_tables: list[str] | None = None,
    ) -> dict:
        dimensions=rule_semantic.time_specs(self.model,[metric],dimensions)
        filters=rule_semantic.time_specs(self.model,[metric],filters)
        filters = rule_semantic.add_filter_specs(self.model, [metric], filters)
        metric_table = metric.get("table")
        if not metric_table:
            raise SemanticQueryError(f"指标缺少表：{metric.get('key')}")
        required = [metric_table] + [item["table"] for item in dimensions + filters]
        required += [table for table in (extra_tables or []) if table]
        aliases: dict[str, str] = {metric_table: "t0"}
        joins: list[str] = []

        def ensure_table(table: str) -> str:
            if table in aliases:
                return aliases[table]
            if metric.get('rule_versions'):
                from rules.formula import guard_join
                guard_join(self.model,metric_table,table)
            path = self._path(metric_table, table)
            for edge in path:
                if edge["a"] not in aliases and edge["b"] not in aliases:
                    raise SemanticQueryError("关系路径无法连接")
                current = edge["a"] if edge["a"] in aliases else edge["b"]
                other = edge["b"] if current == edge["a"] else edge["a"]
                if other in aliases:
                    continue
                current_alias = aliases[current]
                other_alias = f"t{len(aliases)}"
                aliases[other] = other_alias
                current_col = edge["a_col"] if current == edge["a"] else edge["b_col"]
                other_col = edge["b_col"] if current == edge["a"] else edge["a_col"]
                joins.append(
                    f"LEFT JOIN `{other}` {other_alias} ON {current_alias}.`{current_col}` = {other_alias}.`{other_col}`"
                )
            if table not in aliases:
                raise SemanticQueryError(f"无法连接表：{table}")
            return aliases[table]

        for table in required:
            ensure_table(table)

        metric_alias = metric["key"]
        metric_expr,formula_params = rule_semantic.metric_expression(self.model,metric,aliases[metric_table],self._qualify)
        if not metric_expr:
            raise SemanticQueryError("指标表达式为空")
        select_parts = []
        group_parts = []
        columns_meta: dict[str, dict] = {}
        used_aliases: set[str] = set()
        for index, dim in enumerate(dimensions):
            alias = str(dim.get("key") or dim.get("name") or f"dim_{index}")
            alias = re.sub(r"[^A-Za-z0-9_]", "_", alias)[:40] or f"dim_{index}"
            while alias in used_aliases:
                alias += "_"
            used_aliases.add(alias)
            table = dim.get("table") or metric_table
            table_alias = ensure_table(table)
            expr = dim.get("expression") or f"{table_alias}.`{dim['name']}`"
            if dim.get("expression"):
                expr = self._qualify(expr, table, table_alias)
            select_parts.append(f"{expr} AS `{alias}`")
            group_parts.append(f"`{alias}`")
            columns_meta[alias] = {
                "name": alias,
                "label": dim.get("label") or dim.get("name") or alias,
                "role": dim.get("role") or "dimension",
                "table": table,
                "unit": None,
            }
        select_parts.append(f"{metric_expr} AS `{metric_alias}`")
        columns_meta[metric_alias] = {
            "name": metric_alias,
            "label": metric.get("label") or metric_alias,
            "role": "metric",
            "table": metric_table,
            "unit": metric.get("unit"),
            "aggregation": metric_expr,
        }

        where_parts: list[str] = []
        params: list[Any] = list(formula_params)
        for item in filters:
            field = item["field"]
            table = item["table"]
            table_alias = ensure_table(table)
            expr = item.get("expression") or f"{table_alias}.`{field}`"
            if item.get("expression"):
                expr = self._qualify(expr, table, table_alias)
            op = item.get("op", "eq")
            value = item.get("value")
            if op == "in":
                values = value if isinstance(value, (list, tuple)) else [value]
                placeholders = ", ".join(["%s"] * len(values))
                where_parts.append(f"{expr} IN ({placeholders})")
                params.extend(values)
            elif op in ("eq", "ne", "gt", "gte", "lt", "lte", "like"):
                operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "like": "LIKE"}
                where_parts.append(f"{expr} {operators[op]} %s")
                params.append(value)
            else:
                raise SemanticQueryError(f"不支持的过滤操作：{op}")

        sql = (
            f"SELECT {', '.join(select_parts)} "
            f"FROM `{metric_table}` {aliases[metric_table]} "
            + (" ".join(joins) + " " if joins else "")
            + ("WHERE " + " AND ".join(where_parts) + " " if where_parts else "")
            + (f"GROUP BY {', '.join(group_parts)} " if group_parts else "")
            + f"ORDER BY `{metric_alias}` DESC LIMIT %s"
        )
        params.append(max(1, min(int(limit), 500)))
        return {
            "sql": sql,
            "params": params,
            "columns_meta": columns_meta,
            "metric": metric,
            "dimensions": dimensions,
            "joins": joins,
            "query": query,
        }

    def _read_plan(self, plan: dict) -> dict:
        # 仅受控计划的最终 LIMIT 多取一行；保存实际执行参数，计划本身不变。
        params = list(plan['params'])
        requested = params[-1]
        params[-1] = requested + 1
        with rule_semantic.execution_scope():
            raw = run_read_query(plan['sql'], params, limit=requested)
        plan['params'] = params
        meta = raw.setdefault('completeness', {
            'version': 1, 'returned_rows': len(raw['rows']), 'query_rows': None,
            'has_more': None, 'fetch_truncated': None, 'sql_limited': None,
            'evidence_rows': len(raw['rows']),
        })
        meta.update(scope='semantic_groups' if plan['dimensions'] else 'semantic_aggregate',
                    requested_limit=requested, matched_rows=None)
        return raw

    def _execute(self, plan: dict) -> dict:
        raw = self._read_plan(plan)
        field_meta = plan["columns_meta"]
        columns = raw["columns"]
        rows = raw["rows"]
        chart = build_chart_spec(
            columns, rows,
            title=f"{plan['metric'].get('label', '指标')}分析",
            query=plan.get("query", ""),
            field_meta=field_meta,
        )
        result = {
            "effective_bindings": [{'metric_key':plan['metric']['key'],'binding':plan['metric']['rule_binding']}] if plan['metric'].get('rule_binding') else [],
            "rule_versions": plan['metric'].get('rule_versions', []),
            "metric": plan["metric"],
            "dimensions": plan["dimensions"],
            "columns": columns,
            "rows": rows,
            "rowCount": raw["rowCount"],
            "completeness": raw["completeness"],
            "sql": plan["sql"],
            "params": plan['params'],
            "joins": plan["joins"],
            "semantic": field_meta,
            "chart": chart,
        }
        result['statistics'] = self._statistical_contract(result)
        return result

    def _build_group(self, metrics: list[dict], dims: list[dict], filters: list[dict]) -> dict:
        """构建一个指标组（同表指标）的 GROUP BY 子查询。"""
        dims=rule_semantic.time_specs(self.model,metrics,dims)
        filters=rule_semantic.time_specs(self.model,metrics,filters)
        table = metrics[0].get("table")
        if not table:
            raise SemanticQueryError("指标缺少表定义")
        required = [table] + [item["table"] for item in dims + filters]
        aliases: dict[str, str] = {table: "t0"}
        joins: list[str] = []

        def ensure(target: str) -> str:
            if target in aliases:
                return aliases[target]
            if any(m.get('rule_versions') for m in metrics):
                from rules.formula import guard_join
                guard_join(self.model,table,target)
            path = self._path(table, target)
            for edge in path:
                if edge["a"] not in aliases and edge["b"] not in aliases:
                    raise SemanticQueryError("关系路径无法连接")
                current = edge["a"] if edge["a"] in aliases else edge["b"]
                other = edge["b"] if current == edge["a"] else edge["a"]
                if other in aliases:
                    continue
                current_alias = aliases[current]
                other_alias = f"t{len(aliases)}"
                aliases[other] = other_alias
                left_col = edge["a_col"] if current == edge["a"] else edge["b_col"]
                right_col = edge["b_col"] if current == edge["a"] else edge["a_col"]
                joins.append(f"LEFT JOIN `{other}` {other_alias} ON {current_alias}.`{left_col}` = {other_alias}.`{right_col}`")
            return aliases[target]

        for target in required:
            ensure(target)

        select_parts: list[str] = []
        group_parts: list[str] = []
        columns_meta: dict[str, dict] = {}
        for index, dim in enumerate(dims):
            alias = f"dim_{index}"
            dim_table = dim.get("table") or table
            dim_alias = ensure(dim_table)
            expression = dim.get("expression") or f"{dim_alias}.`{dim['name']}`"
            if dim.get("expression"):
                expression = self._qualify(expression, dim_table, dim_alias)
            select_parts.append(f"{expression} AS `{alias}`")
            group_parts.append(f"`{alias}`")
            columns_meta[alias] = {
                "name": alias, "label": dim.get("label") or dim.get("name") or alias,
                "role": "dimension", "table": dim_table,
            }
        formula_params=[]
        for metric in metrics:
            metric_alias = metric["key"]
            metric_expr,bound_params=rule_semantic.metric_expression(self.model,metric,aliases[table],self._qualify)
            formula_params.extend(bound_params)
            select_parts.append(f"{metric_expr} AS `{metric_alias}`")
            columns_meta[metric_alias] = {
                "name": metric_alias, "label": metric.get("label") or metric_alias,
                "role": "metric", "table": table, "unit": metric.get("unit"),
                "aggregation": metric_expr,
            }
        where_parts: list[str] = []
        params: list[Any] = list(formula_params)
        for item in filters:
            filter_table = item["table"]
            filter_alias = ensure(filter_table)
            expression = item.get("expression") or f"{filter_alias}.`{item['name']}`"
            if item.get("expression"):
                expression = self._qualify(expression, filter_table, filter_alias)
            op = item.get("op", "eq")
            value = item.get("value")
            if op == "in":
                values = value if isinstance(value, (list, tuple)) else [value]
                where_parts.append(f"{expression} IN ({', '.join(['%s'] * len(values))})")
                params.extend(values)
            elif op in ("eq", "ne", "gt", "gte", "lt", "lte", "like"):
                operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "like": "LIKE"}
                where_parts.append(f"{expression} {operators[op]} %s")
                params.append(value)
            else:
                raise SemanticQueryError(f"不支持的过滤操作：{op}")
        sql = (
            f"SELECT {', '.join(select_parts)} FROM `{table}` {aliases[table]} "
            + (" ".join(joins) + " " if joins else "")
            + ("WHERE " + " AND ".join(where_parts) + " " if where_parts else "")
            + (f"GROUP BY {', '.join(group_parts)}" if group_parts else "")
        )
        return {"sql": sql.strip(), "params": params, "columns_meta": columns_meta,
                "joins": joins, "table": table, "metrics": metrics}

    def plan_multi(self, metric_names: list[str], dimensions: list[str] | None = None,
                   filters: dict[str, Any] | None = None, limit: int = 100,
                   query: str = "") -> dict:
        """多指标：按指标所在表拆分组，各自先聚合，再按维度键 outer join。"""
        if not metric_names:
            raise SemanticQueryError("至少选择一个指标")
        metrics = []
        for name in metric_names:
            metric = rule_semantic.metric_for(self.model, name)
            if not metric:
                raise SemanticQueryError(f"未知指标：{name}")
            metrics.append(metric)
        dims = [self._resolve_dimension(name) for name in (dimensions or [])]
        filter_specs = []
        for field, value in (filters or {}).items():
            if isinstance(value, dict):
                op = value.get("op", "eq")
                val = value.get("value")
            else:
                op, val = "eq", value
            resolved = self._resolve_dimension(field)
            filter_specs.append({**resolved, "field": resolved["name"], "op": op, "value": val})
        groups: dict[tuple, list[dict]] = {}
        for metric in metrics:
            defaults = rule_semantic.merge_filters(self.model, [metric], {})
            groups.setdefault((metric.get('table',''), metric.get('rule_binding',{}).get('time_field'),json.dumps(defaults,sort_keys=True,ensure_ascii=False)), []).append(metric)
        group_plans = [self._build_group(group, dims, rule_semantic.add_filter_specs(self.model, group, filter_specs)) for group in groups.values()]
        if len(group_plans) == 1:
            plan = group_plans[0]
            sql = f"SELECT * FROM ({plan['sql']}) g0 ORDER BY `{metrics[0]['key']}` DESC LIMIT %s"
            return {
                "sql": sql, "params": plan["params"] + [max(1, min(int(limit), 500))],
                "columns_meta": plan["columns_meta"], "metric": metrics[0],
                "metrics": metrics, "dimensions": dims, "joins": plan["joins"], "query": query,
            }
        # 多表：每个指标组先聚合，再用维度键 union + left join，避免事实表直接 join 造成重复计数。
        ctes = []
        group_names = []
        all_params: list[Any] = []
        for index, plan in enumerate(group_plans):
            name = f"g{index}"
            group_names.append(name)
            ctes.append(f"{name} AS ({plan['sql']})")
            all_params.extend(plan["params"])
        dim_selects = [f"`dim_{i}`" for i in range(len(dims))]
        keys_sql = " UNION ".join(f"SELECT {', '.join(dim_selects)} FROM {name}" for name in group_names) if dims else ""
        if dims:
            ctes.append(f"dim_keys AS ({keys_sql})")
            select_dims = ", ".join(f"k.`dim_{i}` AS `dim_{i}`" for i in range(len(dims)))
            select_metrics = []
            for group in group_plans:
                for metric in group["metrics"]:
                    select_metrics.append(f"{group_names[group_plans.index(group)]}.`{metric['key']}` AS `{metric['key']}`")
            joins = [f"LEFT JOIN {name} {name} ON " + " AND ".join(
                f"k.`dim_{i}` <=> {name}.`dim_{i}`" for i in range(len(dims))
            ) for name in group_names]
            sql = (
                "WITH " + ", ".join(ctes) + " "
                f"SELECT {select_dims}, {', '.join(select_metrics)} FROM dim_keys k "
                + " ".join(joins)
                + f" ORDER BY `{metrics[0]['key']}` DESC LIMIT %s"
            )
        else:
            select_metrics = []
            for index, group in enumerate(group_plans):
                for metric in group["metrics"]:
                    select_metrics.append(f"{group_names[index]}.`{metric['key']}` AS `{metric['key']}`")
            cross = " CROSS JOIN ".join(f"({group['sql']}) {group_names[index]}" for index, group in enumerate(group_plans))
            sql = f"SELECT {', '.join(select_metrics)} FROM {cross} LIMIT %s"
        columns_meta = {}
        for group in group_plans:
            columns_meta.update(group["columns_meta"])
        plan = {
            "sql": sql,
            "params": all_params + [max(1, min(int(limit), 500))],
            "columns_meta": columns_meta,
            "metrics": metrics,
            "metric": metrics[0],
            "dimensions": dims,
            "query": query,
        }
        return plan

    def query_multi(self, metric_names: list[str], dimensions: list[str] | None = None,
                    filters: dict[str, Any] | None = None, limit: int = 100,
                    query: str = "") -> dict:
        plan = self.plan_multi(metric_names, dimensions, filters, limit=limit, query=query)
        raw = self._read_plan(plan)
        columns = raw["columns"]
        chart = build_chart_spec(
            columns, raw["rows"],
            title="多指标分析",
            query=query,
            field_meta=plan["columns_meta"],
        )
        result = {
            "effective_bindings": [{'metric_key':m['key'],'binding':m['rule_binding']} for m in plan['metrics'] if m.get('rule_binding')],
            "rule_versions": [r for m in plan['metrics'] for r in m.get('rule_versions',[])],
            "metrics": plan["metrics"],
            "metric": plan["metric"],
            "dimensions": plan["dimensions"],
            "columns": columns,
            "rows": raw["rows"],
            "rowCount": raw["rowCount"],
            "completeness": raw["completeness"],
            "sql": plan["sql"],
            "params": plan['params'],
            "joins": plan.get("joins", []),
            "semantic": plan["columns_meta"],
            "chart": chart,
        }
        result['statistics'] = self._statistical_contract(result)
        return result

    def plan_query(
        self,
        metric_name: str,
        dimensions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        query: str = "",
    ) -> dict:
        metric = rule_semantic.metric_for(self.model, metric_name)
        if not metric:
            raise SemanticQueryError(f"未知指标：{metric_name}")
        dimensions = dimensions or []
        dims = [self._resolve_dimension(name) for name in dimensions]
        filter_specs = []
        for field, value in (filters or {}).items():
            if isinstance(value, dict):
                op = value.get("op", "eq")
                val = value.get("value")
            else:
                op, val = "eq", value
            resolved = self._resolve_dimension(field)
            filter_specs.append({**resolved, "field": resolved["name"], "op": op, "value": val})
        return self._plan(metric, dims, filter_specs, limit=limit, query=query)

    def query(
        self,
        metric_name: str,
        dimensions: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        query: str = "",
    ) -> dict:
        plan = self.plan_query(metric_name, dimensions, filters, limit=limit, query=query)
        return self._execute(plan)

    def plan_drilldown(
        self,
        hierarchy: str,
        field: str,
        value: Any,
        metric_name: str,
        limit: int = 100,
    ) -> tuple[dict, dict]:
        h = self.model.hierarchy_by_name(hierarchy)
        if not h:
            raise SemanticQueryError(f"未知层级：{hierarchy}")
        levels = list(h.get("levels") or [])
        if field not in levels:
            raise SemanticQueryError(f"字段 {field} 不属于层级 {hierarchy}")
        index = levels.index(field)
        if index + 1 >= len(levels):
            raise SemanticQueryError("已经是最细层级，无法继续下钻")
        current, next_level = field, levels[index + 1]
        table = h.get("table") or ""
        current_expr = self.model.level_expression(h, current)
        next_expr = self.model.level_expression(h, next_level)
        current_field = self.model.resolve_field(f"{table}.{current}") if table else None
        next_field = self.model.resolve_field(f"{table}.{next_level}") if table else None
        current_table = (current_field or next_field or {}).get("table") or table
        if not current_table:
            raise SemanticQueryError("层级缺少表定义")
        dim = {
            "name": next_level,
            "key": next_level,
            "label": next_level,
            "role": "dimension",
            "table": current_table,
            "expression": next_expr or None,
        }
        filter_spec = {
            "name": current,
            "field": current,
            "label": current,
            "role": "dimension",
            "table": current_table,
            "expression": current_expr or None,
            "op": "eq",
            "value": value,
        }
        metric = rule_semantic.metric_for(self.model, metric_name)
        if not metric:
            raise SemanticQueryError(f"未知指标：{metric_name}")
        plan = self._plan(
            metric, [dim], [filter_spec], limit=limit,
            query=f"{h.get('label', hierarchy)}下钻：{current} = {value}",
            extra_tables=[h.get("table")] if h.get("table") else [],
        )
        return plan, {
            "key": h.get("key"), "label": h.get("label"), "field": current,
            "next": next_level, "levels": levels,
        }

    def drilldown(
        self,
        hierarchy: str,
        field: str,
        value: Any,
        metric_name: str,
        limit: int = 100,
    ) -> dict:
        plan, hierarchy_meta = self.plan_drilldown(hierarchy, field, value, metric_name, limit=limit)
        result = self._execute(plan)
        result["hierarchy"] = hierarchy_meta
        return result

    def _statistical_contract(self, result: dict) -> dict:
        from tools.statistical_facts import build_contract
        schema = {key: col for table in self.model._tables.values() for key, col in table.get('columns', {}).items()}
        from rules.engine import business_rules
        definitions = []
        for original in result.get('metrics') or [result.get('metric', {})]:
            metric = dict(original)
            versions = {(r['id'], r['revision']) for r in metric.get('rule_versions', [])}
            aliases = [name for rule in business_rules()
                       if (rule['id'], rule['revision']) in versions and rule['binding']['metric_key'] == metric.get('key')
                       for name in [rule['subject'], *rule.get('match_terms', []), *rule.get('aliases', [])]]
            metric['synonyms'] = [*metric.get('synonyms', []), *aliases]
            definitions.append(metric)
        return build_contract(result, definitions, schema, trusted_semantic=True)

    def format_result(self, result: dict) -> str:
        if not result.get('statistics'):
            result['statistics'] = self._statistical_contract(result)
        columns = result.get("columns") or []
        rows = result.get("rows") or []
        labels = [result.get("semantic", {}).get(column, {}).get("label", column) for column in columns]
        metric_label = "、".join(m.get("label", "") for m in result.get("metrics", [result.get("metric", {})]))
        lines = [f"指标：{metric_label}；SQL：{result['sql']}"]
        if result.get('params'): lines.append('查询参数：'+json.dumps(result['params'],ensure_ascii=False))
        if result.get('rule_versions'): lines.append('已执行规则版本：'+json.dumps(result['rule_versions'],ensure_ascii=False))
        from tools.result_contract import format_table
        return format_table(result, labels) + "\n统计核验定义（资料）：" + json.dumps(result["statistics"], ensure_ascii=False) + "\n" + "\n".join(lines)



engine = SemanticQueryEngine()


def _record(result: dict, title: str) -> str:
    thread_id = context.get_thread_id()
    if not thread_id:
        return ""
    result.setdefault('completeness', {})['evidence_rows'] = len(result.get('rows') or [])
    formatted = engine.format_result(result)
    return evidence.add(thread_id, {
        "type": "database",
        "title": title,
        "sql": result.get("sql", ""),
        "params": result.get('params', []),
        "columns": result.get("columns", []),
        "rows": (result.get("rows") or [])[:500],
        "completeness": result.get("completeness"),
        "statistics": result.get("statistics"),
        "rowCount": result.get("rowCount", 0),
        "snippet": formatted[:1200],
        "chart": result.get("chart"),
        "semantic": result.get("semantic", {}),
        "rule_versions": result.get('rule_versions', []),
        "effective_bindings": result.get('effective_bindings', []),
    })


def run_semantic_query(metric: str = "", dimensions: list[str] | None = None,
                       filters: dict[str, Any] | None = None, limit: int = 100,
                       metrics: list[str] | None = None) -> str:
    names = list(metrics or [])
    if metric:
        names.insert(0, metric)
    names = list(dict.fromkeys(names))
    if not names:
        return "语义查询失败：至少选择一个指标"
    result = engine.query_multi(names, dimensions, filters, limit=limit) if len(names) > 1 else engine.query(names[0], dimensions, filters, limit=limit)
    label = "、".join(m.get("label", "") for m in result.get("metrics", [result["metric"]]))
    evidence_id = _record(result, f"语义查询：{label}")
    return (f"[证据 {evidence_id}] " if evidence_id else "") + engine.format_result(result)


def run_semantic_drilldown(hierarchy: str, field: str, value: str,
                           metric: str, limit: int = 100) -> str:
    result = engine.drilldown(hierarchy, field, value, metric, limit=limit)
    evidence_id = _record(result, f"语义下钻：{hierarchy}.{field}")
    return (f"[证据 {evidence_id}] " if evidence_id else "") + engine.format_result(result)


from langchain_core.tools import tool  # noqa: E402


@tool
def semantic_query(metric: str = "", metrics: list[str] | None = None,
                   dimensions: list[str] | None = None,
                   filters: dict[str, Any] | None = None, limit: int = 100) -> str:
    """按语义层指标做分组统计，支持一次查询多个指标。

    参数：
    - metric: 单个指标 key/中文标签/同义词，例如 drug_count / 药品数量；
    - metrics: 多个指标列表，例如 ["sales_quantity", "total_sales"]；
    - dimensions: 分组维度字段，例如 ["therapeutic_area"]；
    - filters: 等值过滤，例如 {"therapeutic_area": "心血管"}。
    - limit: 最多返回的分组数，1～500，默认 100；额外探测一行并报告是否截断。
    返回带证据编号、范围说明的统计表和实际 SQL/参数。
    """
    denial = source_policy.source_denial("db_query")
    if denial:
        return denial
    try:
        return run_semantic_query(metric, dimensions or [], filters or {}, metrics=metrics, limit=limit)
    except Exception as exc:  # noqa: BLE001
        source_policy.record_gap("db_query", "语义查询失败，未取得请求的指标事实", reason="tool_error")
        from tools.result_contract import query_error
        return "语义查询失败：" + query_error(exc)


@tool
def semantic_drilldown(hierarchy: str, field: str, value: str, metric: str, limit: int = 100) -> str:
    """按语义层级下钻一组指标。

    例如 hierarchy="product", field="therapeutic_area", value="心血管",
    metric="drug_count"，会按产品层级下一层（剂型）聚合。
    """
    denial = source_policy.source_denial("db_query")
    if denial:
        return denial
    try:
        return run_semantic_drilldown(hierarchy, field, value, metric, limit=limit)
    except Exception as exc:  # noqa: BLE001
        source_policy.record_gap("db_query", "语义下钻失败，未取得请求的明细事实", reason="tool_error")
        from tools.result_contract import query_error
        return "语义下钻失败：" + query_error(exc)
