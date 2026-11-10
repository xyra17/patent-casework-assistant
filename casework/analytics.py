"""Program-computed business reports and explainable, human-confirmed assignment."""
from __future__ import annotations

import json
import re
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from casework import catalog,parser,store
from casework.domains import canonical as canonical_domain

COMPLETED={'已交局','已完成','已交付'}
INACTIVE=COMPLETED|{'暂停','已暂停','撤回','已撤回','终止'}
ACTIVE={'待撰写','撰写中','初稿撰写','技术交底','客户审核','自检中','待补充','等待补充','待客户确认','送审中','待交局','修改中'}
DUE_FIELDS={'draft_due_date','client_review_due_date','filing_due_date'}


def today():return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def as_date(value=None):
    return date.fromisoformat(value) if value else today()


def is_completed(case,as_of):
    return bool(case.get('filing_verified') and case.get('actual_filed_date') and case['actual_filed_date']<=as_of.isoformat())


def active(case,as_of):
    if is_completed(case,as_of):return False
    if case.get('stage') in INACTIVE:return False
    return case.get('stage') in ACTIVE


def unique_cases(cases):
    counts=Counter(c['case_id'] for c in cases)
    duplicates=sorted(key for key,n in counts.items() if n>1)
    return [c for c in cases if c['case_id'] not in duplicates],duplicates


def summary_case(case):
    return {k:case.get(k) for k in ('case_key','case_id','client_case_id','title','client_alias','owner_name','technical_domain','stage',
                                    'received_date','draft_due_date','client_review_due_date','filing_due_date',
                                    'actual_filed_date','filing_verified','conflict_fields','version')}


def actual_date_unknown(case, field):
    if field not in ('received_date','actual_filed_date'):raise ValueError('实际日期未知筛选字段无效')
    return (not case.get(field) or field in (case.get('conflict_fields') or [])
            or (field=='actual_filed_date' and not case.get('filing_verified')))


def stage_matches(case, stage, base):
    """Department semantic scope, never used for attachment literal filtering."""
    return active(case, base) if stage == '在办' else not stage or case.get('stage') == stage


@store.consistent_read
def cases_page(*,query='',owner=None,domain=None,stage=None,client=None,due_field=None,due_status=None,due_statuses=None,missing_date_field=None,as_of=None,offset=0,limit=50):
    if not 0<=offset or not 1<=limit<=200:raise ValueError('分页参数无效')
    base=as_date(as_of);items=store.list_cases()
    if due_field and due_field not in DUE_FIELDS:raise ValueError('请明确初稿、客户送审或向局提交期限')
    if due_status not in (None,'overdue','next14','unknown'):raise ValueError('未知期限条件')
    if due_status and due_statuses:raise ValueError('单个与多个期限条件不能同时指定')
    statuses=set(due_statuses or ([due_status] if due_status else []))
    if not statuses<={'overdue','next14','unknown'}:raise ValueError('未知期限条件')
    if missing_date_field not in (None,'received_date','actual_filed_date'):raise ValueError('实际日期未知筛选字段无效')
    filtered=[]
    for case in items:
        if parser.CASE_RE.fullmatch(query.strip()):
            if case['case_id'].upper()!=query.strip().upper():continue
        elif query and query.lower() not in store.dumps({k:case.get(k) for k in ('case_id','title','client_alias','client_case_id','owner_name','technical_domain')}).lower():continue
        if owner and case.get('owner_name')!=owner:continue
        if domain and canonical_domain(case.get('technical_domain'))!=canonical_domain(domain):continue
        if client and case.get('client_alias')!=client:continue
        if not stage_matches(case,stage,base):continue
        if missing_date_field and not actual_date_unknown(case,missing_date_field):continue
        if statuses:
            if not due_field:raise ValueError('期限筛选必须指定日期字段')
            value=case.get(due_field)
            matched = active(case,base) and (('unknown' in statuses and value is None) or (value and (
                ('overdue' in statuses and value<base.isoformat()) or
                ('next14' in statuses and base.isoformat()<=value<(base+timedelta(days=14)).isoformat()))))
            if not matched:continue
        filtered.append(case)
    from casework.requirements import due_group
    group_proofs = {status: {'count':sum(due_group(c.get(due_field),base.isoformat()) == status for c in filtered),
                    'complete':True, 'as_of':base.isoformat(), 'due_field':due_field}
                    for status in statuses}
    return {'due_group_proofs':group_proofs,'rows':[summary_case(c) for c in filtered[offset:offset+limit]],'total':len(filtered),'offset':offset,'limit':limit,
            'has_more':len(filtered)>offset+limit,'as_of':base.isoformat(),'coverage':store.coverage(),
            'filters':{'query':query,'owner':owner,'domain':domain,'stage':stage,'client':client,'due_field':due_field,'due_status':due_status,'due_statuses':due_statuses,'missing_date_field':missing_date_field}}


@store.consistent_read
def complete_cases_page(*,max_rows=500,**params):
    """Drain pages in one snapshot; a bounded prefix never claims completeness."""
    result=cases_page(**{**params,'offset':0,'limit':min(200,max_rows)})
    rows=list(result['rows']);pages=1
    while result['total']>len(rows) and len(rows)<max_rows:
        page=cases_page(**{**params,'offset':len(rows),'limit':min(200,max_rows-len(rows))})
        if not page['rows']:break
        rows.extend(page['rows']);pages+=1
    counts=Counter(row['case_id'] for row in rows)
    return {**result,'rows':rows,'has_more':len(rows)<result['total'],
            'pages_read':pages,'duplicate_case_ids':sorted(cid for cid,n in counts.items() if n>1)}


def month_bounds(month):
    if not re.fullmatch(r'\d{4}-\d{2}',month):raise ValueError('月份格式应为YYYY-MM')
    start=date.fromisoformat(month+'-01')
    end=date(start.year+(start.month==12),start.month%12+1,1)
    return start,end


@store.consistent_read
def report(start_month,end_month,*,group_by='technical_domain',client=None,missing_date_field=None):
    if missing_date_field not in (None,'received_date','actual_filed_date'):raise ValueError('实际日期未知筛选字段无效')
    start,_=month_bounds(start_month);last,end=month_bounds(end_month)
    if group_by not in ('technical_domain','owner_name','client_alias'):raise ValueError('不支持的分组字段')
    if start>last or (last.year-start.year)*12+last.month-start.month>35:raise ValueError('报表起止范围须为1–36个月')
    all_cases=store.list_cases();all_cases=[c for c in all_cases if not client or c.get('client_alias')==client]
    cases,duplicates=unique_cases(all_cases)
    months=[];current=start
    while current<end:
        months.append(current.strftime('%Y-%m'));current=month_bounds(current.strftime('%Y-%m'))[1]
    grouped={};drilldown={}
    def add(case,metric,value):
        month=value[:7];group=str((canonical_domain(case.get(group_by)) if group_by=='technical_domain' else case.get(group_by)) or '未登记')
        key=(month,group)
        row=grouped.setdefault(key,{'month':month,'group':group,'received_count':0,'filed_count':0,'received_case_keys':[],'filed_case_keys':[]})
        row[metric+'_count']+=1;row[metric+'_case_keys'].append(case['case_key'])
        drilldown[case['case_key']]=summary_case(case)
    unknown_received=[];unknown_filed=[];future_filed=[]
    for case in cases:
        received=case.get('received_date')
        if not actual_date_unknown(case,'received_date') and start.isoformat()<=received<end.isoformat():add(case,'received',received)
        elif actual_date_unknown(case,'received_date'):unknown_received.append(summary_case(case))
        filed=case.get('actual_filed_date')
        if not actual_date_unknown(case,'actual_filed_date'):
            if filed>today().isoformat():future_filed.append(summary_case(case))
            elif start.isoformat()<=filed<end.isoformat():add(case,'filed',filed)
        elif actual_date_unknown(case,'actual_filed_date'):
            unknown_filed.append(summary_case(case))
    rows=sorted(grouped.values(),key=lambda r:(r['month'],r['group']))
    totals=[{'month':month,'received_count':sum(r['received_count'] for r in rows if r['month']==month),
             'filed_count':sum(r['filed_count'] for r in rows if r['month']==month)} for month in months]
    return {'start':start.isoformat(),'end':end.isoformat(),'group_by':group_by,'rows':rows,'totals':totals,
            'missing_date_field':missing_date_field,
            'unknown_actual_dates':[summary_case(c) for c in cases if missing_date_field and actual_date_unknown(c,missing_date_field)],
            'cases':list(drilldown.values()),'unknown_received':unknown_received,'unknown_filed':unknown_filed,'future_filed':future_filed,
            'chronology_conflicts':[summary_case(c) for c in cases if not actual_date_unknown(c,'received_date')
                and not actual_date_unknown(c,'actual_filed_date') and c['actual_filed_date']<c['received_date']],
            'population_count':len(cases),'duplicate_case_ids_excluded':duplicates,'coverage':store.coverage(),
            'definitions':{'received_count':'实际收案日期在[月初,下月月初)的去重案件数量',
                           'filed_count':'已核实实际交局日期在[月初,下月月初)的去重案件数量；计划日期与目录更新时间不参与',
                           'unknown':'未知或冲突不进入月份分配；跨目录重复案号在核对前排除，避免重复计数'}}


@store.consistent_read
def topics(start_month,end_month,client=None):
    start,_=month_bounds(start_month);_,end=month_bounds(end_month)
    if start>=end or (end.year-start.year)*12+end.month-start.month>36:raise ValueError('主题观察范围须为1–36个月')
    cases,duplicates=unique_cases(store.list_cases());groups=defaultdict(list);proofs=defaultdict(list)
    for case in cases:
        if client and case.get('client_alias')!=client:continue
        received=case.get('received_date')
        if not received or not start.isoformat()<=received<end.isoformat():continue
        for tag in case['tags']:
            key=(received[:7],case.get('client_alias') or '未登记客户',tag['tag'])
            groups[key].append(case['case_key']);proofs[key].extend(tag['sources'][:2])
    return {'rows':[{'month':m,'client':c,'tag':t,'case_count':len(set(keys)),'case_keys':sorted(set(keys)),'sources':proofs[(m,c,t)][:8]}
                    for (m,c,t),keys in sorted(groups.items())],
            'duplicate_case_ids_excluded':duplicates,'start':start.isoformat(),'end':end.isoformat(),'coverage':store.coverage(),
            'meaning':'按实际收案月份观察已接入正文的技术主题；标签可重叠，主题计数不能相加为案件总数，也不等于客户整体研发战略。'}


def list_people():
    with store.db() as conn:
        return [{**dict(row),'domains':list(dict.fromkeys(canonical_domain(v) for v in json.loads(row['domains'])))} for row in conn.execute('SELECT * FROM people ORDER BY name,id')]


def save_person(name,domains,*,ident=None,expected_version=None,enabled=True,notes=''):
    if not name.strip() or not isinstance(domains,list) or len(domains)>20:raise ValueError('请填写姓名与领域列表')
    domains=list(dict.fromkeys(canonical_domain(v)[:80] for v in domains if str(v).strip()))
    with store.db(write=True) as conn:
        if ident:
            old=conn.execute('SELECT * FROM people WHERE id=?',(ident,)).fetchone()
            if not old:raise KeyError('代理师不存在')
            if old['version']!=expected_version:raise ValueError('代理师配置已变化，请刷新')
            if old['name']!=name:raise ValueError('姓名是历史记录关联字段，改名请先核对历史别名；本页支持修改领域及启停')
            conn.execute('UPDATE people SET domains=?,enabled=?,notes=?,version=version+1 WHERE id=?',(store.dumps(domains),int(enabled),notes[:1000],ident))
            before=dict(old)
        else:
            ident=uuid4().hex[:16];before=None
            conn.execute('INSERT INTO people(id,name,domains,enabled,notes) VALUES(?,?,?,?,?)',(ident,name.strip()[:80],store.dumps(domains),int(enabled),notes[:1000]))
        store.audit(conn,ident,'person_update' if before else 'person_create',before,{'name':name,'domains':domains,'enabled':enabled})
    return next(p for p in list_people() if p['id']==ident)


@store.consistent_read
def owner_workload(owner, *, as_of=None):
    """Exact-owner workload, independent of technical ranking and registration."""
    if not owner or not owner.strip():raise ValueError('人员负荷查询必须提供完整代理师姓名')
    base=as_date(as_of)
    cases,duplicates=unique_cases(store.list_cases())
    owned=[c for c in cases if c.get('owner_name')==owner]
    if not owned:raise ValueError('当前发布案件信息中未核实该代理师，不能把无匹配当作零负荷')
    current=[c for c in owned if active(c,base)]
    unknown=[c for c in owned if not is_completed(c,base) and c.get('stage') not in ACTIVE|INACTIVE]
    result={'owner':owner,'as_of':base.isoformat(),'active_count':len(current),
            'unknown_stage_count':len(unknown),'duplicate_case_ids_excluded':duplicates,
            'active_cases':[summary_case(c) for c in current],
            'unknown_stage_cases':[summary_case(c) for c in unknown],
            'scope':'仅当前发布的案件信息；不含未接入案件、实际工时、会议和个人日程，不承诺空闲。',
            'active_definition':'仅明确登记为在办阶段且尚无基准日前已核实实际交局的案件计入在办；已核实实际交局、已完成、暂停、撤回、终止均不计入在办。历史案件总数与当前在办负荷是不同指标，不构成数字冲突。',
            'deadline_definition':f'临近期限区间[{base.isoformat()}，{(base+timedelta(days=14)).isoformat()})；逾期为期限早于基准日。仅统计已知在办案件，三个计划节点分别统计，同一总项按案件去重。'}
    result['near_count']=sum(any(c.get(f) and base.isoformat()<=c[f]<(base+timedelta(days=14)).isoformat() for f in DUE_FIELDS) for c in current)
    result['overdue_count']=sum(any(c.get(f) and c[f]<base.isoformat() for f in DUE_FIELDS) for c in current)
    for short,field in [('draft','draft_due_date'),('client','client_review_due_date'),('filing','filing_due_date')]:
        result['near_'+short+'_count']=sum(bool(c.get(field) and base.isoformat()<=c[field]<(base+timedelta(days=14)).isoformat()) for c in current)
        result['overdue_'+short+'_count']=sum(bool(c.get(field) and c[field]<base.isoformat()) for c in current)
        result['unknown_'+short+'_count']=sum(not bool(c.get(field)) for c in current)
    return result


@store.consistent_read
def workload_summary(*, as_of=None, client=None, owner=None, domain=None, stage=None):
    """Group the same filtered snapshot used for identities and active counts."""
    base=as_date(as_of)
    cases,duplicates=unique_cases(store.list_cases())
    filter_choices={key:sorted({c[field] for c in cases if c.get(field)}) for key,field in
                    [('client','client_alias'),('owner','owner_name'),('domain','technical_domain')]}
    filters={key:value for key,value in dict(client=client,owner=owner,domain=domain,stage=stage).items() if value}
    cases=[c for c in cases if (not client or c.get('client_alias')==client)
           and (not owner or c.get('owner_name')==owner)
           and (not domain or canonical_domain(c.get('technical_domain'))==canonical_domain(domain))
           and stage_matches(c,stage,base)]
    groups={}
    for c in cases:
        owner=c.get('owner_name') or '未登记承办人'
        group=groups.setdefault(owner,{'owner':owner,'active_count':0,'unknown_stage_count':0,
                                      'active_case_ids':[],'unknown_stage_case_ids':[]})
        if active(c,base):
            group['active_count']+=1;group['active_case_ids'].append(c['case_id'])
        elif not is_completed(c,base) and c.get('stage') not in ACTIVE|INACTIVE:
            group['unknown_stage_count']+=1;group['unknown_stage_case_ids'].append(c['case_id'])
    scope='当前完整发布的案件信息'+('；筛选：'+ '、'.join(k+'='+v for k,v in filters.items()) if filters else '')+'，按承办人分组；同案号重复记录排除。登记负荷不代表可用工时。'
    return {'as_of':base.isoformat(),'owners':sorted(groups.values(),key=lambda r:(-r['active_count'],r['owner'])),
            'filters':filters,'filter_choices':filter_choices,'case_details':[summary_case(c) for c in cases],
            'duplicate_case_ids_excluded':duplicates,'coverage':store.coverage(),
            'snapshot_proof':{'complete':True,'unique_case_count':len(cases),'observed_owner_count':len(groups),
                              'duplicate_case_ids_excluded':duplicates,
                              'scope':'对一致性读取的当前发布快照逐案分类，完整覆盖所有已登记承办人；不是分页预览。'},
            'scope':scope,
            'active_definition':'明确在办且未在基准日前核实实际交局；已完成、暂停、撤回、终止均不计入在办，状态未知单列。'}


@store.consistent_read
def recommend(query,*,domain=None,as_of=None,case_key=None,workload_rank=False):
    if not query.strip():raise ValueError('请输入交底标题或技术描述')
    base=as_date(as_of);all_cases=store.list_cases();cases,duplicates=unique_cases(all_cases)
    if case_key and not any(c['case_key']==case_key for c in cases):
        raise ValueError('待分案案件不存在、目录已停用或案号重复，请先在案件信息中核对')
    query_tags=set(parser.query_tags(query))
    result=[]
    for person in list_people():
        if not person['enabled']:continue
        owned=[c for c in cases if c.get('owner_name')==person['name'] and c['case_key']!=case_key]
        current=[c for c in owned if active(c,base)]
        unknown_stage=[c for c in owned if not is_completed(c,base) and c.get('stage') not in ACTIVE|INACTIVE]
        # A colleague can have relevant work in progress. Preserve its actual
        # stage in the source proof; never present it as a completed matter.
        history=owned
        matches=[]
        for case in ([] if workload_rank else history):
            # Recheck the actual quoted text, including catalogs built by an
            # older parser. A stored label or similarity score is insufficient.
            sources=[];matched=set()
            from casework.archive_selection import core_excerpt
            for tag in case['tags']:
                if tag['tag'] not in query_tags:continue
                proof=next((s for s in tag['sources'] if tag['tag'] in parser.tags(core_excerpt(s.get('excerpt','')))),None)
                if proof:
                    matched.add(tag['tag'])
                    if proof not in sources:sources.append(proof)
            if '缓存隔离' in query_tags and '缓存隔离' not in matched:
                from casework import catalog
                for file in catalog.documents(case['case_key']):
                    if file.get('kind') not in ('application','disclosure') or file.get('state')!='parsed':continue
                    document=catalog.document(file['id'])
                    for chunk in document['payload'].get('chunks',[]):
                        if chunk.get('retrieval_role')!='focus':continue
                        excerpt=chunk.get('text','')
                        if not re.search(r'隔离不同会话缓存|生成隔离缓存键|缓存身份.{0,12}(?:校验|验证)',excerpt):continue
                        if '缓存隔离' not in parser.tags(excerpt):continue
                        matched.add('缓存隔离')
                        sources.append({'file_id':file['id'],'revision':file['revision'],
                            'parser_key':file['parser_key'],'path':file['relative_path'],
                            'location':chunk['location'],'excerpt':excerpt})
                        break
                    if '缓存隔离' in matched:break
            if matched:
                matches.append({'case_key':case['case_key'],'case_id':case['case_id'],'title':case.get('title'),'matched_tags':sorted(matched),'sources':sources[:3]})
        near=Counter();overdue=Counter();unknown_due=Counter()
        for case in current:
            for field in sorted(DUE_FIELDS):
                value=case.get(field)
                if not value:unknown_due[field]+=1
                elif value<base.isoformat():overdue[field]+=1
                elif value<(base+timedelta(days=14)).isoformat():near[field]+=1
        domain_match=bool(not workload_rank and domain and canonical_domain(domain) in {canonical_domain(v) for v in person['domains']})
        near_cases=[c for c in current if any(c.get(f) and base.isoformat()<=c[f]<(base+timedelta(days=14)).isoformat() for f in DUE_FIELDS)]
        overdue_cases=[c for c in current if any(c.get(f) and c[f]<base.isoformat() for f in DUE_FIELDS)]
        score=min(len(matches),3)*15+(15 if domain_match else 0)-len(current)*2-len(near_cases)*3-len(overdue_cases)*4-min(len(unknown_stage),5)*2
        result.append({'person_id':person['id'],'name':person['name'],'bound_domains':person['domains'],'domain_match':domain_match,
                       'history_matches':matches[:8],'history_match_count':len(matches),'active_count':len(current),
                       'near_due_cases':[summary_case(c) for c in near_cases],'overdue_cases':[summary_case(c) for c in overdue_cases],
                       'near_due_by_node':dict(near),'overdue_by_node':dict(overdue),'unknown_dates_by_node':dict(unknown_due),
                       'unknown_stage_cases':[summary_case(c) for c in unknown_stage],'active_cases':[summary_case(c) for c in current],
                       'score':score,'basis':('仅按已知在办与三个期限负荷排序' if workload_rank else '已登记承办的技术材料与当前负荷（在办不代表已完成）' if matches else '领域绑定与当前案件量兜底')})
    # A technical recommendation must surface verified mechanism matches before
    # zero-load fallbacks; the score still ranks people within each group.
    result.sort(key=lambda r:(not bool(r['history_matches']) if not workload_rank else False,
                              -r['score'],r['active_count'],r['name']))
    return {'recommendation_mode':'workload_rank' if workload_rank else 'technical','query':query,'domain':canonical_domain(domain),'as_of':base.isoformat(),'query_tags':sorted(query_tags),'candidates':result,
            'technical_intent_unresolved':not workload_rank and not bool(query_tags),
            'case_key':case_key,'duplicate_case_ids_excluded':duplicates,'coverage':store.coverage(),
            'deadline_definition':f'临近期限区间为[{base.isoformat()}，{(base+timedelta(days=14)).isoformat()})，含基准日、不含结束日；逾期为期限早于基准日。任一节点满足即计入对应案件数，同案多个节点在该项中不重复计数；同案可同时有逾期节点和临近节点。状态未知不算已知在办，期限未知不推算临近或逾期，分别披露。',
            'formula':('负荷分＝－在办案件×2－14天内到期案件×3－逾期案件×4－状态未知惩罚（最多10）；分数高者优先，同分按在办量、姓名排列。' if workload_rank else '有原文核实技术匹配者优先，组内按分数排序：每件相关承办案件15分（最多45）＋绑定领域匹配15－在办案件×2－14天内到期案件×3－逾期案件×4－状态未知惩罚（最多10）'),
            'limitations':['分数仅为公开排序规则，不表示专业能力或预计工时；由Boss确认，工具不会自动分案。',
                           '历史承办标签表示接触过相应技术；人数、期限和状态只对当前发布的已知记录成立。',
                           '三个期限分别统计；同一案件涉及多个节点时，负荷案件数只计一次。']}


def recommendation_needs_confirmation(result):
    return result.get('recommendation_mode') not in ('workload_only','workload_rank') and bool(result.get('technical_intent_unresolved') or not any(c['history_matches'] for c in result['candidates']))


def workload_view(result):
    result['recommendation_mode']='workload_only'
    result['candidates'].sort(key=lambda p:p['name'])
    for person in result['candidates']:
        for field in ('score','domain_match','history_matches','history_match_count'):
            person.pop(field,None)
        person['basis']='仅展示已登记在办与期限负荷，不判断技术适配'
    result['formula']='已按你的选择仅查看负荷，按姓名排列，不提供接案排序。'
    result['limitations']=['人员范围仅含当前启用登记人员，不代表全部门负荷。',
                           '阶段或日期缺失分别披露，不能据此认定人员空闲。',
                           '三个期限分别统计；同一案件涉及多个节点时，负荷案件数只计一次。']
    result['requires_user_confirmation']=False
    return result


def assign(case_key,person_id,expected_version,reason,*,confirmed=False,actor='本机操作'):
    if not confirmed:raise ValueError('分案必须由人工明确确认')
    with store.db(write=True) as conn:
        person=conn.execute('SELECT * FROM people WHERE id=? AND enabled=1',(person_id,)).fetchone()
        if not person:raise ValueError('候选代理师不存在或已停用')
        before=catalog.confirm_in_transaction(conn,case_key,{'owner_name':person['name']},expected_version,reason,actor=actor)
        store.audit(conn,case_key,'assignment_confirmed',{'owner_name':before.get('owner_name')},{'owner_name':person['name'],'person_id':person_id},actor,reason)
    from casework.mirror import sync_mirror
    sync_mirror(case_key.split(':',1)[0])
    result=store.get_case(case_key)
    return {'case':result,'shared_drive_written':False,'message':'平台分案记录已保存；未回写共享盘，请按既有流程更新源表。源字段改变后会提示重新核对。'}
