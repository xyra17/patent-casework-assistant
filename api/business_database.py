"""Business-table browsing using the existing read-only connection, without write endpoints."""
import re
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from tools.mysql_tools import describe_schema,_get_connection,plain_value
from tools.semantic_layer import semantic_layer
from api.business_edit import configured,serial

router=APIRouter(prefix='/api/business-database',tags=['business-database'])
_IDENT=re.compile(r'[A-Za-z0-9_]{1,64}\Z')


@router.get('')
def catalog():
    try:
        schema=describe_schema()
        return {'tables':[{'name':name,'label':semantic_layer._tables.get(name,{}).get('label',name),'columns':[{**c,'semantic_configured':c['name'] in semantic_layer._config.get('tables',{}).get(name,{}).get('columns',{}),'label':semantic_layer._tables.get(name,{}).get('columns',{}).get(c['name'],{}).get('label',c['name'])} for c in cols],
                           'primary_key':[c['name'] for c in cols if c.get('key')=='PRI']}
                          for name,cols in schema.items()],
                'capabilities':{'browse':True,'append':'import','create':configured(),'update':configured(),'delete':configured(),'audit':configured()}}
    except Exception as exc:raise HTTPException(503,'业务数据库暂时无法连接，请检查服务端连接配置') from exc


class TableFilter(BaseModel):
    field: str=Field(min_length=1,max_length=64)
    op: Literal['eq','contains','gt','gte','lt','lte']='eq'
    value: str=Field(max_length=500)


class BrowseRequest(BaseModel):
    table: str=Field(min_length=1,max_length=64)
    page: int=Field(default=1,ge=1,le=10000,strict=True)
    page_size: int=Field(default=50,ge=1,le=100,strict=True)
    filters: list[TableFilter]=Field(default_factory=list,max_length=8)


@router.post('/rows')
def browse(body: BrowseRequest):
    if not _IDENT.fullmatch(body.table) or any(not _IDENT.fullmatch(f.field) for f in body.filters):raise HTTPException(422,'非法表名或字段名')
    if (body.page-1)*body.page_size>200000:raise HTTPException(422,'翻页范围过大，请先筛选数据')
    conn=None
    try:
        conn=_get_connection();cursor=conn.cursor()
        cursor.execute('SHOW TABLES');tables={r[0] for r in cursor.fetchall()}
        if body.table not in tables:raise HTTPException(404,'当前业务库不存在该表')
        cursor.execute(f'DESCRIBE `{body.table}`');schema=cursor.fetchall()
        fields=[r[0] for r in schema]
        if any(f.field not in fields for f in body.filters):raise HTTPException(422,'筛选字段不属于所选业务表')
        order=[r[0] for r in schema if r[3]=='PRI'] or fields[:1]
        where=[];params=[]
        for f in body.filters:
            op={'eq':'=','contains':'LIKE','gt':'>','gte':'>=','lt':'<','lte':'<='}[f.op]
            where.append(f'`{f.field}` {op} %s')
            value=f.value
            if f.op=='contains':value='%'+value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            params.append(value)
        sql=f'SELECT * FROM `{body.table}`'+(' WHERE '+' AND '.join(where) if where else '')
        sql+=' ORDER BY '+', '.join('`'+c.replace('`','``')+'`' for c in order)+' LIMIT %s OFFSET %s'
        cursor.execute(sql,tuple(params+[body.page_size+1,(body.page-1)*body.page_size]))
        rows=cursor.fetchall()
        return {'table':body.table,'columns':[d[0] for d in cursor.description],
                'rows':[[plain_value(v) for v in row] for row in rows[:body.page_size]],
                'page':body.page,'page_size':body.page_size,'has_more':len(rows)>body.page_size,
                'has_primary_key':any(r[3]=='PRI' for r in schema),
                'row_keys':[{name:serial(row[fields.index(name)]) for name in fields if any(c[0]==name and c[3]=='PRI' for c in schema)} for row in rows[:body.page_size]]}
    except HTTPException:raise
    except Exception as exc:raise HTTPException(503,'读取业务数据失败，请检查连接或筛选值类型') from exc
    finally:
        if conn:conn.close()


class MetricOptionsRequest(BaseModel):
    metrics: list[str] = Field(default_factory=list, max_length=20)


def validate_metric_dimensions(metrics, dimensions, schema=None):
    """Read-only validation shared by option discovery and the UI query endpoint."""
    from tools.semantic_query import engine
    from rules.formula import guard_join
    from rules.semantic import metric_for
    if not metrics: raise ValueError('请先选择指标')
    for name in metrics:
        metric=metric_for(semantic_layer,name)
        if not metric: raise ValueError('未知指标：'+name)
        table=metric['table']
        if schema is not None and table not in schema: raise ValueError('指标来源表已不存在：'+table)
        if schema is not None:
            fields={c['name'] for c in schema[table]}
            configured=semantic_layer._tables.get(table,{}).get('columns',{})
            used=set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*',metric.get('expression',''))) & set(configured)
            if used-fields: raise ValueError('指标字段在实际数据库中不存在：'+'、'.join(sorted(used-fields)))
        for name in dimensions:
            field=semantic_layer.resolve_field(name)
            if not field: raise ValueError('未配置或有歧义的维度：'+name)
            if field.get('role') not in ('dimension','time','geo','hierarchy'): raise ValueError('该字段不能用作分组维度')
            if schema is not None and field['name'] not in {c['name'] for c in schema.get(field['table'],[])}: raise ValueError('维度字段在实际数据库中不存在')
            guard_join(semantic_layer,table,field['table'])
            if schema is not None:
                current=table
                for edge in engine._path(table,field['table']):
                    other=edge['b'] if current==edge['a'] else edge['a']
                    column=edge['b_col'] if current==edge['a'] else edge['a_col']
                    if [c['name'] for c in schema.get(other,[]) if c.get('key')=='PRI']!=[column]:
                        raise ValueError('关联目标的实际主键与语义关系不一致，暂不提供该维度')
                    current=other
    return engine.plan_multi(metrics,dimensions,{},limit=1)


@router.post('/metric-options')
def metric_options(body: MetricOptionsRequest):
    try: schema=describe_schema()
    except Exception as exc: raise HTTPException(503,'无法核对实际数据库结构，暂不提供指标分组选项') from exc
    semantic_layer.snapshot()
    options=[]
    for table,cfg in semantic_layer._tables.items():
        for name,col in cfg.get('columns',{}).items():
            if col.get('role') not in ('dimension','time','geo','hierarchy'):continue
            value=table+'.'+name
            try:validate_metric_dimensions(body.metrics,[value],schema);reason=''
            except ValueError as exc:reason=str(exc)
            options.append({'value':value,'label':col.get('label',name)+' · '+table,'table':table,'compatible':not reason,'reason':reason})
    try:plan=validate_metric_dimensions(body.metrics,[],schema);error=''
    except ValueError as exc:plan={};error=str(exc)
    return {'dimensions':options,'tables':list(dict.fromkeys(m['table'] for m in plan.get('metrics',[]))),'error':error}
