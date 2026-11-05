"""只通过既有指标和字段编译口径；不执行用户存入的 SQL。"""
import re
from decimal import Decimal, InvalidOperation
from copy import deepcopy
from contextlib import contextmanager
from rules import engine

_IDENT=re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$')


def field_spec(model, name):
    if not _IDENT.fullmatch(name): raise ValueError('口径字段必须使用已有的 table.column')
    table,column=name.split('.')
    metadata=(model._tables.get(table,{}).get('columns') or {}).get(column)
    if metadata is None: raise ValueError(f'语义层不存在字段：{name}；请先维护数据语义定义')
    return {**metadata,'table':table,'name':column,'field':column}


def validate_binding(binding, model=None):
    if model is None:
        from tools.semantic_layer import semantic_layer
        model=semantic_layer
    metric=model.find_metric(binding['metric_key'])
    if not metric or metric['key']!=binding['metric_key']: raise ValueError('必须选择已有语义指标 key')
    field=binding.get('field')
    if field:
        spec=field_spec(model,field)
        if spec['table']!=metric['table']: raise ValueError('计算字段必须属于所选指标的基础表')
        if binding['aggregation'] in ('sum','avg','min','max') and spec.get('role') not in ('metric',) and spec.get('type') not in ('number','integer','float','decimal'):
            raise ValueError('该聚合方式需要数值字段')
    if binding.get('time_field'):
        time=field_spec(model,binding['time_field'])
        if time['table']!=metric['table'] or time.get('role')!='time':raise ValueError('时间口径必须是基础表已有时间字段')
    if binding.get('formula'):
        if binding.get('field') or binding.get('aggregation'):raise ValueError('公式与单字段聚合不能并用')
        from rules.formula import compile_formula
        compile_formula(model,binding,metric['table'])
    validate_filters(model,metric['table'],binding.get('filters',[]))
    return binding


def validate_filters(model,table,filters):
    seen=set()
    for item in filters:
        spec=field_spec(model,item['field'])
        if item['field'] in seen: raise ValueError('同一过滤字段只能配置一次；区间请另建语义指标')
        seen.add(item['field'])
        if item['op']=='in' and (not isinstance(item['value'],list) or not item['value'] or len(item['value'])>50):
            raise ValueError('in 过滤需要1～50个值')
        if item['op']!='in' and isinstance(item['value'],list): raise ValueError('仅 in 过滤允许多个值')
        if item['op'] != 'like' and (spec.get('role')=='metric' or spec.get('type') in ('number','integer','float','decimal')):
            values=item['value'] if isinstance(item['value'],list) else [item['value']]
            try:
                if any(not Decimal(str(value)).is_finite() for value in values): raise ValueError('非有限数值')
            except (InvalidOperation, ValueError):
                raise ValueError(f"数值字段 {item['field']} 的过滤值必须是有效数字")
        if spec['table']!=table:
            from rules.formula import guard_join
            guard_join(model,table,spec['table'])


def metric_for(model, name):
    rule=next((r for r in engine.business_rules() if str(name).casefold() in [str(v).casefold() for v in [r['subject'],*r['match_terms'],*r.get('aliases',[])]]),None)
    metric=model.find_metric(rule['binding']['metric_key'] if rule else name)
    if not metric: return None
    relevant=[r for r in engine.business_rules() if r['binding']['metric_key']==metric['key']]
    result=deepcopy(metric)
    if relevant:
        for r in relevant: validate_binding(r['binding'],model)
        bindings={str(r['binding']) for r in relevant}
        if len(bindings)>1: raise ValueError('本次指标存在冲突口径，禁止执行')
        binding=relevant[0]['binding']
        result['rule_binding']=binding
        if binding.get('field'):
            column=binding['field'].split('.')[1]
            agg=binding['aggregation'];function={'count_distinct':'COUNT','sum':'SUM','count':'COUNT','avg':'AVG','min':'MIN','max':'MAX'}[agg]
            result['expression']=f"{function}({'DISTINCT ' if agg=='count_distinct' else ''}{column})"
        result['rule_versions']=[{'id':r['id'],'revision':r['revision']} for r in relevant]
    return result


def merge_filters(model, metrics, user_filters):
    defaults={}
    keys={m['key'] for m in metrics}
    for rule in engine.business_rules():
        if rule['binding']['metric_key'] not in keys: continue
        for item in rule['binding'].get('filters',[]):
            name=item['field'];time=rule['binding'].get('time_field')
            if time and field_spec(model,name).get('role')=='time' and name.split('.')[0]==time.split('.')[0]:name=time
            defaults[name]={'op':item['op'],'value':item['value']}
    # Resolve aliases to the same physical field: current explicit filters replace defaults.
    for name,value in (user_filters or {}).items():
        field=model.resolve_field(name)
        if not field: raise ValueError(f'语义层没有字段：{name}')
        canonical=f"{field['table']}.{field['name']}"
        defaults[canonical]=value
    return defaults


@contextmanager
def execution_scope():
    token=engine._semantic.set(True)
    try: yield
    finally: engine._semantic.reset(token)


def add_filter_specs(model, metrics, filters):
    explicit={(f['table'], f.get('field') or f['name']):f for f in filters}
    result=list(filters)
    for name,value in merge_filters(model,metrics,{}).items():
        resolved=field_spec(model,name)
        existing=explicit.get((resolved['table'],resolved['name']))
        if existing:
            # Current user overrides are resolved before planning by skipping the rule.
            # Arbitrary tool arguments cannot silently lift an adopted business scope.
            same_value=str(existing.get('value'))==str(value['value'])
            if existing.get('op','eq')!=value['op'] or not same_value:
                raise PermissionError('查询过滤与本次已采用业务口径冲突，请先由用户修订任务并核对规则，不能由工具自行改写')
        else:
            result.append({**resolved,'op':value['op'],'value':value['value']})
    return result


def time_specs(model,metrics,specs):
    """Apply the confirmed time basis to calendar grouping AND date predicates."""
    times={m.get('rule_binding',{}).get('time_field') for m in metrics}-{None}
    if not times:return specs
    if len(times)>1:raise ValueError('多个指标的时间口径不同，请分别查询')
    chosen=field_spec(model,next(iter(times)));result=[]
    for item in specs:
        value=dict(item)
        if value.get('table')==chosen['table']:
            if value.get('expression'):
                expr=value['expression']
                for name,col in model._tables[chosen['table']].get('columns',{}).items():
                    if col.get('role')=='time':expr=re.sub(r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])',chosen['name'],expr)
                value['expression']=expr
            elif value.get('role')=='time':
                value.update(name=chosen['name'],field=chosen['name'],label=chosen.get('label',chosen['name']))
        result.append(value)
    return result


def metric_expression(model,metric,alias,qualify):
    binding=metric.get('rule_binding',{})
    if binding.get('formula'):
        from rules.formula import compile_formula
        return compile_formula(model,binding,metric['table'],alias)
    return qualify(str(metric.get('expression','')),metric['table'],alias),[]
