"""Bounded read-only workflows for structured department tasks; no model arithmetic."""
from __future__ import annotations
import asyncio,json,re
from typing import Literal
from contextvars import ContextVar
from pydantic import BaseModel,ConfigDict,Field,model_validator,create_model
from casework.requirements import TaskRequirement, TaskConstraint, TechnicalConcept, selected_due_statuses, plan_requirement_steps
from casework.archive_intent import ArchiveIntent, bind_intent, require_scope, SCOPE_LABELS, RELATION_LABELS

# Host execution authority; a tool argument alone cannot downgrade a general Agent.
_workload_rank_authorized=ContextVar('department_workload_rank_authorized',default=False)

class DepartmentRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation:Literal['archive','cases','case_brief','monthly','meetings','recommend','summary']
    case_field:Literal['owner_name','client_alias','title','stage','received_date','draft_due_date','client_review_due_date','filing_due_date','actual_filed_date','filing_verified']|None=None
    case_fields:list[Literal['owner_name','client_alias','title','stage','received_date','draft_due_date','client_review_due_date','filing_due_date','actual_filed_date','filing_verified']]=Field(default_factory=list,max_length=10)
    meeting_scope:Literal['all','latest']='all'
    query:str=Field(default='',max_length=4000)
    retrieval_query:str=Field(default='',max_length=4000)
    owner:str|None=None
    stage:str|None=None
    due_field:Literal['draft_due_date','client_review_due_date','filing_due_date']|None=None
    due_status:Literal['overdue','next14','unknown']|None=None
    missing_date_field:Literal['received_date','actual_filed_date']|None=None
    due_statuses:list[Literal['overdue','next14','unknown']]|None=Field(default=None,min_length=1,max_length=3)
    start_month:str|None=None
    end_month:str|None=None
    client:str|None=None
    clients:list[str]|None=Field(default=None,max_length=20)
    ipr:str|None=None
    domain:str|None=None
    as_of:str|None=None
    completed_only:bool=False
    archive_kind:Literal['drawing']|None=None
    archive_intent:ArchiveIntent=Field(default_factory=ArchiveIntent)
    filing_month:str|None=None
    result_limit:int|None=Field(default=None,ge=1,le=30)
    compare_months:bool=False
    requirements:list[TaskRequirement]=Field(default_factory=list,max_length=20)
    technical_concepts:list[TechnicalConcept]=Field(default_factory=list,max_length=12)
    normalization_events:list[dict]=Field(default_factory=list,max_length=40)
    constraints:list[TaskConstraint]=Field(default_factory=list,max_length=20)
    recommendation_mode:Literal['technical','workload_only','workload_rank']='technical'

    @model_validator(mode='after')
    def supported_parameters(self):
        if self.case_field and self.case_fields:raise ValueError('单字段和多字段参数不能同时指定')
        if len(set(self.case_fields))!=len(self.case_fields):raise ValueError('查询字段不能重复')
        common={'operation','requirements','constraints','normalization_events','retrieval_query'}
        fields={
            'summary':set(),
            'archive':{'query','completed_only','result_limit','technical_concepts','archive_kind','archive_intent'},
            'cases':{'query','case_field','case_fields','owner','stage','domain','client','due_field','due_status','due_statuses','missing_date_field','as_of','filing_month'},
            'case_brief':{'query','as_of','meeting_scope'},
            'monthly':{'start_month','end_month','client','compare_months','missing_date_field'},
            'meetings':{'client','clients','ipr'},
            'recommend':{'query','domain','as_of','technical_concepts','recommendation_mode','archive_intent'},
        }[self.operation]|common
        unsupported=[key for key,value in self.model_dump(exclude_defaults=True).items() if value not in (None,'',False,[]) and key not in fields]
        if unsupported:raise ValueError(self.operation+'不支持参数：'+','.join(unsupported)+'。不得添加用户未要求的筛选；真实额外条件须保留为other，不能忽略。')
        if self.operation=='monthly':
            from casework.analytics import month_bounds
            if not self.start_month or not self.end_month:raise ValueError('月报需明确月份范围')
            month_bounds(self.start_month);month_bounds(self.end_month)
        if self.due_status and self.due_statuses:raise ValueError('单个与多个期限条件不能同时指定')
        if selected_due_statuses(self) and not self.due_field:raise ValueError('期限筛选须明确节点')
        if self.due_statuses and len(self.due_statuses)<2:raise ValueError('单个期限条件请使用due_status')
        if self.due_statuses and len(set(self.due_statuses))!=len(self.due_statuses):raise ValueError('期限条件不能重复')
        if self.compare_months and self.start_month==self.end_month:raise ValueError('月份比较至少需要两个不同月份')
        if self.client and self.clients:raise ValueError('单客户与多客户筛选不能同时指定')
        if self.operation in ('archive','recommend') and not self.query.strip():raise ValueError('检索或推荐需技术描述')
        if self.operation=='case_brief':
            from casework.parser import CASE_RE
            if not CASE_RE.fullmatch(self.query.strip()):raise ValueError('单案核对须提供一个完整所编号，复杂比较交由Agent')
        if self.as_of:
            from datetime import date
            date.fromisoformat(self.as_of)
        if self.filing_month:
            from casework.analytics import month_bounds
            from casework.parser import CASE_RE
            month_bounds(self.filing_month)
            if not CASE_RE.fullmatch(self.query):raise ValueError('交局归月核对须指定一个完整所编号')
        return self


class DepartmentRoute(BaseModel):
    supported:bool
    request:DepartmentRequest|None=None


# The model supplies meaning and slots. The host compiles an executable action
# from requirements, so two model classifications cannot contradict each other.
DepartmentIntent=create_model('DepartmentIntent',__config__=ConfigDict(extra='forbid'),
    **{name:(field.annotation,field) for name,field in DepartmentRequest.model_fields.items()
       if name not in ('operation','requirements','recommendation_mode','normalization_events','retrieval_query')},
    requirements=(list[TaskRequirement],Field(min_length=1,max_length=20)))


class DepartmentIntentRoute(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request:DepartmentIntent


class DepartmentResolutionError(ValueError):
    def __init__(self,diagnostics):
        super().__init__('部门参数经有界修正后仍未通过校验，转完整任务流程')
        self.diagnostics=diagnostics


def original_search_query(task):
    """Keep every user clause; remove only the application's appended mode hint."""
    return task.split('\n【任务类型：',1)[0].strip()


def compile_intent(intent,query):
    from casework.requirements import action_for, normalize_constraints
    intent,events=normalize_constraints(intent)
    from casework.entities import bind_filters
    params=intent.model_dump(exclude={'operation'})
    params['normalization_events']=events
    params['operation']=action_for(intent.requirements,query)
    if params['operation']=='case_brief':
        params['meeting_scope']='latest' if re.search(r'最近(?:一次|一场)?会议|最新(?:一次|一场)?会议',query) else 'all'
    if params['operation']=='archive':
        # The model's compact query is a planning hint, never the only copy of
        # the user's subject. A preceding sentence may define what "similar" means.
        params['retrieval_query']=original_search_query(query)
    if params['operation'] in ('archive','recommend') and not any(c.kind=='workload_basis' for c in intent.constraints):
        params['archive_intent']=bind_intent(query,intent.archive_intent)
        require_scope(params['archive_intent'],query,intent.model_dump())
    if params['operation']=='archive' and not params['query'].strip() and params['archive_intent'].quote:
        # Recover only the verbatim search scope already bound to this task.
        params['query']=params['archive_intent'].quote
    if intent.technical_concepts:
        from casework import parser
        from casework.routing import affirmative_quote
        if any(not affirmative_quote(query,c.quote) for c in intent.technical_concepts):
            raise ValueError('技术概念没有绑定原问题中的正向技术要求')
        # Archive candidates are checked against the actual mechanism by the
        # evidence-bound selector. A guessed catalog tag must not add a new
        # technical requirement to that query (the catalog is not exhaustive).
        if params['operation']=='recommend':
            terms=[term for c in intent.technical_concepts for term in [c.tag,*parser.TAG_RULES[c.tag][:3]]]
            params['query']=' '.join([intent.query,*dict.fromkeys(terms)])
    if any(c.kind=='workload_basis' for c in intent.constraints):
        params['recommendation_mode']='workload_rank'
        params['query']=intent.query or '人员负荷'
    request=DepartmentRequest(**bind_filters(params))
    from casework.requirements import constraint_issues
    issues=constraint_issues(query,request)
    if issues:raise ValueError('；'.join(issues))
    return request


def sources_for(request):
    if request.operation=='archive':return ['knowledge']
    if request.operation=='recommend' and request.recommendation_mode in ('workload_only','workload_rank'):return ['db_query']
    if request.operation in ('recommend','case_brief'):return ['db_query','knowledge']
    return ['db_query']


def validate_request(query,request):
    """Preserve explicit case anchors; a month must not erase a single-case check."""
    from casework.parser import CASE_RE
    if request.case_field or request.case_fields:
        from casework.execution_policy import field_request
        expected=field_request(query)
        if (expected is None or request.model_dump()!=expected.model_dump()):
            return None
    ids={m.group().upper() for m in CASE_RE.finditer(query)}
    if ids and (request.operation not in ('cases','archive','case_brief') or
                not ids<={m.group().upper() for m in CASE_RE.finditer(request.query)}):
        return None
    return request


async def resolve(query):
    """Extract a small read contract against actual catalog values; no writes or facts."""
    from agent.llm import get_llm
    from casework import store,catalog,analytics
    cases=store.list_cases();meetings=catalog.meetings()
    values={'clients':sorted({c['client_alias'] for c in cases if c.get('client_alias')}),
            'iprs':sorted({p for m in meetings for p in m['iprs']}),
            'owners':sorted({c['owner_name'] for c in cases if c.get('owner_name')}),
            'domains':sorted({c['technical_domain'] for c in cases if c.get('technical_domain')})}
    from casework.parser import TAG_RULES
    values['technical_concepts']=TAG_RULES
    from pathlib import Path
    prompt=(Path(__file__).resolve().parents[1]/'prompt/department_intent.txt').read_text()
    prompt+='\n仅输出JSON对象，严格符合以下schema：'+json.dumps(DepartmentIntentRoute.model_json_schema(),ensure_ascii=False)
    model=get_llm(temperature=0).with_structured_output(DepartmentIntentRoute,method='json_mode',include_raw=True)
    messages=[{'role':'system','content':prompt},
        {'role':'user','content':json.dumps({'task':query,'today':analytics.today().isoformat(),'known_values':values},ensure_ascii=False)}]
    # Correct only an invalid parameter contract, once. Do not retry a deliberate
    # unsupported decision or relax source gates, filters or validators.
    diagnostics=[]
    for attempt in range(2):
        output=await model.ainvoke(messages)
        result=output.get('parsed') if isinstance(output,dict) and 'parsed' in output else output
        if result is None and isinstance(output,dict):
            # Validate raw JSON through the same schema when the provider wrapper lost parsed.
            raw=getattr(output.get('raw'),'content',None)
            if isinstance(raw,str):
                try:result=DepartmentIntentRoute.model_validate_json(raw)
                except ValueError:pass
        if result is not None:
            result=DepartmentIntentRoute.model_validate(result) if isinstance(result,dict) else result
            from casework.requirements import normalize_constraints
            normalized,_=normalize_constraints(result.request)
            other=[r for r in normalized.requirements if r.kind=='other']
            # A compound constraint sentence may be split into several typed spans.
            # Retry contradictions only when every non-punctuation character is covered.
            def covered_by_constraints(quote):
                import re
                uncovered=quote
                for c in sorted(normalized.constraints,key=lambda c:-len(c.quote)):
                    uncovered=uncovered.replace(c.quote,'')
                return not re.sub(r'[\s。；，,;！!？?、]','',uncovered)
            duplicates=[r.quote for r in other if covered_by_constraints(r.quote)]
            try:
                if any(r.kind in ('archive_materials','candidate_recommendation','candidate_history') for r in normalized.requirements) and not any(c.kind=='workload_basis' for c in normalized.constraints):
                    search_intent=bind_intent(query,normalized.archive_intent)
                    require_scope(search_intent,query,normalized.model_dump())
                if other and not duplicates:return None
                if duplicates:raise ValueError('同一原句被同时提为执行约束和不支持的动作：'+json.dumps(duplicates,ensure_ascii=False)+'。请判断原句是否要求新增交付；只属于已有执行行为时保留constraints，不再重复为other。历史快照、写入等真实额外要求仍须other，不得省略。')
                request=compile_intent(result.request,query)
            except ValueError as exc:
                diagnostics.append({'attempt':attempt+1,'extracted':result.request.model_dump(exclude_defaults=True),'issues':[str(exc)]})
                messages.extend([{'role':'assistant','content':result.model_dump_json()},
                    {'role':'user','content':'需求组合或参数不能执行，请按原任务完整修正；不能删去要求。'+str(exc)}])
                continue
            from casework.verification import task_contract_issues
            violations=task_contract_issues(query,request,{})
            if not validate_request(query,request):violations.append('显式案号未保留')
            if not violations:return request
            diagnostics.append({'attempt':attempt+1,'extracted':result.request.model_dump(exclude_defaults=True),'issues':violations})
            messages.extend([{'role':'assistant','content':result.model_dump_json()},
                {'role':'user','content':'以下原任务条件没有被参数保留，只能按原要求修正，不能省略：'+json.dumps(violations,ensure_ascii=False)}])
            continue
        error=output.get('parsing_error')
        for _ in range(3):
            if hasattr(error,'errors'):break
            error=getattr(error,'__cause__',None) or getattr(error,'__context__',None)
            if error is None:break
        errors=[{k:r[k] for k in ('loc','type','msg')} for r in error.errors()] if hasattr(error,'errors') else [{'msg':'结构化参数未通过校验'}]
        diagnostics.append({'attempt':attempt+1,'issues':errors})
        raw=output.get('raw')
        if isinstance(getattr(raw,'content',None),str):messages.append({'role':'assistant','content':raw.content[:20000]})
        messages.append({'role':'user','content':'参数未通过以下约束，请根据原任务重新提取；不能丢掉条件或改变任务，只允许修正参数格式。'+json.dumps(errors,ensure_ascii=False)})
    raise DepartmentResolutionError(diagnostics)


def plan_details(request):
    labels={'summary':'当前全量案件计数','archive':'技术正文与原件记载检索','cases':'案件信息与期限查询','monthly':'实际收案／已核实交局月报',
            'meetings':'实际三方会议清单','recommend':'有据经历与负荷辅助分案','case_brief':'单案信息与技术原文核对'}
    names={'query':'检索内容','client':'客户','clients':'客户范围','ipr':'实际参会IPR','owner':'承办人','stage':'案件状态',
           'domain':'技术领域','start_month':'起始月','end_month':'结束月','as_of':'基准日',
           'due_field':'期限节点','due_status':'期限条件','completed_only':'仅按原件完成状态筛选','filing_month':'实际交局归月核对','result_limit':'返回上限'}
    values={'draft_due_date':'初稿／所限','client_review_due_date':'客户送审','filing_due_date':'向局提交',
            'overdue':'在办且已逾期','next14':'在办且近期到期','unknown':'日期未知或冲突'}
    names.update(due_statuses='期限分组',compare_months='比较各月变化',missing_date_field='实际日期未核实字段',
                 case_field='查询字段',case_fields='查询字段',meeting_scope='会议范围')
    names['technical_concepts']='技术概念与原话'
    if request.recommendation_mode=='workload_only':labels['recommend']='人员在办与期限负荷查询（用户已确认）'
    if request.recommendation_mode=='workload_rank':labels['recommend']='按已知负荷排序的接案候选'
    names['constraints']='执行约束'
    display=request.model_dump(exclude={'retrieval_query'})
    if request.operation=='archive' and request.retrieval_query:display['query']=request.retrieval_query
    filters=[names[k]+'：'+str(values.get(v,v) if isinstance(v,str) else v) for k,v in display.items() if k not in ('operation','requirements','constraints','normalization_events','recommendation_mode','archive_intent') and v not in (None,'',False,[])]
    if request.operation=='archive' and not request.archive_kind:filters.append('查询口径：'+SCOPE_LABELS[request.archive_intent.mode])
    if request.recommendation_mode=='workload_only':filters.append('本次用户选择：仅查看人员负荷，暂缓技术推荐')
    return {'intent_summary':labels[request.operation]+('；'+'；'.join(filters) if filters else ''),
            'steps':(plan_requirement_steps(request)+['按提取的条件读取同一发布快照','程序执行'+labels[request.operation],
                     '绑定执行时来源并单列未知和冲突','逐项核对原问题、执行参数与交付，缺项不能通过']),
            'assumptions':['只覆盖已接入并成功解析的资料；不通过文件更新时间推断实际日期',
                           '所限、客户送审、向局提交分别处理；没有智能体写入或自动分案']}


def cell(value):
    return str(value if value is not None else '未核实').replace('|','\\|').replace('\n','；')


def meeting_detail_fields(request):
    text=' '.join(r.quote for r in request.requirements)
    return [(key,label) for key,label,words in [
        ('content','会议内容',('会议内容','会议纪要','沟通内容')),
        ('pending','待补材料',('待补','待补充')),
        ('changes','修改意见',('修改意见','审核意见'))]
        if any(word in text for word in words) or
        any(r.kind=={'content':'meeting_content','pending':'meeting_pending','changes':'meeting_changes'}[key] for r in request.requirements)]


def requested_case_dates(request):
    """Narrow only a typed date-only request; mixed case requests keep their details."""
    import re
    if (not request.requirements or any(r.kind != 'case_deadlines' for r in request.requirements)
            or request.filing_month):
        return []
    text = ' '.join(r.quote for r in request.requirements)
    from casework.routing import affirmative
    text = affirmative(text)
    planned = [('draft_due_date','初稿期限',r'初稿|所限'),
               ('client_review_due_date','客户送审期限',r'客户送审|送审期限|客户期限'),
               ('filing_due_date','向局提交期限',r'向局提交|提交期限|交局期限')]
    selected = [(field,label) for field,label,pattern in planned if re.search(pattern,text)]
    if selected and not re.search(r'收案|实际交局|承办|负责人|阶段|状态|基本信息|案件信息|全部|所有|计划提交节点',text):
        return selected
    if re.search(r'期限|计划|节点|截止|初稿|送审|提交|会议|文件|技术|承办|负责人|阶段|状态|基本信息|案件信息', text):
        return []
    fields = []
    if '收案' in text:
        fields.append(('received_date', '实际收案日期'))
    if re.search(r'实际交局|交局日期|何时交局|什么时候交局', text):
        fields.append(('actual_filed_date', '实际交局日期'))
    return fields


def case_date_value(row, field):
    if field in row.get('conflicting_candidates', {}):
        return '存在冲突'
    if field == 'actual_filed_date' and row.get('filing_verified') != '已核实':
        return '未核实'
    return cell(row[field]) if row.get(field) else '未记录'


def selected_brief_meetings(meetings,request):
    rows=meetings['rows']
    if request.meeting_scope=='latest':
        latest=max((r['date'] for r in rows if r.get('date')),default=None)
        rows=[r for r in rows if r.get('date')==latest] if latest else []
    return {**meetings,'rows':rows}


def render(result,request):
    if request.operation=='case_brief':
        ledger=render(result['ledger'],DepartmentRequest(operation='cases',query=request.query))
        archive=render(result['archive'],DepartmentRequest(operation='archive',query=request.query))
        archive=archive.replace('可借鉴机制的原文片段：','本案主要技术（原文节选）：')
        pending=''
        if any(r.kind=='case_material_gaps' or r.kind.startswith('meeting_') for r in request.requirements):
            meetings=result['ledger'].get('case_meetings',{'rows':[],'has_more':False,'evidence_id':''})
            selected=selected_brief_meetings(meetings,request)
            pending='\n\n## '+('最近有日期的登记会议（同日记录均保留）' if request.meeting_scope=='latest' else '本案全部登记会议与待补材料')+'\n'+render(selected,DepartmentRequest(operation='meetings',requirements=request.requirements))
            if request.meeting_scope=='latest' and any(not r.get('date') for r in meetings['rows']):
                pending+='\n另有日期未登记的会议，无法判断其先后，需核验。'
        from casework.materials import render_packet
        material_text='\n\n'+render_packet(result['materials'],archive) if result.get('materials') else ''
        return ledger+'\n\n'+archive+pending+material_text
    synthetic=result.get('coverage',{}).get('data_kind')=='synthetic'
    data_note='（独立模拟资料）' if synthetic else ''
    if request.operation=='summary':
        return '\n'.join(['## 当前案件数量'+data_note+'（基准日：'+result['as_of']+'）',
            '| 案件总数 | 在办案件数量 | 已核实实际交局数量 | 状态未知案件数量 | 来源 |',
            '| --- | --- | --- | --- | --- |',
            '| '+' | '.join(str(result[k]) for k in ('total_count','active_count','filed_count','unknown_stage_count'))+f" | [{result['evidence_id']}] |",
            '统计范围：当前接入资料。'])
    if request.operation=='archive':
        lines=['## 技术正文检索'+('（办理信息为模拟）' if synthetic else '')]
        review=result.get('selection_review',{})
        if review.get('mode'):
            lines.append('查询口径：'+SCOPE_LABELS[review['mode']]+'。')
            if review['mode']=='ambiguous':lines.append('以下按关联程度列出候选材料。')
            if review.get('status')!='passed':lines.append('关联程度核对未完成，以下仅保留检索候选。')
        for case in result['results']:
            facts=case['document_facts'];ids=facts['field_evidence_ids']
            refs=lambda field:''.join('['+sid+']' for sid in ids.get(field,[]))
            lines.extend(['',f"### {cell(case['case_id'])} · {cell(case.get('title'))}",
                ('原文件记载承办人：'+cell(facts['owner_name'])+'。'+refs('owner_name')) if facts.get('owner_name') else '承办人：当前材料未核实。',
                '原文件记载阶段：'+cell(facts.get('stage'))+'。'+refs('stage')])
            if facts.get('notes'):
                lines.append('内部案号：'+cell(case['case_id'])+'。材料来源与办理范围（原件备注）：'+cell(facts['notes'])+'。'+refs('notes'))
            relation=case.get('relevance')
            if relation:
                lines.append('关联程度：'+RELATION_LABELS[relation['relation']]+'；'+cell(relation['detail']))
                if relation.get('quote'):
                    lines.append('关联依据：'+cell(relation['quote'])+' ['+relation['evidence_id']+']')
            if case.get('candidate_only'):
                lines.append(case['case_id']+' 技术相关性待核实。')
            if facts.get('actual_filed_date'):
                lines.append('原文件记载实际交局日期：'+cell(facts['actual_filed_date'])+'。'+refs('actual_filed_date'))
            if facts.get('filing_confirmed') is True:
                lines.append('原件交局核实标记为已核实。'+refs('filing_confirmed'))
            source=next((p for p in case['sources'] if p['kind'] in ('application','disclosure')),case['sources'][0] if case['sources'] else None)
            if source:
                lines.extend(['','图中文字（未识别图形或连线关系）：' if source['kind']=='drawing' else ('关联原文片段：' if relation else '待核对候选原文片段：' if case.get('candidate_only') else '可借鉴机制的原文片段：'),'> '+source['excerpt'].replace('\n','\n> ')+f" [{source['evidence_id']}]"])
            completeness=case.get('evidence_completeness',{})
            for file_check in completeness.get('files',[]):
                for fact in file_check.get('facts',[]):
                    lines.extend(['','所问条件的直接原文：','> '+fact['quote'].replace('\n','\n> ')+f" [{fact['evidence_id']}]"])
            if completeness.get('status')=='unverified':
                lines.append('证据不足：尚未取得所问对象的必要条件或状态，当前不能作出确定判断。')
            for drawing in case.get('related_drawings',[]):
                lines.append('绘图附件：'+cell(drawing['source'])+'。'+drawing['scope']+'；按所编号关联。'+f"[{drawing['evidence_id']}]")
        if not result['results']:lines.append('当前接入资料未找到符合条件的材料。')
        if request.result_limit:
            lines.append(f'按要求最多展示{request.result_limit}份案件参考材料，本次找到并展示{len(result["results"])}份。')
        if request.completed_only:lines.append('范围：原件记载为已完成的案件。')
        if result['has_more']:lines.append('仍有未展示的检索候选，可在案件工作台缩小条件继续查看。')
        return '\n'.join(lines)
    if request.operation=='cases':
        if request.case_field or request.case_fields:
            from casework.execution_policy import FIELD_LABELS
            selected=request.case_fields or [request.case_field]
            lines=['（模拟办理信息）'] if synthetic else []
            for row in result['rows']:
                for field in selected:
                    value=case_date_value(row,field)
                    lines.append(row['case_id']+' 的'+FIELD_LABELS[field]+'：'+value+f"。[{result['evidence_id']}]")
                    if row.get('field_provenance_evidence_id'):
                        lines.append('原件字段出处：'+f"[{row['field_provenance_evidence_id']}]")
                    for candidate in row.get('conflicting_candidates',{}).get(field,[]):
                        lines.append('冲突记录：'+cell(candidate['value'])+'（'+cell(candidate['path'])+'，'+cell(candidate['location'])+'）'+f"。[{row['conflict_evidence_id']}]")
            if not result['rows']:lines.append('当前接入资料未找到该案件。'+f"[{result['evidence_id']}]")
            return '\n'.join(lines)
        if result.get('answer_markdown') and (request.due_status or request.due_statuses):
            sections=[result['answer_markdown']]
            if result.get('unknown_due_answer_markdown'):
                sections.append(result['unknown_due_answer_markdown'])
            if result.get('unknown_due_has_more'):sections.append('日期未知清单仍有未展示记录，不能视为完整清单。')
            return '\n\n'.join(sections)
        dates = requested_case_dates(request)
        if dates:
            lines = []
            if synthetic:lines.append('（模拟办理信息）')
            for row in result['rows']:
                for field, label in dates:
                    lines.append(row['case_id']+' 的'+label+'：'+case_date_value(row,field)+f"。[{result['evidence_id']}]")
                    candidates = row.get('conflicting_candidates', {}).get(field, [])
                    if candidates:
                        lines.append('冲突记录：'+'；'.join(cell(c['value'])+'（'+cell(c['path'])+'，'+cell(c['location'])+'）' for c in candidates)+f"。[{row['conflict_evidence_id']}]")
            if not result['rows']:lines.append('当前接入资料未找到该案件。'+f"[{result['evidence_id']}]")
            if result['has_more']:lines.append('还有未展示记录，可在案件工作台查看。')
            return '\n'.join(lines)
        if request.due_statuses:
            from casework.requirements import due_group
            labels={'overdue':'已过计划期限','next14':'未来两周计划到期','unknown':'期限待核实'}
            sections=[]
            for status in request.due_statuses:
                group={**result,'rows':[r for r in result['rows'] if due_group(r.get(request.due_field),result['as_of'])==status],
                       'unknown_due_cases':[],'unknown_due_has_more':False}
                body=render(group,request.model_copy(update={'due_status':status,'due_statuses':None}))
                proof=result.get('due_group_proofs',{}).get(status,{})
                if proof.get('complete') and proof.get('count')==0:
                    body='当前接入的在办案件中，没有符合该项条件的案件。'+f"[{result['evidence_id']}]"
                sections.append('## '+labels[status]+'\n'+body)
            if result.get('unknown_due_cases') and 'unknown' not in request.due_statuses:
                sections.append('## 期限待核实\n'+'\n'.join(r['case_id']+'：所选计划期限缺失或冲突，不能判断是否已过期。'
                    +f"[{result['unknown_due_evidence_id']}]" for r in result['unknown_due_cases']))
            if result.get('unknown_due_has_more'):sections.append('期限未知清单还有未展示记录，请在工作台继续分页核对。')
            return '\n\n'.join(sections)
        rows=['## 案件查询结果'+data_note,
              '| 所编号 | 客户案号 | 承办人 | 案件状态 | 初稿期限／所限 | 客户送审期限 | 向局提交期限 | 实际交局日期 | 交局核实 | 来源 |',
              '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        notes=[]
        for r in result['rows']:
            if request.query or request.missing_date_field=='received_date':
                notes.append(r['case_id']+' 实际收案日期：'+case_date_value(r,'received_date')+'。'+f"[{result['evidence_id']}]")
            keys=('case_id','client_case_id','owner_name','stage','draft_due_date','client_review_due_date','filing_due_date','actual_filed_date','filing_verified')
            rows.append('| '+' | '.join('冲突待核实' if k in r['conflicting_candidates'] else cell(r.get(k)) for k in keys)+f" | [{result['evidence_id']}] |")
            from casework.parser import CASE_RE
            if CASE_RE.fullmatch(request.query):
                from casework.requirements import due_group
                labels={'draft_due_date':'初稿','client_review_due_date':'客户送审','filing_due_date':'向局提交'}
                statuses={'overdue':'已过计划日','next14':'未来两周内计划到期','later':'后续计划节点','unknown':'日期缺失或冲突，待核实'}
                notes.append('节点提醒（基准日'+result['as_of']+'）：'+'；'.join(label+'：'+statuses[due_group(r.get(field),result['as_of'])]
                    for field,label in labels.items())+f"。[{result['evidence_id']}]")
            for field,candidates in r['conflicting_candidates'].items():
                label={'received_date':'实际收案日期','actual_filed_date':'实际交局日期','draft_due_date':'初稿期限／所限','client_review_due_date':'客户送审期限','filing_due_date':'向局提交期限'}.get(field,field)
                notes.append(f"{r['case_id']} 的{label}存在冲突："+'；'.join(cell(c['value'])+'，'+cell(c['path'])+'，'+cell(c['location']) for c in candidates)+f"。[{r['conflict_evidence_id']}]")
            if request.filing_month:
                verified=bool(r['actual_filed_date'] and r['filing_verified']=='已核实')
                if not verified:
                    conclusion='实际交局日期未核实，暂不能计入'+request.filing_month+'的实际交局数量'
                else:
                    conclusion=('可以' if r['actual_filed_date'][:7]==request.filing_month else '不能')+'计入'+request.filing_month+'的实际交局数量，实际交局日期为'+r['actual_filed_date']
                notes.append(r['case_id']+'：'+conclusion+'。'+f"[{result['evidence_id']}]")
            elif r['stage'] in ('已交局','已完成','已交付') and (not r['actual_filed_date'] or r['filing_verified']!='已核实'):
                notes.append(r['case_id']+' 已标记完成，实际交局日期未核实。'+f"[{result['evidence_id']}]")
        if notes:rows.extend(['',*notes])
        if not result['rows']:rows.append('当前接入资料未找到符合条件的案件。'+f"[{result['evidence_id']}]")
        if request.due_status in ('overdue','next14'):
            rows.extend(['',f"基准日：{result['as_of']}；以上为在办案件的计划期限。"])
            for c in result.get('unknown_due_cases',[]):rows.append(c['case_id']+'，承办人：'+cell(c.get('owner_name'))+'；所选期限缺失或冲突，不能判定逾期，另列待核实。'+f"[{result['unknown_due_evidence_id']}]")
            if result.get('unknown_due_has_more'):rows.append('还有期限未知记录，可在案件工作台继续查看。')
        if any(r.kind=='case_list' and re.search(r'多少|数量|几[件个]',r.quote) for r in request.requirements):
            scope='；'.join(str(v) for v in result.get('filters',{}).values() if v not in (None,'',[]))
            rows.append('筛选范围：'+(scope or '当前接入资料')+'。')
            rows.extend(['| 筛选案件数量（件） | 来源 |','| --- | --- |',
                         '| '+str(result['total'])+' | '+f"[{result['count_evidence_id']}] |"])
        if request.missing_date_field:
            rows.append('范围：当前接入资料中'+{'received_date':'实际收案日期','actual_filed_date':'实际交局日期或核实依据'}[request.missing_date_field]+'未核实的案件，包含各办理阶段。')
        if result['has_more']:rows.append('当前为分页结果，后续记录请在案件工作台查看。')
        return '\n'.join(rows)
    if request.operation=='monthly':
        return result['answer_markdown']+'\n\n统计范围：当前接入资料'+('（模拟办理信息）' if synthetic else '')+'。'
    if request.operation=='meetings':
        details=meeting_detail_fields(request)
        agents=sorted({name for row in result['rows'] for name in row['agents']})
        lines=['## 实际登记的三方会议'+data_note]
        if agents:
            lines.extend(['实际参会同事：'+'、'.join(cell(name) for name in agents)+f"。[{result['evidence_id']}]",''])
        lines.extend(['| 会议日期 | 所编号 | 客户 | 实际参会IPR | 实际参会代理师 | '+''.join(label+' | ' for _,label in details)+'来源 |',
                      '| '+' | '.join(['---']*(6+len(details)))+' |'])
        for r in result['rows']:
            lines.append('| '+' | '.join(cell(v) for v in (r['date'],r['case_id'],r['client'],'、'.join(r['iprs']),'、'.join(r['agents']),*(r.get(key) for key,_ in details)))+f" | [{result['evidence_id']}] |")
        if not result['rows']:lines.append('当前接入资料未找到符合条件的会议。')
        if result['has_more']:lines.append('当前为分页结果，后续记录请在案件工作台继续查看。')
        return '\n'.join(lines)
    load_only=request.recommendation_mode=='workload_only'
    load_rank=request.recommendation_mode=='workload_rank'
    has_history=not (load_only or load_rank)
    candidates=sorted(result['candidates'],key=lambda c:c['name']) if load_only else result['candidates'][:3]
    sid=result['evidence_id'];base=result['as_of']
    fields=[('draft_due_date','初稿'),('client_review_due_date','客户送审'),('filing_due_date','向局提交')]
    from datetime import timedelta
    from casework.analytics import as_date
    end=(as_date(base)+timedelta(days=14)).isoformat()
    rows=[('## 人员负荷' if load_only else '## 辅助分案候选')+data_note,f'### 基准日 {base}',
          ('以下为当前已登记人员的负荷。' if load_only else '按在办与期限负荷排序的前三位候选：' if load_rank else '结合相关案件经历、技术领域与负荷排序的前三位候选：')+f"[{result['basis_evidence_id']}]",
          f"临近期限范围：{base}至{end}之前。[{result['basis_evidence_id']}]",
          '| 代理师 | 在办案件数量（件） | 临近期限案件数量（件） | 逾期案件数量（件） | 状态未知案件数量（件） |',
          '| --- | --- | --- | --- | --- |']
    for c in candidates:
        rows.append(f"| {cell(c['name'])} | {c['active_count']} [{sid}] | {len(c['near_due_cases'])} [{sid}] | {len(c['overdue_cases'])} [{sid}] | {len(c['unknown_stage_cases'])} [{sid}] |")
    for c in candidates:
        rows.extend(['',f"### {cell(c['name'])}"])
        if has_history:rows.append('推荐依据：'+c['basis']+'。绑定领域：'+('、'.join(c['bound_domains']) or '未登记')+'。')
        for h in (c['history_matches'][:3] if has_history else []):
            source=next((p for p in h['sources'] if p.get('excerpt')),None)
            if source:rows.append(f"- 历史案件记录承办 {h['case_id']} [{c['history_evidence_id']}]，技术原文：{cell(source['excerpt'])} [{source['evidence_id']}]")
        if has_history and not c['history_matches']:rows.append('没有已核对的相关历史承办机制，领域与已知负荷仅作兜底。')
        rows.extend(['','| 当前在办案号 | 案件状态 | 初稿期限／所限 | 客户送审期限 | 向局提交期限 | 期限状态 | 来源 |','| --- | --- | --- | --- | --- | --- | --- |'])
        for case in c['active_cases']:
            from datetime import timedelta
            from casework.analytics import as_date
            end=(as_date(base)+timedelta(days=14)).isoformat()
            status=[]
            for field,label in fields:
                value=case.get(field)
                if not value:status.append(label+'待核实')
                elif value<base:status.append(label+'已逾期')
                elif value<end:status.append(label+'临近')
            rows.append('| '+' | '.join(cell(case.get(k)) for k in ('case_id','stage',*(f for f,_ in fields)))+' | '+('、'.join(status) or '未触发已知临近/逾期节点')+f" | [{c['load_evidence_id']}] |")
        if not c['active_cases']:rows.append('当前接入记录中无已知在办案件。')
        unknown=[label for f,label in fields if c['unknown_dates_by_node'].get(f)]
        if unknown:rows.append('期限待核实：'+'、'.join(unknown)+'。')
        if c['unknown_stage_cases']:rows.append('案件状态待核实：'+'、'.join(v['case_id'] for v in c['unknown_stage_cases'])+'。')
    if not load_only:rows.extend(['','以上为接案候选，尚未保存分案。'])
    return '\n'.join(rows)


async def execute(request,thread_id,round_num,required_sources):
    from api import context,evidence
    from api.monitor import monitor
    from tools.casework_tools import query_department_cases,search_case_archive,read_case_archive_document
    from casework import store, analytics
    if request.operation=='recommend' and request.recommendation_mode=='technical':
        require_scope(request.archive_intent,request.query,request.model_dump())
    context.set_round(round_num);context.reset_tool_budget()
    params=request.model_dump(exclude_none=True,exclude={'case_field','case_fields','meeting_scope','completed_only','archive_kind','archive_intent','filing_month','result_limit','requirements','constraints','normalization_events','technical_concepts','recommendation_mode','retrieval_query'})
    if request.operation!='monthly':params.pop('compare_months',None)
    if request.operation=='archive':
        scope=request.archive_intent if request.archive_intent!=ArchiveIntent() else bind_intent(request.query)
        require_scope(scope,request.query,request.model_dump())
        params={'query':request.retrieval_query or request.query,'completed_only':request.completed_only,'query_scope':scope.mode}
        if request.archive_kind:params['kind']=request.archive_kind
        # "Find one" limits delivery, not the candidates judged for relevance.
        # A first-ranked background mention must not prevent reading a direct match.
        params['limit']=min(30,max(6,request.result_limit or 6))
        if any(c.kind=='lexical_only' for c in request.constraints):params['allow_vector']=False
    if request.operation=='recommend' and request.recommendation_mode=='workload_rank':params['recommendation_mode']='workload_rank'
    if request.operation=='cases':params.update(limit=100,complete=True)
    if request.operation=='cases' and any(r.kind=='case_list' and re.search(r'多少|数量|几[件个]',r.quote) for r in request.requirements):params['include_count']=True
    tool=search_case_archive if request.operation=='archive' else query_department_cases
    run_id=f'department:{thread_id}:{round_num}'
    calls=[('result',tool,params)] if request.operation!='case_brief' else [
        ('ledger',query_department_cases,{'operation':'cases','query':request.query,'as_of':request.as_of}),
        ('archive',search_case_archive,{'query':request.query,'limit':1})]
    if request.operation=='case_brief':
        from casework.materials import collect_case_materials
        calls.append(('materials',collect_case_materials,{'case_id':request.query}))
    loop=asyncio.get_running_loop()
    def read_tools():
        results={}
        with store.read_snapshot():
            snapshot_version=store.published_version()
            for key,read_tool,args in calls:
                event={'agent_name':'部门业务流程','agent_run_id':run_id,'round':round_num,
                       'run_id':run_id+':'+key,'tool_name':read_tool.name}
                def report(kind,message,data):
                    asyncio.run_coroutine_threadsafe(monitor.report(thread_id,kind,message,data),loop).result(timeout=10)
                report('tool_start','按已确认计划查询部门业务事实',{**event,'args':args})
                try:
                    from casework.read_cache import invoke
                    raw=invoke(read_tool,args)
                    results[key]=json.loads(raw)
                    if request.operation=='archive' and key=='result':
                        from casework.archive_readback import complete
                        def read_gap(read_args):
                            read_event={**event,'tool_name':'read_case_archive_document','args':read_args}
                            report('tool_start','按证据缺口读取同版本原文',read_event)
                            value=invoke(read_case_archive_document,read_args)
                            report('tool_end','原文回读已返回',read_event)
                            return value
                        complete(results[key],request.retrieval_query or request.query,read_gap)
                except Exception as exc:
                    report('tool_error','部门只读查询未完成',{**event,'error':str(exc)})
                    raise
                report('tool_end','部门业务事实已返回',{**event,'summary':raw[:1000]})
                if (key=='result' and request.operation=='recommend' and request.recommendation_mode=='technical'
                        and analytics.recommendation_needs_confirmation(results[key])):
                    calls.append(('technical_lookup',search_case_archive,{'query':request.query,'limit':6}))
        results['_snapshot_version']=snapshot_version
        return results
    token=_workload_rank_authorized.set(request.operation=='recommend' and request.recommendation_mode=='workload_rank' and any(c.kind=='workload_basis' for c in request.constraints))
    try:
        results=await asyncio.to_thread(read_tools)
    finally:
        _workload_rank_authorized.reset(token)
    result={k:v for k,v in results.items() if not k.startswith('_')} if request.operation=='case_brief' else results['result']
    if request.operation=='archive' and request.archive_kind is None:
        from casework.archive_selection import select_candidates
        result=await select_candidates(result,request.retrieval_query or request.query,intent=scope)
    if request.operation=='archive' and request.result_limit:
        selected=result['results']
        result={**result,'results':selected[:request.result_limit],
                'has_more':bool(result.get('has_more') or len(selected)>request.result_limit),
                'selection_candidate_count':len(selected)}
        if result.get('selection_review'):
            review=result['selection_review']
            result['selection_review']={**review,'candidate_pool_status':review['status'],
                'status':'unverified' if any(c.get('candidate_only') for c in result['results']) else 'passed'}
    report=render(result,request)
    entries=evidence.snapshot(thread_id)
    from casework.verification import verify
    checked=verify(request,result,entries,report)
    from casework.verification import delivery
    output={'report':report,'evidence':entries,'called_sources':sources_for(request),
            'evidence_readback':result.get('evidence_readback',{}),
            'business_check':checked,
            'business_delivery':delivery(request,result),
            'execution_contract':{'mode':'fixed_read_only','tools':[t.name for _,t,_ in calls]+[
                c['tool'] for c in result.get('evidence_readback',{}).get('calls',[])],
                                  'snapshot_version':results['_snapshot_version'],'read_schedule':'sequential_same_snapshot',
                                  'executed_sources':sources_for(request),'allowed_sources':required_sources,
                                  'source_files_written':False,'assignment_saved':False,'artifacts_created':False,
                                  'candidate_detail_entry':'案件工作台／辅助分案'},
            'required_sources':required_sources,'require_pdf':False,'pdf_produced':False}
    if checked.get('status')=='passed' and any(r.kind=='markdown_report' for r in request.requirements):
        from pathlib import Path
        import hashlib
        root=Path(context.get_session_dir())
        path=root/'部门月报.md'
        if path.exists():raise ValueError('月报目标文件已存在，不能覆盖其他文件')
        path.write_text(report,encoding='utf-8')
        output['report_files']={path.name:hashlib.sha256(report.encode()).hexdigest()}
        output['business_delivery']['markdown_report']=path.name
        output['execution_contract']['artifacts_created']=True
    if 'technical_lookup' in results:
        from api.clarification import recommendation_question, question_report
        question=recommendation_question(request,result,results['technical_lookup'])
        output.update(clarification=question,report=question_report(question))
        output['business_check']={**checked,'status':'awaiting_input','issues':['推荐所需技术经历尚未确认，须由用户选择下一步']}
    return output
