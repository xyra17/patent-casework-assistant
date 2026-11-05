import asyncio,json
from unittest.mock import patch
from types import SimpleNamespace
from casework.archive_selection import select_candidates
from casework.workflow import DepartmentRequest,render
from casework.verification import delivery
from tools.statistics_check import statement_units
from tools.casework_tools import query_department_cases
from casework.requirements import TaskRequirement

def test_meeting_pending_delivered_and_declared():
    request=DepartmentRequest(operation='meetings',requirements=[TaskRequirement(kind='meeting_attendance',quote='逐场列待补材料')])
    payload={'rows':[{'case_id':'AI-2026-001','client':'测试客户','date':'2026-01-01','iprs':['IPR'],'agents':['甲'],
                     'pending':'补充低频token量化误差对照'}],'evidence_id':'S1','has_more':False}
    assert '待补材料' in render(payload,request)
    assert '补充低频token量化误差对照' in render(payload,request)
    assert 'pending' in delivery(request,payload)['fields']

def model(decision):
    class Model:
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages,**kwargs):
            payload=json.loads(messages[-1]['content'].split('\n前次契约错误：')[0])
            ids={d['case_id'] for d in payload['documents']}
            return {'parsed':{'documents':[d for d in decision['documents'] if d['case_id'] in ids]}}
    return Model

def candidates():
    return {'results':[{'case_id':'AI-2026-001','sources':[{'kind':'application','excerpt':'识别关键约束锚点并预留强制保留块'}]},
                       {'case_id':'AI-2026-002','sources':[{'kind':'application','excerpt':'按缓存使用频率淘汰低价值项'}]}],'has_more':False}

def test_archive_semantic_selection_requires_literal_positive_quote():
    decision={'documents':[{'case_id':'AI-2026-001','relevant':True,'quote':'识别关键约束锚点并预留强制保留块','detail':'满足锚点保留'},
                           {'case_id':'AI-2026-002','relevant':False,'quote':'','detail':'只说明缓存淘汰'}]}
    result=asyncio.run(select_candidates(candidates(),'找采用约束锚点保护的案件',model(decision)))
    assert [c['case_id'] for c in result['results']]==['AI-2026-001']
    decision['documents'][0]['quote']='自行编造的原文不是证据'
    failed=asyncio.run(select_candidates(candidates(),'找采用约束锚点保护的案件',model(decision)))
    assert failed['selection_review']['status']=='unverified'
    assert len(failed['results'])==2

def test_two_metric_scope_is_not_a_business_count():
    assert not statement_units('实际收案与已核实实际交局两项口径分开。')
    assert statement_units('本月收到两项专利。')
    assert not statement_units('测试代理师01（模拟）负责该案。')

def test_summary_is_full_snapshot_and_rejects_unsupported_filters():
    import pytest
    with pytest.raises(ValueError):DepartmentRequest(operation='summary',owner='甲')
    cases=[{'case_id':'A','stage':'初稿撰写'}, {'case_id':'B','stage':'已交局','filing_verified':True,'actual_filed_date':'2026-01-01'},
           {'case_id':'C','stage':'未登记'}]
    with patch('tools.casework_tools._guard',return_value=None),patch('tools.casework_tools._add',return_value='S1'),\
         patch('casework.store.list_cases',return_value=cases),patch('casework.store.coverage',return_value={}),\
         patch('casework.catalog.meetings',return_value=[]):
        # Invoke the wrapped function directly to avoid a real catalog or active UI context.
        result=json.loads(query_department_cases.func.__wrapped__(operation='summary'))
    assert [result[k] for k in ['total_count','active_count','filed_count','unknown_stage_count']]==[3,1,1,1]


def test_quote_linewrap_recovery_does_not_allow_paraphrase():
    from casework.archive_selection import literal_quote
    assert literal_quote('保护离群值高精度组；为普通组选择量化尺\n度','为普通组选择量化尺度')=='为普通组选择量化尺\n度'
    assert literal_quote('保护离群值高精度组','保护全部值高精度组') is None

def test_scope_and_citation_do_not_require_technical_body():
    from casework.requirements import canonical_constraint
    assert canonical_constraint('说明模拟数据限制')=='data_scope'
    assert canonical_constraint('不要沿用旧的34案或300案口径')=='data_scope'
    assert canonical_constraint('列来源')=='cite_evidence'
    assert canonical_constraint('说明模拟数据准确率') is None


def test_summary_render_is_checked_with_actual_statistics_contract():
    from tools.statistics_check import check_statistics
    # Frozen external contract from the real failed run; no dependency on local eval files.
    contract={'metrics':[{'column':key,'index':index,'label':label,'names':names,'unit':'件','definition_verified':True,
                         'time':{'status':'bounded','field':'as_of','start':'2026-11-03','end':'2026-11-04'},'filters':[]}
                        for index,(key,label,names) in enumerate([
                            ('total_count','案件总数',['案件总数']),('active_count','在办案件数量',['在办案件数量']),
                            ('filed_count','实际交局数量',['实际交局数量','已核实实际交局数量']),
                            ('unknown_stage_count','状态未知案件数量',['状态未知案件数量'])])], 'dimensions':[]}
    entries=[{'id':'S1','type':'database','rows':[[100,75,25,0]],'statistics':contract}]
    payload={'as_of':'2026-11-03','total_count':100,'active_count':75,'filed_count':25,'unknown_stage_count':0,'evidence_id':'S1','coverage':{'data_kind':'synthetic'}}
    request=DepartmentRequest(operation='summary')
    report=render(payload,request)
    check=asyncio.run(check_statistics(report,entries))
    assert check['status']=='passed',check
    assert check['checked_claims']==4
    corrupted=asyncio.run(check_statistics(report.replace('| 100 |','| 101 |'),entries))
    assert corrupted['status']=='failed'


def test_report_membership_question_is_not_file_delivery():
    from agent.main_agent import classify_request
    assert classify_request('实际收案日期未知，能根据会议时间猜一个并计入月报吗？ 不生成文件。')==(False,False)
    assert classify_request('生成月报并将本案计入月报')==(True,False)

def test_registered_material_gaps_compile_to_single_case():
    from casework.requirements import action_for
    requirements=[TaskRequirement(kind='case_technology',quote='正文'),TaskRequirement(kind='case_material_gaps',quote='待补材料')]
    assert action_for(requirements,'查AI-2026-001正文和待补材料')=='case_brief'


def test_generated_shared_background_cannot_establish_specific_mechanism():
    from casework.archive_selection import core_excerpt
    text='SYN-AI-2026-028\n[0001] 背景与处理边界：关键锚点保护\n[0003] 实现约束：强制保留块M\nS1 标定单元格行列身份\n[0004] 连接同列标题和脚注'
    selected=core_excerpt(text)
    assert '强制保留块M' not in selected and '连接同列标题和脚注' in selected
    assert '关键锚点保护' not in selected
    public='CN118034661A\n实现约束：直接技术内容'
    assert core_excerpt(public)==public


def test_body_excerpt_does_not_answer_whether_a_mechanism_is_proven():
    from casework.verification import task_contract_issues
    request=DepartmentRequest(operation='archive',query='AI-2026-003')
    issues=task_contract_issues('查AI-2026-003，反例是否能证明该案实现了向量索引？',request,{})
    assert any('原文摘录动作不能替代推理结论' in i for i in issues)


def test_archive_does_not_add_guessed_catalog_mechanism_to_user_query():
    from casework.workflow import DepartmentIntent, compile_intent
    query='找关键约束锚点保护、稀疏块选择相关的申请正文'
    intent=DepartmentIntent(query='关键约束锚点保护 稀疏块选择',requirements=[
        {'kind':'archive_materials','quote':query}],technical_concepts=[
        {'tag':'检索增强生成','quote':'关键约束锚点保护'}])
    request=compile_intent(intent,query)
    assert request.operation=='archive'
    assert request.query=='关键约束锚点保护 稀疏块选择'
    assert 'RAG' not in request.query


def test_independent_archive_review_can_remove_an_analogous_candidate():
    calls=[]
    class Model:
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):
            calls.append(messages)
            docs=json.loads(messages[-1]['content'])['documents']
            return {'parsed':{'documents':[
                {'case_id':d['case_id'],'relevant':len(calls)==1,
                 'quote':d['text'] if len(calls)==1 else '',
                 'detail':'初筛相关' if len(calls)==1 else '原文只支持临时输入修订，不支持既有事实撤销'} for d in docs]}}
    result=asyncio.run(select_candidates({'results':[candidates()['results'][0]]},'目标机制要求：事实撤销依赖传播',Model))
    assert result['selection_review']['status']=='passed'
    assert result['results']==[]
    assert len(calls)==2


def test_archive_absence_must_be_scoped_at_the_claim():
    from tools.content_verification import archive_absence_issues
    evidence=[{'type':'knowledge','casework':True}]
    assert archive_absence_issues('正文没有任何一句记载该机制。\n不能断言全文缺失。',evidence)
    assert not archive_absence_issues('本次已回读片段未见该机制，不能断言全文没有。',evidence)
    assert not archive_absence_issues('正文未记载该机制。',[])


def test_meeting_pending_is_typed_without_requiring_column_words():
    from casework.workflow import DepartmentIntent, compile_intent, meeting_detail_fields
    query='之前谁跟他碰过？当时说还要补什么来着？'
    req=compile_intent(DepartmentIntent(requirements=[
        {'kind':'meeting_attendance','quote':'之前谁跟他碰过？'},
        {'kind':'meeting_pending','quote':'当时说还要补什么来着？'}]),query)
    assert req.operation=='meetings'
    assert ('pending','待补材料') in meeting_detail_fields(req)


def test_colloquial_month_comparison_cannot_silently_become_counts_only():
    from casework.verification import task_contract_issues
    req=DepartmentRequest(operation='monthly',start_month='2026-09',end_month='2026-10')
    assert any('比较月份' in i for i in task_contract_issues('9月和10月哪个月多一点？',req,{}))
    req.compare_months=True
    assert not any('monthly_comparison' in i or '比较月份' in i for i in task_contract_issues('9月和10月哪个月多一点？',req,{}))


def test_reference_request_does_not_authorize_personnel_recommendation():
    from casework.verification import task_contract_issues
    req=DepartmentRequest(operation='recommend',query='记忆撤销')
    assert any('接案人员' in i for i in task_contract_issues('以前有没有做过这种？我想找个案子参考一下。',req,{}))
    assert not any('接案人员' in i for i in task_contract_issues('这种案子让谁接合适？',req,{}))


def test_markdown_bold_does_not_hide_unscoped_archive_claim():
    from tools.content_verification import archive_absence_issues
    assert archive_absence_issues('整份正文**没有把向量索引写成模块**。',[{'type':'knowledge','casework':True}])
