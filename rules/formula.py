"""Bounded expression tree -> parameterized SQL; no stored/user SQL fragments."""
from rules.semantic import field_spec


def numeric(model,name):
    spec=field_spec(model,name)
    if spec.get('role')!='metric' and spec.get('type') not in ('number','integer','float','decimal'):
        raise ValueError(f'公式需要数值字段：{name}')
    return spec


def related_edge(model,base,child):
    pk=model._tables[base].get('primary_key')
    for rel in model._config.get('relationships',[]):
        if rel.get('type')=='many_to_one' and rel.get('from','').split('.')[0]==child and rel.get('to')==f'{base}.{pk}':
            field_spec(model,rel['from']);field_spec(model,rel['to'])
            return rel
    raise ValueError('关联汇总需要明细表到基础表主键的明确 many_to_one 关系；禁止猜测关联或直接连接明细')


def compile_formula(model,binding,base,alias='t0'):
    params=[];count=0;field_count=0
    aggregate=binding.get('formula_aggregation','sum').upper()
    if aggregate not in ('SUM','AVG','MIN','MAX'):raise ValueError('无效的公式聚合')
    mode=binding.get('formula_mode','row')
    if mode not in ('row','aggregate'):raise ValueError('无效的公式计算顺序')
    def grouped(expr):return f'{aggregate}({expr})' if mode=='aggregate' else expr
    def build(node,depth=0):
        nonlocal count,field_count
        count+=1
        if count>40 or depth>6:raise ValueError('计算公式最多40个节点、6层')
        op=node['op'];args=node.get('args',[])
        if op in ('add','sub','mul','div'):
            if len(args)!=2 or node.get('field') or node.get('value') is not None or node.get('filters'):raise ValueError('四则运算需要且仅需要两个操作数')
            a,b=[build(n,depth+1) for n in args]
            return f'({a} / NULLIF({b}, 0))' if op=='div' else "("+a+" "+{'add':'+','sub':'-','mul':'*'}[op]+" "+b+")"
        if args:raise ValueError('字段/数值/关联汇总不能包含子公式')
        if op=='number':
            import math
            value=node.get('value')
            if value is None or not math.isfinite(value) or abs(value)>1e12 or node.get('field') or node.get('filters'):raise ValueError('公式常量必须为有限数值，绝对值不超过一万亿')
            params.append(value);return '%s'
        if node.get('value') is not None:raise ValueError('字段不能携带常量')
        spec=numeric(model,node.get('field') or '')
        field_count+=1
        if op=='field':
            if node.get('filters') or spec['table']!=base:raise ValueError('普通公式字段必须属于基础表；明细字段请选择关联汇总')
            return grouped(f"{alias}.`{spec['name']}`")
        if op!='related_sum':raise ValueError('不支持的公式操作')
        edge=related_edge(model,base,spec['table']);child=spec['table'];child_alias=f'rs{count}'
        parts=[f"{child_alias}.`{edge['from'].split('.')[1]}` = {alias}.`{edge['to'].split('.')[1]}`"]
        # Reuse the same typed filter validator, constrained strictly to the child table.
        for item in node.get('filters',[]):
            f=field_spec(model,item['field'])
            if f['table']!=child:raise ValueError('关联汇总过滤只能使用该明细表字段')
            from rules.semantic import validate_filters
            validate_filters(model,child,[item])
            expr=f"{child_alias}.`{f['name']}`"
            if item['op']=='in':
                parts.append(f"{expr} IN ({', '.join(['%s']*len(item['value']))})");params.extend(item['value'])
            else:
                operator={'eq':'=','ne':'<>','gt':'>','gte':'>=','lt':'<','lte':'<=','like':'LIKE'}[item['op']]
                parts.append(f'{expr} {operator} %s');params.append(item['value'])
        return grouped(f"COALESCE((SELECT SUM({child_alias}.`{spec['name']}`) FROM `{child}` {child_alias} WHERE {' AND '.join(parts)}), 0)")
    expression=build(binding['formula'])
    if not field_count:raise ValueError('公式至少需要一个真实字段')
    return (f'{aggregate}({expression})' if mode=='row' else expression),params


def guard_join(model,start,target):
    """An adopted metric may only join towards a unique parent, never fan out."""
    from tools.semantic_query import SemanticQueryEngine
    current=start
    for edge in SemanticQueryEngine(model)._path(start,target):
        other=edge['b'] if current==edge['a'] else edge['a']
        relations=model._config.get('relationships',[])
        if not any(r.get('type') in ('many_to_one','one_to_one') and r.get('from')==f"{current}.{edge['a_col'] if current==edge['a'] else edge['b_col']}" and r.get('to')==f"{other}.{edge['b_col'] if current==edge['a'] else edge['a_col']}" and r['to'].split('.')[1]==model._tables[other].get('primary_key') for r in relations):
            raise ValueError('该维度或过滤会展开基础记录，存在重复累计风险；请使用关联汇总或先维护唯一关系')
        current=other
