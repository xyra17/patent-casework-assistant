"""Read-only department tools with task-source gates and persisted evidence."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Literal

from langchain_core.tools import tool
from api import context,evidence,source_policy
from casework import analytics,catalog,store,parser
from casework.privacy import ensure_model_allowed


def _guard(source):
    denied=source_policy.source_denial(source)
    if denied:return denied
    try:ensure_model_allowed()
    except PermissionError as exc:return '禁止执行：'+str(exc)
    if not context.consume_tool_budget(source,6 if source=='knowledge' else 12):
        source_policy.record_gap(source,'案件工具共享预算已耗尽，请基于已取结果说明缺口',reason='budget_exhausted')
        return '本轮该来源共享预算已耗尽，不能换工具绕过预算。'
    return None


def _json(value):
    return json.dumps(value,ensure_ascii=False,default=str,indent=2)


def _digest(value, operation):
    """Deterministic, complete fact units kept together when the context is compacted."""
    if operation=='archive':
        rows=[]
        for c in value['results']:
            # One body excerpt per case; full sources remain available in the original.
            source=next((p for p in c['sources'] if p['kind'] in ('application','disclosure')),c['sources'][0] if c['sources'] else {})
            rows.append({'case_id':c['case_id'],'title':c['title'],'document_facts':c['document_facts'],
                         'body':{k:source.get(k) for k in ('excerpt','evidence_id','file_id','location','context_expansion')},
                         'evidence_completeness':c.get('evidence_completeness'),
                         'related_drawings':c.get('related_drawings',[])})
        facts={'cases':rows,'has_more':value['has_more'],'scope':value['meaning'],
               'evidence_readback':value.get('evidence_readback')}
    elif operation=='monthly':
        facts={'answer_token':value.get('answer_token'),'domain_rows':value['rows'],
               'source_evidence_ids':value.get('evidence_ids',[]),
               'scope':{k:value.get(k) for k in ('start','end','client')},
               'delivery':'使用本次answer_token交付完整已登记统计与未知清单；正文由程序按该标记绑定的来源渲染，不拼写新哈希。完整answer_markdown和逐项证明保留在原工具结果中，需额外细节时按ref回读。'}
    elif operation=='recommend':
        load_only=value.get('recommendation_mode')=='workload_only'
        has_history=value.get('recommendation_mode','technical')=='technical'
        facts={'as_of':value.get('as_of'),'candidates':[],'evidence_id':value['evidence_id'],'deadline_definition':value['deadline_definition'],'scope':'按程序排序的首选候选；完整名单、明细与公式在原结果，人工确认才能分案。'}
        if value.get('recommendation_mode')=='workload_rank':facts['scope']='用户明确仅按已知负荷推荐；采用公开负荷规则排序，不判断技术适配。'
        if load_only:facts['scope']='用户明确选择只查看登记人员负荷；按姓名排列，不判断技术适配或推荐接案人。'
        for row in (value['candidates'] if load_only else value['candidates'][:3]):
            keys=('name','active_count','near_due_by_node','overdue_by_node','unknown_dates_by_node','basis')
            facts['candidates'].append({k:row[k] for k in ((*keys,'bound_domains','history_match_count') if has_history else keys)})
            facts['candidates'][-1]['unknown_stage_cases']=[c['case_id'] for c in row['unknown_stage_cases']]
            if has_history:facts['candidates'][-1]['history']=[{'case_id':h['case_id'],'sources':[
                {'excerpt':p['excerpt'],'evidence_id':p['evidence_id']} for p in h['sources'][:1]]} for h in row['history_matches']]
        if value.get('requires_user_confirmation'):
            facts={'requires_user_confirmation':True,'query':value['query'],
                   'scope':'技术经历依据尚未确认，必须停止接案推荐并请用户补充依据；不能自行按负荷给推荐。'}
    else:
        facts={k:v for k,v in value.items() if k not in ('coverage','context_digest')}
    value['context_digest']=facts
    return _json(value)



def _add(title,columns,rows,*,statistics=None,details=None,has_more=False,answer_contract=None,date_completeness=None):
    count=len(rows)
    item={'type':'database','title':title,'sql':'','columns':columns,'rows':rows[:500],
          'snippet':_json(details if details is not None else rows[:5])[:1600],
          'statistics':statistics or {'version':1,'metrics':[],'dimensions':[]},
          'completeness':{'version':1,'scope':'department_published_view','returned_rows':count,
                          'query_rows':None if has_more else count,'matched_rows':None,'has_more':has_more,
                          'model_rows':count,'evidence_rows':min(count,500),
                          'fetch_truncated':has_more,'model_truncated':False,'evidence_truncated':count>500},
          'casework':True,'coverage':store.coverage()}
    if isinstance(details, dict):
        item['casework_filters'] = {k:v for k,v in details.items() if k in (
            'owner','client','stage','domain','due_field','due_status','missing_date_field','as_of','start_month','end_month')
            and v is not None and v != ''}
    from casework.conversation_scope import current
    selected = current()
    if selected is not None:
        item['conversation_case_ids'] = sorted(selected)
        item['completeness']['scope'] = 'conversation_selected_cases'
        for metric in item['statistics'].get('metrics', []):
            metric.setdefault('filters', []).append({'field':'case_id','op':'in','value':sorted(selected)})
    if date_completeness is not None:item['date_completeness']=date_completeness
    if answer_contract is not None:
        item['answer_contract']=answer_contract
    return evidence.add(context.get_thread_id(),item)


def _metric_contract(metric,index,time,dimensions,filters=None,unit='件'):
    labels={'received_count':('实际收案数量',['实际接收数量','新收案件数量','收案数量','实际收案']),
            'filed_count':('实际交局数量',['实际完成数量','交局数量','已核实实际交局数量','已核实交局数量','已核实实际交局','实际交局']),
            'active_count':('在办案件数量',['在办案件数','在办案件','在办数量','登记在办量','在办量']),
            'near_count':('临近期限案件数量',['临近到期案件数量']),
            'overdue_count':('逾期案件数量',['逾期案件数'])}
    labels.update({'history_match_count':('相关历史案件数量',['相关历史案件数']), 'unknown_stage_count':('状态未知案件数量',['状态未知案件数'])})
    labels.update({'received_change_count':('实际收案数量变化',[]), 'filed_change_count':('实际交局数量变化',[])})
    labels.update({'received_change_pct':('实际收案数量变化百分比',['收案数量变化百分比','实际收案增减百分比','收案环比变化百分比']),
                   'filed_change_pct':('实际交局数量变化百分比',['交局数量变化百分比','实际交局增减百分比','交局环比变化百分比'])})
    labels.update({'received_total_count':('实际收案数量合计',['收案数量合计','实际收案合计','区间实际收案数量']),
                   'filed_total_count':('实际交局数量合计',['交局数量合计','实际交局合计','区间实际交局数量'])})
    labels.update({'unknown_received_count':('实际收案日期未核实',['实际收案日期未核实数量']),
                   'unknown_filed_count':('实际交局日期或核实依据未核实',['实际交局日期未核实','实际交局核实依据未核实'])})
    labels['missing_received_count']=('实际收案日期缺失数量',['实际收案日期缺失'])
    labels.update({'total_count':('案件总数',[])})
    labels['matched_case_count']=('筛选案件数量',['筛选案件数','筛选后案件数量','同一筛选范围案件数量','案件数量','清单案件数量'])
    for prefix,word in [('near','临近期限'),('overdue','逾期'),('unknown','期限未知')]:
        for field,label in [('draft','初稿'),('client','客户送审'),('filing','向局提交')]:
            labels[prefix+'_'+field+'_count']=(label+word+'案件数量',[])
    label,aliases=labels[metric]
    return {'column':metric,'index':index,'label':label,'names':[label,*aliases],
            'unit':unit,'definition_verified':True,'time':time,'filters':filters or []}


def _missing_received_cases(unknown_cases):
    """A conflicting date is unknown, but not an absent original value."""
    return [case['case_id'] for case in unknown_cases
            if 'received_date' in case and not case['received_date']
            and 'received_date' not in (case.get('conflict_fields') or [])]


def _monthly_event_rows(report):
    """Use the same snapshot's counted event identities and actual dates."""
    by_key = {case['case_key']: case for case in report['cases']}
    rows = []
    seen = set()
    for group in report['rows']:
        for key, field, label in [('received','received_date','实际收案'),
                                  ('filed','actual_filed_date','已核实实际交局')]:
            identities = group[key+'_case_keys']
            if len(identities) != group[key+'_count']:
                raise ValueError('月度事件数量与明细身份不一致')
            for ident in identities:
                case = by_key[ident]
                actual = case.get(field)
                if (not actual or actual[:7] != group['month']
                        or key == 'filed' and not case.get('filing_verified')):
                    raise ValueError('月度事件日期或交局核实依据不一致')
                identity = (group['month'], label, ident)
                if identity in seen:
                    raise ValueError('月度事件身份重复')
                seen.add(identity)
                rows.append([group['month'], label, case['case_id'], actual])
    return rows


def _meeting_provenance(records):
    rows=[]
    labels={'date':'会议日期','agents':'参会代理师','pending':'待补材料','iprs':'参会IPR','content':'会议内容','changes':'修改意见'}
    for record in records:
        for source in record.get('sources',[]):
            for field,label in labels.items():
                where=source.get('field_locations',{}).get(field)
                if not where:continue
                value=record.get(field)
                if isinstance(value,list):value='、'.join(value)
                rows.append([record['case_id'],label,value,source['path'],where])
    if not rows:return None
    return _add('会议字段原件位置（字段分别绑定，不用记录级行号代替）',
                ['所编号','字段','原件值','原件路径','原件位置'],rows)


def _query_contract(params):
    """The open Agent uses the same action/slot contract as the fixed workflow."""
    from casework.entities import bind_filters
    from casework.workflow import DepartmentRequest
    params=bind_filters(params)
    active={k:v for k,v in params.items() if v not in (None,'',False,[])}
    operation=params['operation']
    if active.get('recommendation_mode')=='technical':active.pop('recommendation_mode')
    if params.get('case_key') and operation not in ('meetings','recommend'):
        raise ValueError('case_key仅用于会议或分案；单案信息请用query保留案号')
    if operation=='workload_summary':
        if set(active)-{'operation','as_of','client','owner','domain','stage'}:raise ValueError('案量分组仅支持基准日、客户、负责人、技术域与阶段，不得忽略其他筛选')
    elif operation=='workload':
        if set(active)-{'operation','owner','as_of'}:
            raise ValueError('人员负荷仅支持完整owner和as_of，不接受其他筛选或排序')
        if not params.get('owner'):raise ValueError('人员负荷必须提供完整owner')
    elif operation=='meetings':
        from casework.parser import CASE_RE
        if params.get('query') and not CASE_RE.fullmatch(params['query'].strip()):
            raise ValueError('会议query仅接受完整案号；客户或IPR请使用明确筛选参数')
        DepartmentRequest(**{k:v for k,v in active.items() if k not in ('case_key','query')})
    elif operation=='topics':
        if set(active)-{'operation','start_month','end_month','client'}:
            raise ValueError('主题观察含未支持的筛选，不能忽略参数')
    else:
        DepartmentRequest(**{k:v for k,v in active.items() if k!='case_key'})
    return params


@tool
@store.consistent_read
def query_department_cases(operation:Literal['cases','meetings','monthly','topics','recommend','summary','workload','workload_summary'],query:str='',
                           owner:str|None=None,domain:str|None=None,stage:str|None=None,client:str|None=None,ipr:str|None=None,
                           due_field:Literal['draft_due_date','client_review_due_date','filing_due_date']|None=None,
                           due_status:Literal['overdue','next14','unknown']|None=None,as_of:str|None=None,
                           due_statuses:list[Literal['overdue','next14','unknown']]|None=None,
                           missing_date_field:Literal['received_date','actual_filed_date']|None=None,
                           start_month:str|None=None,end_month:str|None=None,case_key:str|None=None,
                           compare_months:bool=False,include_count:bool=False,complete:bool=False,
                           clients:list[str]|None=None,
                           recommendation_mode:Literal['technical','workload_rank']='technical',
                           offset:int=0,limit:int=30)->str:
    """查询共享盘发布的部门案件业务视图，只读，不写源文件或分案。

    cases：按案号/标题、代理师、技术域、阶段或明确期限节点查案件信息并分页。按单个案号查询时，另给计划日期与实际交局的原件位置及冲突状态证据；询问出处或冲突须引用该证据，不能仅凭汇总表推断。
    客户案件清单用cases(client=目录完整客户名)，不用通用SQL读取cw_cases。
    仅用户明确要求案件数量时include_count=true，返回可核验的同筛选计数表；否则total仅用于分页，不追加数量结论。
    实际收案日期未核实用missing_date_field=received_date；实际交局日期或依据未核实用actual_filed_date。
    missing_date_field覆盖全部办理阶段，不是计划期限due_status=unknown的在办筛选；两类条件不能替代。
    workload_summary：按承办人在办案量图表；传入用户要求的client、owner、domain、stage及as_of，在同一筛选快照中分组、降序并返回全部案号明细，实际交局排除、未知单列；不能去掉筛选换取全员结果。
    workload：用完整owner查询该代理师当前在办及三个期限负荷；不排序、不推荐，返回可直接引用的answer_markdown。
    summary：当前全量案件总数、在办、已核实实际交局与状态待核实数量，无分页且不接受额外过滤。
    同时查询临近与已过计划期限时due_statuses=["next14","overdue"]，不得只选其中一个；due_status与due_statuses互斥。
    meetings：按client与实际参会ipr查逐场三方记录，登记联系人不能当作参会人。
    monthly：必须提供YYYY-MM的start_month/end_month，返回程序计算的实际收案/交局；未知另列。
    需比较两月情况时compare_months=true，返回程序计算的相邻月份案量变化，不能由案量推断工时。
    topics：按收案月份观察正文技术标签，标签可重叠，不推断研发战略。
    recommend：基于有据历史经历和当前负荷给候选；需要数据库和知识库均获准，不会自动分案。
    as_of为明确基准日YYYY-MM-DD。所限=draft_due_date；客户期限=client_review_due_date；提交期限=filing_due_date。
    实际交局用actual_filed_date且filing_verified=true；不能用计划期限或文件mtime代替。
    """
    denial=_guard('db_query')
    if denial:return denial
    try:
        if not 0<=offset or not 1<=limit<=100:raise ValueError('分页参数不在允许范围')
        if include_count and operation!='cases':raise ValueError('include_count仅适用于案件清单计数')
        params=_query_contract(dict(operation=operation,query=query,owner=owner,domain=domain,stage=stage,
            client=client,clients=clients,ipr=ipr,due_field=due_field,due_status=due_status,due_statuses=due_statuses,
            as_of=as_of,start_month=start_month,end_month=end_month,compare_months=compare_months,case_key=case_key,recommendation_mode=recommendation_mode,missing_date_field=missing_date_field))
        owner,domain,client,clients,ipr=(params.get(k) for k in ('owner','domain','client','clients','ipr'))
        if operation=='workload':
            if offset or limit!=30:raise ValueError('人员负荷不分页，不接受offset/limit')
            result=analytics.owner_workload(owner,as_of=as_of)
            base=analytics.as_date(as_of)
            keys=['active_count','near_count','overdue_count','unknown_stage_count']+[
                prefix+'_'+short+'_count' for prefix in ('near','overdue','unknown') for short in ('draft','client','filing')]
            time={'status':'bounded','field':'as_of','start':base.isoformat(),'end':(base+timedelta(days=1)).isoformat()}
            contract={'version':1,'metrics':[_metric_contract(k,i+1,time,[]) for i,k in enumerate(keys)],
                      'dimensions':[{'column':'owner_name','index':0,'label':'代理师','role':'dimension'}]}
            columns=['代理师']+[m['label'] for m in contract['metrics']]
            delivery={'version':1,'kind':'workload',**{k:result[k] for k in
                      ('as_of','scope','active_definition','deadline_definition')}}
            delivery['excluded_case_ids']=result['duplicate_case_ids_excluded']
            delivery['complete_snapshot']=True
            delivery['owner_case_details']=[{'owner':owner,'active_count':result['active_count'],
                'unknown_stage_count':result['unknown_stage_count'],
                'active_case_ids':[c['case_id'] for c in result['active_cases']],
                'unknown_stage_case_ids':[c['case_id'] for c in result['unknown_stage_cases']]}]
            sid=_add('指定代理师基准日已知在办与期限负荷',columns,[[owner]+[result[k] for k in keys]],statistics=contract,
                     details={k:result[k] for k in ('owner','as_of','active_count','active_definition','deadline_definition','scope')},answer_contract=delivery)
            result['evidence_id']=sid
            from tools.answer_contracts import fact_block,fact_token
            item={'id':sid,'type':'database','columns':columns,'rows':[[owner]+[result[k] for k in keys]],
                  'statistics':contract,'answer_contract':delivery,'title':'指定代理师基准日已知在办与期限负荷',
                  'snippet':_json({k:result[k] for k in ('owner','as_of','active_count','active_definition','deadline_definition','scope')})[:1600]}
            result['answer_markdown']=fact_block(item)
            # Use the persisted entry so selectors include its actual identity.
            persisted=next((e for e in evidence.snapshot(context.get_thread_id()) if e['id']==sid),None)
            if persisted:result['answer_token']=fact_token(persisted)
        elif operation=='workload_summary':
            if offset or limit!=30:raise ValueError('全员负荷不分页，不接受offset/limit')
            from api import storage
            from tools.answer_contracts import workload_summary_date_issues
            run=storage.get_run(context.get_thread_id()) if context.get_thread_id() else None
            task=(run or {}).get('query','')
            date_issues=workload_summary_date_issues(task,[{'answer_contract':{'kind':'workload_summary','as_of':as_of or analytics.today().isoformat()}}])
            if date_issues:raise ValueError(date_issues[0])
            department_stage = stage
            result=analytics.workload_summary(as_of=as_of,client=client,owner=owner,domain=domain,stage=department_stage)
            base=analytics.as_date(result['as_of'])
            time={'status':'bounded','field':'as_of','start':base.isoformat(),'end':(base+timedelta(days=1)).isoformat()}
            filters=[{'field':k,'value':v} for k,v in result['filters'].items()]
            contract={'version':1,'metrics':[_metric_contract(k,i+1,time,[],filters) for i,k in enumerate(('active_count','unknown_stage_count'))],
                      'dimensions':[{'column':'owner_name','index':0,'label':'承办人','role':'dimension'}]}
            columns=['承办人']+[m['label'] for m in contract['metrics']]
            rows=[[r['owner'],r['active_count'],r['unknown_stage_count']] for r in result['owners']]
            delivery={'version':1,'kind':'workload_summary','as_of':result['as_of'],'scope':result['scope'],
                      'definition':result['active_definition'],'complete_snapshot':True,
                      'snapshot_proof':result['snapshot_proof'],
                      'owner_case_details':result['owners'],
                      'filters':result['filters'],'filter_choices':result['filter_choices'],'case_details':result['case_details']}
            from tools.answer_contracts import workload_summary_scope_issues
            scope_issues=workload_summary_scope_issues(task,{'answer_contract':delivery,'rows':rows})
            if scope_issues:raise ValueError(scope_issues[0])
            sid=_add('筛选范围承办人在办与状态未知案量' if result['filters'] else '全体承办人在办与状态未知案量',columns,rows,statistics=contract,details=result['owners'],answer_contract=delivery)
            result['evidence_id']=sid
            from tools.answer_contracts import fact_block,fact_token
            item=next(e for e in evidence.snapshot(context.get_thread_id()) if e['id']==sid)
            result.update(answer_markdown=fact_block(item),answer_token=fact_token(item))
            result['answer_guidance']='使用answer_token完整交付汇总表和全部承办人案号明细，不手抄或只列部分人；已含本次筛选范围案量需要的明细和未知清单，无需重复取数。只在用户另外指定其他逐案字段时另查相应字段。正文仅补图表入口及非数字结论。'
            result['chart_ready']=bool(rows)
        elif operation=='summary':
            base=analytics.today();cases,duplicates=analytics.unique_cases(store.list_cases())
            result={'as_of':base.isoformat(),'total_count':len(cases),
                    'active_count':sum(analytics.active(c,base) for c in cases),
                    'filed_count':sum(analytics.is_completed(c,base) for c in cases),
                    'unknown_stage_count':sum(not analytics.is_completed(c,base) and c.get('stage') not in analytics.ACTIVE|analytics.INACTIVE for c in cases),
                    'duplicate_case_ids_excluded':duplicates,'coverage':store.coverage()}
            keys=['total_count','active_count','filed_count','unknown_stage_count']
            time={'status':'bounded','field':'as_of','start':base.isoformat(),'end':(base+timedelta(days=1)).isoformat()}
            contract={'version':1,'metrics':[_metric_contract(k,i,time,[]) for i,k in enumerate(keys)],'dimensions':[]}
            result['evidence_id']=_add('当前完整发布快照的案件计数',[m['label'] for m in contract['metrics']],
                                      [[result[k] for k in keys]],statistics=contract)
        elif operation=='cases':
            # “在办” is a business scope over the department's typed stages,
            # while attachment tables may legitimately contain a literal stage
            # value. Keep this mapping local to the department catalog.
            department_stage = stage
            reader=analytics.complete_cases_page if complete else analytics.cases_page
            paging={} if complete else {'offset':offset,'limit':limit}
            result=reader(query=query,owner=owner,domain=domain,stage=department_stage,client=client,due_field=due_field,due_status=due_status,due_statuses=due_statuses,missing_date_field=missing_date_field,as_of=as_of,**paging)
            columns=['所编号','客户案号','发明名称','代理师','技术领域','案件状态','实际收案日期','初稿期限／所限','客户送审期限','向局提交期限','实际交局日期','交局已核实']
            keys=['case_id','client_case_id','title','owner_name','technical_domain','stage','received_date','draft_due_date','client_review_due_date','filing_due_date','actual_filed_date','filing_verified']
            # Client identity must also be present in the cited evidence, not
            # only in the private query row used by the renderer.
            columns.append('客户简称')
            keys.append('client_alias')
            case_columns=list(columns)
            due_contract = None
            if (due_field and due_status in ('overdue','next14','unknown') and not result['has_more']
                    and offset == 0 and len(result['rows']) == result['total']):
                due_contract={'version':1,'kind':'due_case_list','as_of':result['as_of'],
                              'due_field':due_field,'due_status':due_status,'total':result['total'],
                              'complete_snapshot':True,'group_proofs':result['due_group_proofs'],
                              'scope':'当前完整发布快照中已知在办案件；已核实交局及非在办阶段排除',
                              'unknown_definition':'所选期限字段为空或冲突（归一值为null）'}
            elif (due_field and due_statuses and offset==0 and not result['has_more']
                    and len(result['rows'])==result['total']):
                due_contract={'version':1,'kind':'due_case_groups','as_of':result['as_of'],
                              'due_field':due_field,'complete_snapshot':True,
                              'group_proofs':result['due_group_proofs']}
            sid=_add('部门案件信息（当前已同步数据）',columns,[[r.get(k) for k in keys] for r in result['rows']],
                     details=result['filters'],has_more=result['has_more'],answer_contract=due_contract)
            result['evidence_id']=sid
            if due_contract:
                from tools.answer_contracts import fact_block,fact_token
                bound=next((e for e in evidence.snapshot(context.get_thread_id())
                            if e.get('id')==sid and e.get('answer_contract')==due_contract),None)
                if bound:
                    result['answer_markdown']=fact_block(bound)
                    result['answer_token']=fact_token(bound)
            if include_count:
                # The filtered total and how many of those cases are still in
                # progress answer two different questions; both stay checkable.
                filters=[{'field':k,'value':v} for k,v in result['filters'].items() if v not in (None,'',[])]
                columns,values=['筛选案件数量'],[[result['total']]]
                metrics=[_metric_contract('matched_case_count',0,{'status':'unbounded'},[],filters)]
                if not result['has_more'] and len(result['rows'])==result['total']:
                    values[0].append(sum(analytics.active(r,analytics.as_date(as_of)) for r in result['rows']))
                    columns.append('在办案件数量')
                    metrics.append(_metric_contract('active_count',1,{'status':'as_of'},[],filters))
                result['count_evidence_id']=_add('同一筛选范围的案件总量',columns,values,details=result['filters'],
                statistics={'version':1,'metrics':metrics,'dimensions':[]})
            if set(due_statuses or [due_status]) & {'overdue','next14'}:
                missing=analytics.complete_cases_page(query=query,owner=owner,domain=domain,stage=department_stage,client=client,due_field=due_field,due_status='unknown',as_of=as_of)
                result['unknown_due_cases']=[r for r in missing['rows'] if analytics.active(r,analytics.as_date(as_of))]
                result['unknown_due_has_more']=missing['has_more']
                unknown_contract=None
                if not missing['has_more'] and len(result['unknown_due_cases'])==missing['total']:
                    unknown_contract={'version':1,'kind':'due_case_list','as_of':missing['as_of'],
                        'due_field':due_field,'due_status':'unknown','total':missing['total'],
                        'complete_snapshot':True,'group_proofs':missing['due_group_proofs'],
                        'scope':'当前完整发布快照中已知在办案件；已核实交局及非在办阶段排除',
                        'unknown_definition':'所选期限字段为空或冲突（归一值为null）'}
                result['unknown_due_evidence_id']=_add('所选期限未知或冲突的已知在办案件',case_columns,[[r.get(k) for k in keys] for r in result['unknown_due_cases']],
                    details=missing['filters'],has_more=missing['has_more'],answer_contract=unknown_contract)
                if unknown_contract:
                    from tools.answer_contracts import fact_block,fact_token
                    bound=next((e for e in evidence.snapshot(context.get_thread_id())
                                if e.get('id')==result['unknown_due_evidence_id']
                                and e.get('answer_contract')==unknown_contract),None)
                    if bound:
                        result['unknown_due_answer_markdown']=fact_block(bound)
                        result['unknown_due_answer_token']=fact_token(bound)
            for row in result['rows']:
                snapshot=store.get_case(row['case_key'])
                row['field_states']={k:f['state'] for k,f in snapshot['fields'].items() if f['state'] in ('conflict','confirmation_stale')}
                row['conflicting_candidates']={k:[{'value':c['value'],'path':c['path'],'location':c['location']} for c in snapshot['fields'][k]['candidates']] for k in snapshot['conflict_fields']}
                if row['conflicting_candidates']:
                    row['conflict_evidence_id']=_add('案件字段冲突候选 · '+row['case_id'],
                        ['所编号','字段','候选值','原件','定位'],
                        [[row['case_id'],parser.FIELDS[field][0],c['value'],c['path'],c['location']]
                         for field,candidates in row['conflicting_candidates'].items() for c in candidates])
                if parser.CASE_RE.fullmatch(query.strip()) and len(result['rows'])==1:
                    source_rows=[]
                    for field in ('owner_name','client_alias','title','stage','received_date','draft_due_date','client_review_due_date','filing_due_date','actual_filed_date','filing_confirmed'):
                        entry=snapshot['fields'].get(field) or {}
                        label=parser.FIELDS.get(field,(field,))[0]
                        for candidate in entry.get('candidates') or []:
                            source_rows.append([row['case_id'],label,str(candidate.get('value')),
                                                entry.get('state','unknown'),candidate.get('path'),candidate.get('location')])
                        if not entry.get('candidates'):
                            source_rows.append([row['case_id'],label,'未记录',entry.get('state','unknown'),'',''])
                    row['field_provenance_evidence_id']=_add('本案日期字段原件出处与冲突状态',
                        ['所编号','字段','原件值','核对状态','原件路径','原件位置'],source_rows,
                        details={'case_id':row['case_id'],'conflict_fields':snapshot.get('conflict_fields',[]),
                                 'field_states':{field:(snapshot['fields'].get(field) or {}).get('state','unknown')
                                                 for field in ('draft_due_date','client_review_due_date','filing_due_date','actual_filed_date','filing_confirmed')}})
                row['filing_verified']='已核实' if row['filing_verified'] else '未核实'
            if parser.CASE_RE.fullmatch(query.strip()) and len(result['rows'])==1:
                meetings=catalog.meetings(case_key=result['rows'][0]['case_key'])
                msid=_add('本案逐场会议与登记待补材料',
                    ['所编号','客户','会议日期','参会IPR','参会代理师','会议内容','待补材料','修改意见'],
                    [[r['case_id'],r['client'],r['date'],'、'.join(r['iprs']),'、'.join(r['agents']),r['content'],r['pending'],r['changes']] for r in meetings])
                result['case_meetings']={'rows':meetings,'has_more':False,'evidence_id':msid,
                                         'field_provenance_evidence_id':_meeting_provenance(meetings)}
            result['note']='total用于分页；仅include_count=true时返回数量证据，未要求数量时不要额外附加计数。未知和冲突保持为空，case_key供后续定位。'
            if include_count:
                scope='；'.join(str(v) for v in result['filters'].values() if v not in (None,'',[])) or '当前接入资料'
                cells=' | '.join(str(v) for v in values[0])
                heads=' | '.join(c+'（件）' for c in columns)
                result['count_answer_markdown']='筛选范围：'+scope+'。\n| '+heads+' | 来源 |\n|'+' --- |'*(len(columns)+1)+'\n| '+cells+f" | [{result['count_evidence_id']}] |"
        elif operation=='meetings':
            if client and clients:raise ValueError('单客户和多客户筛选不能并存')
            # A case number is a supported meeting anchor. Resolve it through
            # the published catalog instead of silently ignoring query.
            if query:
                from casework import store as case_store
                matches=[c for c in case_store.list_cases()
                         if c.get('case_id','').upper()==query.strip().upper()]
                if len(matches)==1:
                    if case_key and case_key!=matches[0]['case_key']:
                        raise ValueError('会议案号与case_key不一致')
                    case_key=matches[0]['case_key']
                elif len(matches)>1:
                    raise ValueError('案号对应多个案件记录，不能静默选择会议来源')
                else:
                    return _json({'rows': [], 'has_more': False, 'query': query, 'note': '发布目录未找到此案号，未扩大查询范围'})
            records=catalog.meetings(client=client,ipr=ipr,case_key=case_key)
            if clients:records=[r for r in records if r['client'] in clients]
            rows=records[offset:offset+limit]
            sid=_add('实际登记的逐场三方会议',['所编号','客户','会议日期','参会IPR','参会代理师','会议内容','待补材料','修改意见'],
                     [[r['case_id'],r['client'],r['date'],'、'.join(r['iprs']),'、'.join(r['agents']),r['content'],r['pending'],r['changes']] for r in rows],has_more=len(records)>offset+limit)
            result={'rows':rows,'has_more':len(records)>offset+limit,'evidence_id':sid,
                    'field_provenance_evidence_id':_meeting_provenance(rows),'coverage':store.coverage(),
                    'note':'同案多场、同场多人分别保留；不能用立案联系人或当前承办人替代实际参会人。客户不同的同名IPR不能合并个人偏好。'}
        elif operation=='monthly':
            if not start_month or not end_month:raise ValueError('月报须提供明确的起止月份')
            result=analytics.report(start_month,end_month,client=client,missing_date_field=missing_date_field)
            ids=[]
            # Each source fixes one metric and one absolute month; no model arithmetic.
            for total in result['totals']:
                start,end=analytics.month_bounds(total['month'])
                selected=[r for r in result['rows'] if r['month']==total['month']]
                for metric,field in [('received_count','received_date'),('filed_count','actual_filed_date')]:
                    label='实际收案数量' if metric=='received_count' else '实际交局数量'
                    rows=[[r['group'],r[metric]] for r in selected]
                    time={'status':'bounded','start':start.isoformat(),'end':end.isoformat(),'field':field}
                    filters=[{'field':'client_alias','value':client}] if client else []
                    contract={'version':1,'metrics':[_metric_contract(metric,1,time,[],filters)],
                              'dimensions':[{'column':'technical_domain','index':0,'label':'技术领域','role':'dimension'}]}
                    sid=_add(f"{start.year}年{start.month}月各技术领域{label}",['技术领域',label],rows,statistics=contract)
                    ids.append(sid)
                    total_contract={'version':1,'metrics':[_metric_contract(metric,0,time,[],filters)],'dimensions':[]}
                    total[label+'_evidence_id']=_add(f"{start.year}年{start.month}月{label}合计",[label],[[total[metric]]],statistics=total_contract)
            range_totals={}
            if len(result['totals'])>1:
                # A multi-month request is answered with a sum as well as per-month
                # values. The sum is program-owned so the answer never has to add up.
                range_start=analytics.month_bounds(result['totals'][0]['month'])[0]
                range_end=analytics.month_bounds(result['totals'][-1]['month'])[1]
                for metric,label,field,value in [
                        ('received_total_count','实际收案数量合计','received_date',sum(t['received_count'] for t in result['totals'])),
                        ('filed_total_count','实际交局数量合计','actual_filed_date',sum(t['filed_count'] for t in result['totals']))]:
                    time={'status':'bounded','start':range_start.isoformat(),'end':range_end.isoformat(),'field':field}
                    contract={'version':1,'metrics':[_metric_contract(metric,0,time,[],filters)],'dimensions':[]}
                    range_totals[metric]=(label,value,_add(start_month+'至'+end_month+label,[label],[[value]],statistics=contract))
            lines=[('统计范围：客户＝'+client+'；所有办理阶段；按实际发生日期归月。') if client
                   else '统计范围：当前接入的全部客户，所有办理阶段；按实际发生日期归月。',
                   '| 月份 | 实际收案数量（件） | 实际交局数量（件） |','| --- | --- | --- |']
            for total in result['totals']:
                year,month=total['month'].split('-')
                lines.append(f"| {year}年{int(month)}月 | {total['received_count']} [{total['实际收案数量_evidence_id']}] | {total['filed_count']} [{total['实际交局数量_evidence_id']}] |")
            lines.append('')
            if range_totals:
                lines.append('区间合计（'+range_start.isoformat()+'至'+range_end.isoformat()+'，不含末日）：'
                    +'，'.join(f"{label}{value}件 [{sid}]" for label,value,sid in range_totals.values())+'。')
                result['range_total_evidence_ids']={metric:sid for metric,(_,_,sid) in range_totals.items()}
            if result['unknown_received'] or result['unknown_filed']:
                lines.append('未核实日期不计入对应指标的月份；另一项实际日期已核实的，仍可计入其对应指标。')
            result['answer_markdown']='\n'.join(lines)
            result['evidence_ids']=ids
            event_rows = _monthly_event_rows(result)
            result['case_event_details'] = event_rows
            result['case_event_details_evidence_id'] = _add('逐月实际发生事件的案号与日期',
                ['月份','事件指标','所编号','实际日期'], event_rows,
                details={'client':client,'start_month':start_month,'end_month':end_month})
            from api import storage
            run = storage.get_run(context.get_thread_id()) if context.get_thread_id() else None
            task = (run or {}).get('query', '')
            # “给明细” is itself a delivery requirement.  Requiring the
            # query to also mention “日期” silently dropped received-case
            # details for monthly count questions such as “给明细”.
            # Emit the program-bound event table whenever the user asks for
            # case-level detail/listing; the table carries the exact event
            # date and source row, so the renderer remains source-bound.
            if re.search(r'案号|明细|清单', task):
                sid = result['case_event_details_evidence_id']
                result['answer_markdown'] += '\n\n| 月份 | 事件指标 | 所编号 | 实际日期 | 来源 |\n| --- | --- | --- | --- | --- |'
                for row in event_rows:
                    result['answer_markdown'] += '\n| ' + ' | '.join(str(value) for value in row) + f' | [{sid}] |'
            result.pop('cases',None)
            result['coverage']={k:result['coverage'][k] for k in ('complete_scan','scope_note','data_kind')}
            missing_received = _missing_received_cases(result['unknown_received'])
            result['unknown_received']=[{'case_id':c['case_id'],'reason':'实际收案日期缺失或冲突，月份未知'} for c in result['unknown_received']]
            result['unknown_filed']=[{'case_id':c['case_id'],'reason':'实际交局日期缺失、冲突或核实依据不足，月份未知（不等于已发生交局或办理异常）'} for c in result['unknown_filed']]
            result['unknown_evidence_id']=_add('实际发生日期未核实清单（不归入任一月份）',
                ['所编号','缺口'],[[c['case_id'],c['reason']] for key in ('unknown_received','unknown_filed') for c in result[key]])
            filters=[{'field':'client_alias','value':client}] if client else []
            def completeness(predicate,count):
                return {'version':1,'predicate':predicate,'stage_scope':'all',
                        'time_scope':'unassigned','filters':filters,
                        'population_count':result['population_count'],'matched_count':count,
                        'complete':not bool(result['duplicate_case_ids_excluded']),
                        'excluded_duplicate_ids':result['duplicate_case_ids_excluded'],
                        'scope':'当前接入案件，排除待核对的重复案号；未知日期不分配到月份'}
            result['unknown_count_evidence_id']=_add('实际发生日期未核实数量（当前接入范围、所有办理阶段、不归月）',
                ['实际收案日期未核实','实际交局日期或核实依据未核实'],
                [[len(result['unknown_received']),len(result['unknown_filed'])]],
                statistics={'version':1,'metrics':[
                    _metric_contract('unknown_received_count',0,{'status':'unbounded'},[],filters),
                    _metric_contract('unknown_filed_count',1,{'status':'unbounded'},[],filters)],'dimensions':[]})
            result['missing_received_case_ids'] = missing_received
            result['missing_received_count_evidence_id'] = _add(
                '实际收案日期缺失数量（不含日期冲突，所有办理阶段，不归月）',
                ['实际收案日期缺失数量'], [[len(missing_received)]],
                details={'client':client, 'case_ids':missing_received, 'excludes':'日期冲突'},
                statistics={'version':1,'dimensions':[],'metrics':[
                    _metric_contract('missing_received_count',0,{'status':'unbounded'},[],filters)]})
            if '收案日期缺失' in task:
                sid = result['missing_received_count_evidence_id']
                result['answer_markdown'] += '\n实际收案日期缺失数量'+str(len(missing_received))+'件（不含日期冲突，所有办理阶段，不归月）：'+('、'.join(missing_received) or '无符合记录')+f' [{sid}]。'
            result['date_completeness']={}
            for key,field,label in [('unknown_received','received_date','实际收案日期'),
                                    ('unknown_filed','actual_filed_date','实际交局日期或核实依据')]:
                rows=result[key]
                contract=completeness(key,len(rows))
                sid=_add(label+'未核实完整清单（所有办理阶段，不归月）',
                         ['所编号','缺口'],[[r['case_id'],r['reason']] for r in rows],
                         date_completeness=contract)
                result['date_completeness'][key]={**contract,'evidence_id':sid}
                result['answer_markdown']+=f"\n{label}未核实共计{len(rows)}件（当前接入范围、所有办理阶段、不归月）："+('、'.join(r['case_id'] for r in rows) or '无符合记录')+f" [{sid}][{result['unknown_count_evidence_id']}]。"
            result['answer_markdown']+='\n交局日期或依据未核实包含尚在办、尚未交局的正常记录，不直接等于日期异常、漏办或已发生交局。'
            if missing_date_field:
                label={'received_date':'实际收案日期','actual_filed_date':'实际交局日期或核实依据'}[missing_date_field]
                unknown=result['unknown_actual_dates']
                result['unknown_actual_evidence_id']=_add('各办理阶段实际日期未核实清单',['所编号','未核实字段'],
                    [[c['case_id'],label] for c in unknown],details={'client':client,'missing_date_field':missing_date_field})
            # Conflict candidates remain unassigned to a month, including plan dates.
            # Distinguish event vs plan fields; neither candidate repairs the canonical date.
            conflicts=[]
            for case in store.list_cases():
                if client and case.get('client_alias')!=client:continue
                if not case.get('conflict_fields'):continue
                snapshot=store.get_case(case['case_key'])
                for field in snapshot['conflict_fields']:
                    if field not in {'received_date','actual_filed_date',*analytics.DUE_FIELDS}:continue
                    for candidate in snapshot['fields'][field]['candidates']:
                        conflicts.append([case['case_id'],parser.FIELDS[field][0],
                            '实际发生日' if field in ('received_date','actual_filed_date') else '计划节点',
                            candidate['value'],candidate['path'],candidate['location']])
            result['date_conflicts']=conflicts
            result['date_conflict_evidence_id']=_add('日期冲突候选（当前接入范围，不归月）',
                ['所编号','字段','日期性质','候选值','原件','定位'],conflicts,
                date_completeness=completeness('date_conflicts',len(conflicts)))
            result['date_completeness']['date_conflicts']={**completeness('date_conflicts',len(conflicts)),'evidence_id':result['date_conflict_evidence_id']}
            if conflicts:
                result['answer_markdown']+='\n\n日期冲突记录：'
                result['answer_markdown']+='\n| 所编号 | 字段名称 | 日期性质 | 候选日期 | 原件名称 | 来源定位编号 |\n| --- | --- | --- | --- | --- | --- |'
                for ident,label,nature,value,path,location in conflicts:
                    cell=lambda v:str(v).replace('|','\\|').replace('\n',' ')
                    result['answer_markdown']+='\n| '+' | '.join(cell(v) for v in (ident,label,nature,value,path,location))+f" [{result['date_conflict_evidence_id']}] |"
            if not conflicts:
                scope_note=(f'客户＝{client}；' if client else '')+'当前接入范围、所有办理阶段、不归月'
                result['answer_markdown']+=f"\n日期冲突：{scope_note}未查到日期冲突候选。"+f"[{result['date_conflict_evidence_id']}]"
            chronology=result['chronology_conflicts']
            chrono_contract={**completeness('chronology_conflicts',len(chronology)),
                'definition':'仅比较收案及已核实交局日期均可确定的案件，检查实际交局早于实际收案；未知日期不能证明先后顺序正常'}
            chrono_sid=_add('实际日期先后核对：已核实交局早于收案',
                ['所编号','实际收案日期','实际交局日期'],
                [[c['case_id'],c['received_date'],c['actual_filed_date']] for c in chronology],
                date_completeness=chrono_contract)
            result['date_completeness']['chronology_conflicts']={**chrono_contract,'evidence_id':chrono_sid}
            result['answer_markdown']+='\n实际日期先后核对（仅比较两项日期均可确定的案件，未知者不能判定正常）：已核实交局早于收案的记录'+('：'+'、'.join(c['case_id'] for c in chronology) if chronology else '未查到')+f"。[{chrono_sid}]"
            result['comparisons']=[]
            if compare_months:
                if len(result['totals'])<2:raise ValueError('月份比较至少需要两个不同月份')
                lines=['','## 月份变化','| 月份比较及两月覆盖区间 | 实际收案数量变化（件） | 实际交局数量变化（件） |','| --- | --- | --- |']
                summaries=[]
                for previous,current in zip(result['totals'],result['totals'][1:]):
                    def change_pct(now,before):
                        # A zero base has no percentage. The absolute change stays
                        # available so the answer never divides by zero.
                        return None if not before else round((now-before)/before*100,1)
                    item={'previous_month':previous['month'],'month':current['month'],
                          'received_change_count':current['received_count']-previous['received_count'],
                          'filed_change_count':current['filed_count']-previous['filed_count'],
                          'received_change_pct':change_pct(current['received_count'],previous['received_count']),
                          'filed_change_pct':change_pct(current['filed_count'],previous['filed_count'])}
                    time={'status':'bounded','start':analytics.month_bounds(previous['month'])[0].isoformat(),
                          'end':analytics.month_bounds(current['month'])[1].isoformat(),'field':'month_comparison'}
                    comparison_filters=[{'field':'client_alias','value':client}] if client else []
                    metrics=[_metric_contract(m,i,time,[],comparison_filters) for i,m in enumerate(('received_change_count','filed_change_count'))]
                    item['evidence_id']=_add(current['month']+'相比'+previous['month']+'实际案量变化',
                        [m['label'] for m in metrics],[[item['received_change_count'],item['filed_change_count']]],
                        statistics={'version':1,'metrics':metrics,'dimensions':[]},
                        details={'calculation':'当月实际数量减前月实际数量','input_evidence_ids':[
                            previous['实际收案数量_evidence_id'],current['实际收案数量_evidence_id'],
                            previous['实际交局数量_evidence_id'],current['实际交局数量_evidence_id']]})
                    pct_columns=[];pct_row=[]
                    pct_metrics=[]
                    for metric in ('received_change_pct','filed_change_pct'):
                        if item[metric] is None:continue
                        pct_metrics.append(_metric_contract(metric,len(pct_row),time,[],comparison_filters,unit='%'))
                        pct_columns.append(pct_metrics[-1]['label']);pct_row.append(item[metric])
                    if pct_row:
                        item['pct_evidence_id']=_add(current['month']+'相比'+previous['month']+'实际案量变化百分比',
                            pct_columns,[pct_row],statistics={'version':1,'metrics':pct_metrics,'dimensions':[]},
                            details={'calculation':'(当月实际数量减前月实际数量)/前月实际数量×100，保留一位小数；前月为0时不计算百分比','input_evidence_ids':[item['evidence_id']]})
                    else:
                        item['pct_evidence_id']=None
                    result['comparisons'].append(item)
                    month_label=lambda value:value[:4]+'年'+str(int(value[5:]))+'月'
                    lines.append(f"| {month_label(current['month'])} 相比 {month_label(previous['month'])}；[{time['start']}，{time['end']}) | {item['received_change_count']} [{item['evidence_id']}] | {item['filed_change_count']} [{item['evidence_id']}] |")
                    change=lambda n:'减少' if n<0 else '增加' if n>0 else '持平'
                    summary=f"{month_label(current['month'])} 的实际收案数量{change(item['received_change_count'])}，实际交局数量{change(item['filed_change_count'])}。[{item['evidence_id']}]"
                    percentages=[]
                    for metric,label in (('received_change_pct','实际收案数量变化百分比'),('filed_change_pct','实际交局数量变化百分比')):
                        if item[metric] is None:
                            percentages.append(label+'：前月为0，不计算百分比（绝对变化见上）')
                        else:
                            percentages.append(f"{label}{item[metric]:g}% [{item['pct_evidence_id']}]")
                    summaries.append(summary)
                    # A percentage needs its own two-month scope so the program can
                    # verify the comparison without inference.
                    summaries.append(f"{month_label(current['month'])}相比{month_label(previous['month'])}："
                                     +'，'.join(percentages)+'。')
                lines.extend(['',*summaries])
                lines.append('变化仅反映当前已登记并核实的案量，不等同于实际工时、人员忙闲或办理效率。')
                result['answer_markdown']+='\n'+'\n'.join(lines)
            body=result['answer_markdown']
            sid=_add('月度业务统计完整交付',[],[],details={'input_evidence_ids':result['evidence_ids']},
                     answer_contract={'version':1,'kind':'monthly_report','body':body,
                                      'start_month':start_month,'end_month':end_month,'client':client})
            from tools.answer_contracts import fact_token
            persisted=next((e for e in evidence.snapshot(context.get_thread_id()) if e['id']==sid),None)
            if persisted:result['answer_token']=fact_token(persisted)
            result['answer_guidance']='用answer_token交付完整程序统计表及未知冲突清单，正文不重复改写数字；文件可保存answer_markdown。直接按totals给每月实际收案数量、实际交局数量，逐行引用对应*_evidence_id。unknown_*就是当前接入全部案件的待核实清单，不再扫全表、不需要查询布尔值。不要输出目录代号或解析文件数量等未问数字。'

        elif operation=='topics':
            if not start_month or not end_month:raise ValueError('主题观察须提供明确起止月份')
            result=analytics.topics(start_month,end_month,client)
            sid=_add('按实际收案月份观察正文技术主题',['月份','客户','技术标签','覆盖案件'],[[r['month'],r['client'],r['tag'],'、'.join(r['case_keys'])] for r in result['rows']])
            result['evidence_id']=sid
        else:
            from api import clarification
            load_only=clarification.current().get('choice')=='workload_only'
            from casework.workflow import _workload_rank_authorized
            if recommendation_mode=='workload_rank' and not _workload_rank_authorized.get():
                raise ValueError('仅按负荷排序必须来自用户原话已核验的部门执行契约，工具不能自行降级')
            load_rank=recommendation_mode=='workload_rank' and not load_only
            has_history=not (load_only or load_rank)
            if has_history:
                denied=_guard('knowledge')
                if denied:return denied+' 辅助分案需要获准的技术历史依据，不能自动扩大来源。'
            result=analytics.recommend(query,domain=domain,as_of=as_of,case_key=case_key,workload_rank=load_rank)
            if load_only:
                analytics.workload_view(result)
                result['basis_evidence_id']=_add('本次人员负荷范围（当前已启用登记人员，不代表全部门）',
                    ['代理师','展示范围','期限计数口径'],
                    [[r['name'],result['formula'],result['deadline_definition']] for r in result['candidates']])
            elif load_rank:
                result['basis_evidence_id']=_add('本次仅按负荷排序依据（不核对技术经历）',
                    ['代理师','排序分','排序规则','期限计数口径'],
                    [[r['name'],r['score'],result['formula'],result['deadline_definition']] for r in result['candidates']])
            else:
                result['basis_evidence_id']=_add('本次候选排序依据（当前已启用登记人员，不代表全部门）',
                    ['代理师','绑定领域','排序分','排序规则','期限计数口径'],
                    [[r['name'],'、'.join(r['bound_domains']),r['score'],result['formula'],result['deadline_definition']] for r in result['candidates']])
            base=analytics.as_date(as_of)
            # All exposed counts have program-owned names and source cells.
            metric_names=['active_count','near_count','overdue_count']+(['history_match_count'] if has_history else [])+['unknown_stage_count']
            metrics=[(metric,i+1) for i,metric in enumerate(metric_names)]
            nodes=[('draft','draft_due_date'),('client','client_review_due_date'),('filing','filing_due_date')]
            next_index=len(metrics)+1
            metrics.extend((prefix+'_'+short+'_count',next_index+i) for i,(prefix,short) in enumerate(
                [(prefix,short) for prefix in ('near','overdue','unknown') for short,_ in nodes]))
            time={'status':'bounded','field':'as_of','start':base.isoformat(),'end':(base+timedelta(days=1)).isoformat()}
            contract={'version':1,'metrics':[_metric_contract(m,i,time,[]) for m,i in metrics],
                      'dimensions':[{'column':'owner_name','index':0,'label':'代理师','role':'dimension'}]}
            columns=['代理师']+[m['label'] for m in contract['metrics']]
            rows=[]
            for r in result['candidates']:
                row=[r['name'],r['active_count'],len(r['near_due_cases']),len(r['overdue_cases'])]+([r['history_match_count']] if has_history else [])+[len(r['unknown_stage_cases'])]
                for key in ('near_due_by_node','overdue_by_node','unknown_dates_by_node'):row.extend(r[key].get(field,0) for _,field in nodes)
                rows.append(row)
            sid=_add('基准日已知在办案件与期限负荷' if not has_history else '基准日已知在办案件、技术历史与期限负荷',columns,rows,statistics=contract)
            result['evidence_id']=sid
            result['source_usage']=['db_query','knowledge'] if has_history else ['db_query']
            for row in result['candidates']:
                if has_history:row['history_evidence_id']=_add('历史案件承办与完成状态 · '+row['name'],
                    ['所编号','代理师','案件状态','实际交局日期','交局已核实'],
                    [[h['case_id'],row['name'],store.get_case(h['case_key']).get('stage'),
                      store.get_case(h['case_key']).get('actual_filed_date'),store.get_case(h['case_key']).get('filing_verified')]
                     for h in row['history_matches']])
                row['load_evidence_id']=_add('当前在办案件与三个期限 · '+row['name'],
                    ['所编号','代理师','案件状态','初稿期限／所限','客户送审期限','向局提交期限'],
                    [[c.get(k) for k in ('case_id','owner_name','stage','draft_due_date','client_review_due_date','filing_due_date')] for c in row['active_cases']])
                row.pop('score',None)
                for history in (row['history_matches'] if has_history else []):
                    for proof in history['sources']:proof['evidence_id']=_proof(proof,history)
            if has_history:result['formula']='完整排序公式在案件工作台可查；回答重点是有据经历、已知负荷、未知字段和人工确认。'
            if has_history and analytics.recommendation_needs_confirmation(result):
                result['requires_user_confirmation']=True
                clarification.record_recommendation_gap(query,domain,as_of)
        return _digest(result,operation)
    except (ValueError,KeyError) as exc:
        source_policy.record_gap('db_query',str(exc),reason='query_invalid')
        return '查询未完成：'+str(exc)


def _proof(proof,case):
    return evidence.add(context.get_thread_id(),{'type':'knowledge','title':case.get('title') or case['case_id'],
        'source':proof.get('source',proof.get('path','')),'chunk_id':proof.get('chunk_id'),'revision':proof['revision'],
        'location':proof['location'],'snippet':proof.get('excerpt',''),'matched_terms':proof.get('matched_terms',[]),
        'casework':True,'file_id':proof['file_id'],'parser_key':proof['parser_key'],
        'context_expansion':proof.get('context_expansion'), 'spans':proof.get('spans'),
        'read_scope':proof.get('read_scope'),
        'retrieved_excerpt':proof.get('retrieved_excerpt'), 'retrieved_location':proof.get('retrieved_location'),
        'case_id':case['case_id'],'owner_name':case.get('owner_name'),'current_snapshot':proof.get('current',True)})


@tool
@store.consistent_read
def search_case_archive(query:str,limit:int=6,domain:str|None=None,owner:str|None=None,kind:Literal['disclosure','application','intake','receipt','drawing']|None=None,completed_only:bool=False,allow_vector:bool=True,query_scope:Literal['ambiguous','mention','topic','mechanism']='ambiguous')->str:
    """在获准共享盘已发布的交底、申请文件等正文中找相似机制，并按所编号归并。

    文件名没有关键词也可由正文命中。返回本地向量/BM25渠道、案件、代理师、原文和定位。
    结果是历史参考，不能自动当作本案事实；后续用read_case_archive_document核对正文。
    completed_only只保留原文件字段标记已交局/已完成/已交付的检索候选，不采用平台人工覆盖的状态。
    kind=drawing检索Visio图中文字；给出所编号时也能返回无正文的绘图附件。
    related_drawings仅按所编号关联，不证明图形、连线或结构关系。
    query_scope按用户原任务口径设置：mention提到即可（含背景和否定），topic核心主题，mechanism方案采用；仅说相关用ambiguous。
    返回召回候选，不能将背景提及作为采用证明；模糊浏览按关联程度区分，计数/经历/推荐用途口径不明时须先确认。
    """
    denial=_guard('knowledge')
    if denial:return denial
    try:
        from casework.search import search
        # Archive-only filters must use source-document facts, never platform
        # overrides whose authority belongs to db_query.
        result=search(query,limit=30 if completed_only or domain or owner else limit,kind=kind,allow_vector=allow_vector,query_scope=query_scope)
        accepted=[]
        for case in result['results']:
            from casework.conversation_scope import allows
            if not allows(case['case_id']):
                continue
            # Only source-document claims establish archive metadata. Platform overrides
            # belong to db_query and must not leak through a knowledge-only task.
            snapshot=store.get_case(case['case_key']);facts={}
            metadata_sources={};field_files={}
            for field in ('title','technical_domain','owner_name','stage','actual_filed_date','filing_confirmed','notes'):
                candidates=snapshot['fields'].get(field,{}).get('candidates',[])
                values={store.dumps(c['value']) for c in candidates}
                facts[field]=candidates[0]['value'] if len(values)==1 else None
                field_files[field]=[c['file_id'] for c in candidates[:6]]
                for candidate in candidates[:6]:
                    proof=metadata_sources.setdefault(candidate['file_id'],{**candidate,'parts':[],'locations':[]})
                    proof['parts'].append(parser.FIELDS[field][0]+'：'+str(candidate['value']))
                    proof['locations'].append(candidate['location'])
            if completed_only and facts['stage'] not in analytics.COMPLETED:continue
            if owner and facts['owner_name']!=owner:continue
            from casework.domains import canonical
            if domain and canonical(facts['technical_domain'])!=canonical(domain):continue
            for field in ('title','technical_domain','owner_name','stage'):case[field]=facts[field]
            fact_ids=[];source_ids={}
            for proof in metadata_sources.values():
                proof['excerpt']='；'.join(proof.pop('parts'));proof['location']='；'.join(dict.fromkeys(proof.pop('locations')))
                sid=_proof(proof,case);fact_ids.append(sid);source_ids[proof['file_id']]=sid
            facts['evidence_ids']=fact_ids
            facts['field_evidence_ids']={f:list(dict.fromkeys(source_ids[k] for k in files if k in source_ids)) for f,files in field_files.items()}
            case['document_facts']=facts
            case['owner_name']=facts['owner_name'];case['stage']=facts['stage']
            case.pop('client',None)
            # A high-scoring negative/ablation passage alone can hide the actual
            # method. Include one leading chunk from each already-hit technical
            # document, with the same published version and source bindings.
            technical_files={p['file_id']:p for p in case['sources'] if p['kind'] in ('application','disclosure')}
            for fid,proof in technical_files.items():
                item=catalog.document(fid,proof['revision'],proof['parser_key'])
                chunks=item['payload']['chunks']
                if chunks and not any(p['file_id']==fid and chunks[0]['text'] in p['excerpt'] for p in case['sources']):
                    leading={k:v for k,v in proof.items() if k not in (
                        'context_expansion','retrieved_excerpt','retrieved_location','chunk_id','spans')}
                    leading.update(excerpt=chunks[0]['text'],location=chunks[0]['location'],spans=chunks[0].get('spans',[]),
                                   chunk_id=store.digest([fid,proof['revision'],proof['parser_key'],0]))
                    case['sources'].append(leading)
            for proof in case['sources']:
                proof['evidence_id']=_proof(proof,case)
                proof.pop('score',None)
                proof.pop('chunk_id',None)
            for drawing in case.get('related_drawings',[]):
                drawing['location']='文件登记（按所编号关联）'
                drawing['excerpt']=f"绘图附件：{drawing['source']}；{drawing['association']}；{drawing['scope']}"
                drawing['evidence_id']=_proof(drawing,case)
            accepted.append(case)
        result['has_more']=result['has_more'] or len(accepted)>limit
        result['results']=accepted[:limit]
        # Open Agent callers share the deterministic critical-gap handoff.
        from casework.archive_readback import complete
        from casework.read_cache import invoke
        complete(result, query, lambda args: invoke(read_case_archive_document, args))
        result['completion_filter']='按原件记载完成状态筛选当前检索候选，不代表全部历史案件已被穷尽' if completed_only else '未限制案件状态'
        result['answer_guidance']='document_facts来自已解析原文件字段，附原件定位及证据；可以在模拟资料范围引用，不要求正文署名。平台人工覆盖不在此返回。可见片段足以支持局部引用，无需逐份全文通读；未全读不能断言全文不存在。'
        result['answer_guidance']+=' 查询口径：'+query_scope+'；仅提及、方案中采用、核心主题须区分，召回数量不是符合口径的全库案件数。'
        result['answer_guidance']+=' 片段可能按需补入相邻原文；context_expansion记录扩展方向、层数、覆盖缺口和停止原因，retrieved_excerpt保留原始命中。completeness仅为本地规则信号，不是语义核验；未出现条件或例外不能证明全文没有。未补全项及同版本回读参数见unresolved_sources和follow_up，仍有缺口时继续读取原文件或使用结构化读取。'
        if not result['results']:source_policy.record_gap('knowledge','已接入共享盘中未找到支持所问技术机制的材料',reason='no_hits')
        return _digest(result,'archive')
    except ValueError as exc:return '检索未完成：'+str(exc)


@tool
def read_case_archive_document(file_id:str,revision:str|None=None,parser_key:str|None=None,offset:int=0,limit:int=8)->str:
    """按检索返回的file_id读共享盘解析版本，分页返回原文及版本状态；不接收任意磁盘路径。

    offset从0开始，每次1–12块。has_more=true表示尚未读完发布文本；历史版本只用于历史核对。
    """
    denial=_guard('knowledge')
    if denial:return denial
    try:
        if offset<0 or not 1<=limit<=12:raise ValueError('分页参数超限')
        item=catalog.document(file_id,revision,parser_key);chunks=item['payload']['chunks'];selected=chunks[offset:offset+limit]
        from casework.conversation_scope import allows
        if not allows(item['case_id']):
            raise ValueError('该原文不属于本轮已确认的案件范围')
        case={'case_id':item['case_id'],'title':item['relative_path']}
        for chunk in selected:
            chunk['evidence_id']=_proof({'file_id':file_id,'revision':item['revision'],'parser_key':item['parser_key'],
                         'path':item['relative_path'],'location':chunk['location'],'excerpt':chunk['text'],'current':item['current'],
                         'read_scope':{'offset':offset,'returned':len(selected),'total_chunks':len(chunks),
                                       'has_more':len(chunks)>offset+len(selected),'current':item['current']}},case)
        return _json({'file_id':file_id,'source':item['relative_path'],'revision':item['revision'],'parser_key':item['parser_key'],
                      'current':item['current'],'version_note':item['version_note'],'chunks':selected,
                      'warnings':item['payload'].get('warnings',[]),'version_status':item['version_status'],
                      'offset':offset,'next_offset':offset+len(selected),'has_more':len(chunks)>offset+limit,
                      'complete_published_text':offset==0 and len(selected)==len(chunks),
                      'scope':'只证明当前页解析文本已读，不证明扫描PDF、所有原件或整个共享盘均已覆盖'})
    except (KeyError,ValueError) as exc:return '原文读取未完成：'+str(exc)
    except PermissionError:
        source_policy.record_gap('knowledge','原文读取被来源权限拒绝',reason='source_denied')
        return '禁止执行：原文读取被来源权限拒绝；证据不足，不能据此确认所问条件。'
    except TimeoutError:
        source_policy.record_gap('knowledge','原文读取超时，所问条件仍待核实',reason='tool_timeout')
        return '原文读取超时：证据不足，不能据此确认所问条件。'
    except (OSError,RuntimeError):
        source_policy.record_gap('knowledge','原文结构或存储读取失败',reason='read_failed')
        return '原文读取未完成：结构或存储读取失败；证据不足，不能据此确认所问条件。'
