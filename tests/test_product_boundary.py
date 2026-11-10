"""Independent synthetic contract regressions; no private raw or paid model."""
import copy
import json
from pathlib import Path
import pytest
from tools.product_boundary import request_contract, scope, prompt, publication_notice, digest
from tools.result_assessment import assess_result, report_hash
from tools.attachment_tables import query_table
from tools.source_delivery import dedup_aggregation_issues

ROOT=Path(__file__).resolve().parents[1]


def fixture(profile='public-reference', query='检索公开资料', *, passed=True, text='有据说明[S1]'):
    contract=request_contract(query, {'profile':profile})
    run={'status':'completed','result':text,'degraded':not passed,
        'evidence':[{'id':'S1','type':'web','title':'官方原件','url':'https://example.org/doc', 'snippet':'原文摘要'}],
        'events':[{'event':'product_boundary_request','data':contract},
        {'event':'reflection','data':{'round':1,'candidate_report_ref':report_hash(text),'verification':{'status':'passed' if passed else 'failed',
            'checks':[{'criterion':'evidence_support','status':'passed' if passed else 'failed'},
                      {'criterion':'task_completion','status':'passed' if passed else 'failed'}]}}},
        {'event':'quality_summary','data':{'passed':passed,'returned_round':1,'issues':[]}}]}
    return run


def state(run):return assess_result(run)['product_boundary']['status']


def test_normal_public_retrieval_verified_without_human_review():
    assert state(fixture())=='VERIFIED'


@pytest.mark.parametrize('change', ['no_source','forged_ref','review_unknown','final_hash_changed','tool_failure'])
def test_failures_never_renamed_professional_review(change):
    r=fixture('disclosure-search','找技术相似的参考案')
    if change=='no_source':r['evidence']=[]
    if change=='forged_ref':r['evidence'][0]['id']='S2'
    if change=='review_unknown':r['events'][1]['data']['verification']={'status':'unverified','checks':[]}
    if change=='final_hash_changed':r['result']+='改写后的正文'
    if change=='tool_failure':r['status']='failed'
    assert state(r) in ('INCOMPLETE','BLOCKED')


def test_candidate_prose_does_not_decide_state():
    assert state(fixture(text='请人工确认[S1]'))=='VERIFIED'
    assert state(fixture(text='全部核验成功[S1]',passed=False))=='INCOMPLETE'


def test_professional_candidates_only_review_after_objective_sources_pass():
    r=fixture('disclosure-search','找机制相似可借鉴的方案',text='初步分析[S1][S2]')
    r['evidence']=[{'id':'S1','type':'file','title':'交底.pdf','revision':'a'*64,'location':'第1页','snippet':'原文A'},
        {'id':'S2','type':'knowledge','title':'参考说明','revision':'b'*64,'location':'第2页','snippet':'原文B','case_id':'SIM-2026-01'}]
    assert state(r)=='REVIEW_REQUIRED'
    assert assess_result(r)['product_boundary']['human_confirmation'] is None
    r['evidence'][0]['location']=''
    assert state(r)=='INCOMPLETE'




async def _program_diff_complete_and_hash_bound_without_technical_inference(tmp_path, monkeypatch):
    from api import context,evidence,storage
    from api.monitor import monitor
    from tools.product_boundary import prepare_objective
    monkeypatch.setattr(storage,'_DB_PATH',tmp_path/'app.db');storage.init_db()
    thread='boundary-diff';context.set_session(thread,str(tmp_path));evidence.reset(thread)
    (tmp_path/'attachments').mkdir()
    (tmp_path/'attachments/old.md').write_text('标题\n\n保留七天\n检查版本\n')
    (tmp_path/'attachments/new.md').write_text('标题\n\n撤销立即失效\n检查版本\n')
    captured=[]
    async def report(t,event,msg,data):captured.append({'event':event,'data':data})
    monkeypatch.setattr(monitor,'report',report)
    c=request_contract('仅列文字增删改与未变化，提供两侧原句',{'profile':'document-comparison','ordered_files':['old.md','new.md']})
    try:
        with scope(c):await prepare_objective(thread)
        obj=captured[-1]['data']
        assert obj['status']=='VERIFIED' and len(obj['records'])==4
        assert obj['edit_counts']=={'added':0,'deleted':0,'modified':1,'schema':0}
        assert obj['records'][2]['left']['lines'][0]['location'].startswith('物理第3行')
        assert not c['professional_judgments']
        r=fixture('document-comparison',text='文字对照[S1][S2]')
        r['evidence']=evidence.snapshot(thread);r['events'][0]['data']=c;r['events'].append(captured[-1])
        assert state(r)=='VERIFIED'
        r['events'][0]['data']=request_contract('比较机制及技术影响',c)
        assert state(r)=='REVIEW_REQUIRED'
        obj['records'][2]['left']['lines'][0]['text']='假原文'
        assert state(r)=='INCOMPLETE'
    finally:context.reset_session();evidence.clear(thread)


def test_professional_notice_cannot_certify_or_disable_existing_guards():
    c=request_contract('比较机制与技术影响',{'profile':'document-comparison'})
    with scope(c):
        text=publication_notice('原文对照');assert '待人工确认' in text
        assert publication_notice(text)==text
        assert '语义越界拦截' in prompt()
    assert publication_notice('普通查询')=='普通查询'


def csv(tmp_path, text):
    p=tmp_path/'cases.csv';p.write_text(text);return p


def test_explicit_dedup_keeps_full_unique_set_original_rows_and_scope(tmp_path):
    p=csv(tmp_path,'案号,负责人,阶段\nA,甲,在办\nA,甲,在办\nB,甲,在办\nC,乙,\n')
    q=query_table(p,filters=[{'field':'阶段','op':'eq','value':'在办'}], group_by=['负责人'],
        aggregates=[{'field':'案号','op':'count','alias':'案件数'}],deduplicate_by=['案号'])
    assert q['rows']==[['甲',2]]
    assert q['deduplication']['unique_rows']==3
    proof=q['aggregation_proof'];assert proof['file_sha256'] and proof['complete']
    assert proof['groups'][0]['metrics'][0]['unique_values']==['A','B']
    assert proof['groups'][0]['source_rows']==[2,3,4]
    e={'id':'S1','title':p.name,'revision':proof['file_sha256'],**q}
    assert not dedup_aggregation_issues('按案号去除完全重复行',[e])
    # Ordinary COUNT remains row count, never silently becomes unique count.
    count=query_table(p,aggregates=[{'field':'案号','op':'count'}])
    distinct=query_table(p,aggregates=[{'field':'案号','op':'nunique'}])
    assert count['rows']==[[4]] and distinct['rows']==[[3]]


@pytest.mark.parametrize('text', ['案号,阶段\nA,在办\nA,交局\n','案号,阶段\n,在办\n'])
def test_dedup_conflicting_or_missing_identity_requires_scope_clarification(tmp_path,text):
    with pytest.raises(ValueError):query_table(csv(tmp_path,text),deduplicate_by=['案号'])


def test_unknown_not_zero_and_wrong_snapshot_not_certified(tmp_path):
    p=csv(tmp_path,'案号,负责人,阶段\nA,甲,在办\nB,乙,\n')
    q=query_table(p,filters=[{'field':'阶段','op':'is_null'}],group_by=['负责人'],aggregates=[{'field':'案号','op':'nunique','alias':'未知阶段案件数'}])
    assert q['rows']==[['乙',1]] and q['aggregation_proof']['groups'][0]['metrics'][0]['unique_values']==['B']
    r=fixture('uploaded-table-analysis',text='统计结果[S1]')
    r['evidence']=[{'id':'S1','type':'file','title':p.name,'location':'CSV行','revision':q['aggregation_proof']['file_sha256'],**q}]
    assert state(r)=='VERIFIED'
    r['evidence'][0]['revision']='another-snapshot'
    assert state(r)=='INCOMPLETE'
    r['evidence'][0]['revision']=q['aggregation_proof']['file_sha256'];r['evidence'][0]['rows']=[['乙',9]]
    assert state(r)=='INCOMPLETE'


def test_program_diff_complete_and_hash_bound_without_technical_inference(tmp_path, monkeypatch):
    import asyncio
    asyncio.run(_program_diff_complete_and_hash_bound_without_technical_inference(tmp_path,monkeypatch))


def test_chart_numbers_and_group_binding_must_match_program(tmp_path):
    from tools.chart_utils import build_chart_items
    q=query_table(csv(tmp_path,'案号,负责人\nA,甲\nB,乙\n'),group_by=['负责人'],aggregates=[{'field':'案号','op':'nunique','alias':'案件数'}])
    r=fixture('uploaded-table-analysis','统计并画柱状图',text='程序统计[S1]')
    r['evidence']=[{'id':'S1','type':'file','title':'cases.csv','location':'CSV','revision':q['aggregation_proof']['file_sha256'],**q}]
    r['charts']=build_chart_items(r['evidence'],'柱状图')
    assert state(r)=='VERIFIED'
    r['charts'][0]['chart']['series'][0]['data'][0]=999
    assert state(r)=='INCOMPLETE'
    r['charts']=build_chart_items(r['evidence'],'柱状图')
    r['evidence'][0]['rows'][0][0]='串组'
    assert state(r)=='INCOMPLETE'


def test_complete_physical_text_is_locatable_without_fabricating_pdf_pages():
    r=fixture('public-reference')
    # This helper only tests literal source binding; public profile still needs web.
    from tools.product_boundary import _sources
    text={'id':'S1','type':'file','title':'input.md','revision':'f'*64,
          'content':'标题\n\n正文','content_complete':True}
    source=_sources([text],{'S1'})[0]
    assert source['valid'] and source['location']['line_count']==3
    assert 'page' not in source['location']
    text['content_complete']=False
    assert not _sources([text],{'S1'})[0]['valid']


def test_database_projection_uses_real_generation_and_complete_returned_rows():
    from tools.product_boundary import _sources
    item={'id':'S1','type':'database','title':'案件信息','casework':True,'columns':['字段'],'rows':[['值']],
          'coverage':{'complete_scan':True,'issues':[],'roots':[{'id':'root','state':'ready','generation':1,'mirror_generation':1}]},
          'completeness':{'scope':'department_published_view','returned_rows':1}}
    assert _sources([item],{'S1'})[0]['valid']
    item['coverage']['roots'][0]['mirror_generation']=0
    assert not _sources([item],{'S1'})[0]['valid']
    item['coverage']['roots'][0]['mirror_generation']=1
    item['completeness']['fetch_truncated']=True
    assert not _sources([item],{'S1'})[0]['valid']


@pytest.mark.parametrize('operation,expected', [('sum','0.3'),('mean','0.15'),('min','0.1'),('max','0.2')])
def test_general_program_aggregates_are_not_forced_to_human_review(tmp_path,operation,expected):
    p=csv(tmp_path,'金额\n0.1\n0.2\n')
    # min/max retain the actual lexical column type, while sum/mean use Decimal.
    q=query_table(p,aggregates=[{'field':'金额','op':operation,'alias':'结果'}])
    assert q['rows']==[[expected]]
    r=fixture('uploaded-table-analysis',text='统计结果[S1]')
    r['evidence']=[{'id':'S1','type':'file','title':p.name,'location':'CSV','revision':q['aggregation_proof']['file_sha256'],**q}]
    assert state(r)=='VERIFIED'


def test_host_responsibility_notice_is_not_misrepresented_as_a_source_quote():
    from tools.evidence_refs import citation_issues
    c=request_contract('找同样机制的参考案',{'profile':'disclosure-search'})
    with scope(c):
        text=publication_notice('')
    # Real first auxiliary S14 was falsely blocked because the host used >.
    assert not text.startswith('>')
    assert not any('原文引句缺少' in issue for issue in citation_issues(text,[]))
    sources=[{'id':'S1','type':'file','title':'source.md','snippet':'原件实际内容','location':'第1行'}]
    assert not any('原文引句缺少' in issue for issue in citation_issues(text,sources))
    assert any('原文引句缺少' in issue for issue in citation_issues('> 这段来自资料的实际引文长度超过十二字',sources))


def test_dedup_proof_does_not_allow_count_of_a_nullable_non_key_field(tmp_path):
    p=csv(tmp_path,'案号,负责人\nA,\nB,甲\n')
    q=query_table(p,deduplicate_by=['案号'],aggregates=[{'field':'负责人','op':'count','alias':'案件数'}])
    e={'id':'S1','title':p.name,'revision':q['aggregation_proof']['file_sha256'],**q}
    assert dedup_aggregation_issues('按案号去重统计案件数量',[e])










@pytest.fixture
def table_sources(tmp_path):
    import hashlib
    p=csv(tmp_path,'案号,负责人,阶段\nUNIT-A,甲,在办\nUNIT-B,甲,已交局\nUNIT-C,乙,在办\nUNIT-C,乙,在办\nUNIT-D,甲,在办\nUNIT-E,丙,\n')
    digest=hashlib.sha256(p.read_bytes()).hexdigest()
    raw=query_table(p)
    aggregate=query_table(p,filters=[{'field':'阶段','op':'eq','value':'在办'}],group_by=['负责人'],
        aggregates=[{'field':'案号','op':'nunique','alias':'在办案件数'}],deduplicate_by=['案号'])
    sources=[{'id':sid,'type':'file','title':p.name,'revision':digest,'location':'工作表 CSV',**data}
             for sid,data in [('S1',raw),('S2',aggregate)]]
    return sources


def test_program_publication_separates_known_other_state_from_unknown(table_sources):
    from tools.product_boundary import render_statistics
    query='按案号去除完全重复行，只统计阶段为在办的案件，按负责人统计并画柱状图，未知阶段另列。'
    with scope(request_contract(query,{'profile':'uploaded-table-analysis'})):
        text,audit,error=render_statistics('错误草稿：把非在办都称作未知。[S2]',table_sources,query)
    assert not error and audit
    assert '完整唯一主键集合' in text and 'UNIT-E' in text
    assert 'UNIT-B' not in text.split('## 未知状态',1)[1]
    assert '物理行：第7行' in text
    from tools.evidence_refs import citation_issues
    assert not citation_issues(text,table_sources)
    altered=copy.deepcopy(table_sources);altered[0]['truncated']=True
    with scope(request_contract(query,{'profile':'uploaded-table-analysis'})):
        assert render_statistics('[S2]',altered,query)[2]


def test_existing_facts_token_requires_exact_source_fingerprint(table_sources):
    from tools.product_boundary import render_statistics
    from tools.answer_contracts import fact_token
    query='按案号去重统计在办案件，未知阶段另列。'
    with scope(request_contract(query,{'profile':'uploaded-table-analysis'})):
        text,audit,error=render_statistics(fact_token(table_sources[1]),table_sources,query)
        assert not error and audit and 'UNIT-E' in text
        assert render_statistics('{{facts:S2:'+ '0'*64 +'}}',table_sources,query)[2]


def test_unbacked_mechanism_replacement_is_rejected():
    from tools.reflect_check import hard_check
    sources=[{'id':'S1','type':'file','title':'base.md','revision':'a'*64,'location':'第1行',
              'content_complete':True,'content':'过期缓存保留七天。','snippet':'过期缓存保留七天。'},
             {'id':'S2','type':'file','title':'revision.md','revision':'b'*64,'location':'第1行',
              'content_complete':True,'content':'权限撤销时立即失效相关缓存。','snippet':'权限撤销时立即失效相关缓存。'}]
    result={'report':'失效规则由七天保留改为权限撤销立即失效，触发条件与时机均改变。[S1][S2]',
            'query':'逐条比较两份技术说明，给两侧原句。','evidence':sources,'required_sources':[],'called_sources':[]}
    assert any('越界' in issue for issue in hard_check(result))


def test_program_facts_cannot_certify_changed_evidence_or_override_failure():
    from casework.store import digest as evidence_digest
    r=fixture('case-materials','核对负责人',text='负责人为甲。[S1]')
    r['evidence']=[{'id':'S1','type':'file','title':'record.txt','revision':'a'*64,'location':'第1行','snippet':'负责人为甲。'}]
    ref=r['events'][1]['data'];ref['verification']['checks']=[{'criterion':'task_completion','status':'passed'}]
    ref['business_check']={'status':'passed','source_binding':{'status':'passed'},'issues':[],
        'report_sha256':report_hash(r['result']),'evidence_sha256':evidence_digest(r['evidence']),
        'claims':[{'subject':'UNIT-A','predicate':'owner','value':'甲','verified':True}]}
    assert assess_result(r)['facts']['basis']=='sealed_program_contract'
    altered=copy.deepcopy(r);altered['evidence'][0]['snippet']='负责人为乙。'
    assert assess_result(altered)['facts']['status']=='unverified'
    ref['verification']['checks'].append({'criterion':'evidence_support','status':'failed'})
    assert assess_result(r)['facts']['status']=='failed'
