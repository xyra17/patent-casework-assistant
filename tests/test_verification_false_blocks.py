"""Paired acceptance/rejection checks; no new evaluation questions or gold here."""
import copy
import json

from tools.answer_contracts import contract_issues, fact_block, render_answer
from tools.content_verification import archive_absence_issues
from tools.evidence_refs import citation_issues
from tools.statistics_check import statement_units, table_claims_for, validate_claims


def test_quoted_period_keeps_its_real_source_but_a_fabricated_quote_fails():
    quote = '不是公开申请文件，没有真实申请号、公开号、申请人或法律状态。'
    items = [{'id': 'S1', 'type': 'knowledge', 'casework': True, 'snippet': quote}]
    good = f'申请文件写明：“{quote}”[S1]（第1页）'
    assert archive_absence_issues(good, items) == []
    assert citation_issues(good, items) == []
    bad = good.replace('没有真实申请号', '具有真实申请号')
    assert citation_issues(bad, items)
    assert archive_absence_issues('申请文件没有实测结果。', items)
    assert archive_absence_issues('全文没有任何实测结果。[S1]', items)
    assert archive_absence_issues('本案没有立案原件。[S1]', items)
    assert archive_absence_issues('该案档案中只找到一份申请文件。', items)
    assert not archive_absence_issues('本次未取得本案立案原件，尚不能判断它是否存在。', items)


def test_duplicate_selector_expansion_only_removes_supported_adjacent_quotes():
    quote = '不是公开申请文件，没有真实申请号、公开号、申请人或法律状态。'
    items = [{'id': 'S1', 'type': 'knowledge', 'snippet': quote}]
    rendered, audit = render_answer(f'“{quote}”“{quote}”[S1][S1]', items)
    assert rendered == f'“{quote}”[S1]'
    assert audit[-1]['kind'] == 'duplicate_bound_quote'
    fake = '申请文件已实际运行，并证明所有任务的准确率均为百分之百。'
    rendered, _ = render_answer(f'“{fake}”“{fake}”[S1]', items)
    assert rendered.count(fake) == 2
    assert citation_issues(rendered, items)


def due_item():
    return {'id': 'S2', 'type': 'database',
            'columns': ['所编号', '代理师', '初稿期限／所限', '客户送审期限', '向局提交期限'],
            'rows': [['TEST-001', '甲', '2026-10-20', '2026-10-27', '2026-11-03']],
            'answer_contract': {'kind': 'due_case_list', 'version': 1, 'complete_snapshot': True,
                'due_field': 'draft_due_date', 'due_status': 'overdue', 'as_of': '2026-11-05', 'total': 1}}


def test_deadline_contract_accepts_layout_but_rejects_changed_field_or_missing_date():
    item = due_item()
    block = fact_block(item)
    reflowed = block.replace(' | ', '|').replace('| --- |', '|---|')
    assert not contract_issues(reflowed, [item])
    assert contract_issues(reflowed.replace('2026-10-27', '2026-10-28'), [item])
    assert contract_issues(reflowed.replace('客户送审期限', '实际交局日期'), [item])
    assert contract_issues(reflowed.replace('|2026-10-27', ''), [item])
    assert contract_issues(reflowed.replace('2026-11-05', '2026-11-06'), [item])
    rendered, _ = render_answer(reflowed, [item])
    assert rendered.count('TEST-001') == 1
    empty = copy.deepcopy(item)
    empty.update(rows=[])
    empty['answer_contract']['total'] = 0
    assert fact_block(empty) == ''
    assert contract_issues('此完整筛选的结果为零件。[S2]', [empty]) == []


def monthly_source(column='received_count', field='received_date'):
    from tools.casework_tools import _metric_contract
    return {'id': 'S3' if column == 'received_count' else 'S4', 'type': 'database',
        'columns': [column], 'rows': [[2]], 'statistics': {'version': 1, 'dimensions': [],
            'metrics': [_metric_contract(column, 0, {'status': 'bounded', 'field': field,
                'start': '2026-05-01', 'end': '2026-06-01'}, [])]}}


def table_verdict(report, items):
    units = statement_units(report)
    claims = table_claims_for(units, items)
    return validate_claims(report, items, claims, units=units)


def test_generic_quantity_column_uses_heading_not_value_to_select_metric():
    good = '## 按实际收案日期归入月份\n\n| 月份 | 数量 |\n| --- | --- |\n| 2026年5月 | 2件 [S3] |'
    source = monthly_source()
    assert table_verdict(good, [source])['status'] == 'passed'
    assert table_verdict(good.replace('2件', '3件'), [source])['status'] == 'failed'
    assert table_verdict(good.replace('2026年5月', '2026年6月'), [source])['status'] == 'failed'
    ambiguous = good.replace('按实际收案日期归入月份', '实际收案与实际交局')
    assert table_verdict(ambiguous, [source, monthly_source('filed_count', 'actual_filed_date')])['status'] == 'failed'
    unknown = copy.deepcopy(source)
    unknown['statistics']['metrics'][0]['definition_verified'] = False
    assert table_verdict(good, [unknown])['status'] == 'failed'


def test_missing_date_count_does_not_relabel_conflicts_as_missing():
    from tools.casework_tools import _missing_received_cases
    rows = [{'case_id':'A','received_date':None,'conflict_fields':[]},
            {'case_id':'B','received_date':None,'conflict_fields':['received_date']},
            {'case_id':'C','received_date':'2026-01-01','conflict_fields':[]},
            {'case_id':'D'}]
    assert _missing_received_cases(rows) == ['A']


def test_full_active_table_requires_full_same_scope_and_excludes_completed_cases():
    from tools.answer_contracts import _full_active_block
    due = due_item()
    due['snippet'] = json.dumps({'owner':'甲','due_field':'draft_due_date','due_status':'overdue'})
    complete = copy.deepcopy(due)
    complete.update(casework=True, answer_contract=None, id='S5',
                    snippet=json.dumps({'owner':'甲'}), completeness={'query_rows':2,'has_more':False})
    complete['columns'] += ['案件状态','实际交局日期','交局已核实']
    complete['rows'] = [complete['rows'][0]+['客户审核',None,False],
                        ['TEST-002','甲','2026-10-20','2026-10-27','2026-11-03','已交局','2026-11-01',True]]
    block = _full_active_block([complete], {'overdue':due})
    assert 'TEST-001' in block and 'TEST-002' not in block
    different = copy.deepcopy(complete)
    different['snippet'] = json.dumps({'owner':'乙'})
    assert not _full_active_block([different], {'overdue':due})
    partial = copy.deepcopy(complete)
    partial['completeness']['has_more'] = True
    assert not _full_active_block([partial], {'overdue':due})


def test_workload_counts_do_not_replace_required_case_groups():
    from tools.answer_contracts import _complete_due_sources
    from tools.casework_tools import _metric_contract
    cases = due_item()
    cases.update(id='S8', casework=True, answer_contract=None, snippet=json.dumps({'owner':'甲'}),
                 completeness={'query_rows':4,'has_more':False})
    cases['columns'] += ['案件状态','实际交局日期','交局已核实']
    cases['rows'] = [cases['rows'][0]+['客户审核',None,False],
        ['NEAR','甲','2026-11-09','2026-11-16','2026-11-23','初稿撰写',None,False],
        ['FAR','甲','2026-12-01','2026-12-08','2026-12-15','初稿撰写',None,False],
        ['UNKNOWN','甲',None,'2026-12-08','2026-12-15','初稿撰写',None,False]]
    workload = {'id':'S9','type':'database','columns':['代理师','在办案件数量'],'rows':[['甲',4]],
        'statistics':{'version':1,'metrics':[_metric_contract('active_count',1,
            {'status':'bounded','field':'as_of','start':'2026-11-05','end':'2026-11-06'},[])],
            'dimensions':[{'column':'owner_name','index':0,'label':'代理师'}]},
        'answer_contract':{'kind':'workload','version':1,'as_of':'2026-11-05',
            'scope':'甲的已知在办','active_definition':'排除已核实交局','deadline_definition':'未来两周含起日不含末日'}}
    query = '以2026-11-05为基准，列出甲的全部在办案号和三项计划期限，按初稿期限列逾期、未来14天及未知案号。'
    sources = _complete_due_sources([cases, workload], query)
    assert {key:len(item['rows']) for key,item in sources.items()} == {'overdue':1,'next14':1,'unknown':1}
    report, _ = render_answer('只给计数，没有案号', [cases, workload], query)
    assert all(ident in report for ident in ('TEST-001','NEAR','FAR','UNKNOWN'))
    assert '2026-12-15' in report
    assert not contract_issues(report, [cases, workload])
    truncated = copy.deepcopy(cases)
    truncated['completeness']['has_more'] = True
    assert not _complete_due_sources([truncated,workload], query)
    assert not _complete_due_sources([cases,workload], query.replace('甲','乙'))
    from tools.task_evidence_checks import task_evidence_issues
    assert not task_evidence_issues(query, [cases, workload])
    # The explicit base date and complete case rows are sufficient themselves.
    assert _complete_due_sources([cases], query).keys() == sources.keys()


def test_monthly_counts_require_the_requested_month_and_actual_event_fields():
    from tools.task_evidence_checks import task_evidence_issues
    from tools.casework_tools import _metric_contract
    query = '统计2026-05和2026-06每月实际收案与已核实实际交局数量，不能以计划期限代替。'
    assert task_evidence_issues(query, [], internal_casework=True)
    sources = []
    for month, end in [('2026-05','2026-06-01'),('2026-06','2026-07-01')]:
        for name,field in [('received_count','received_date'),('filed_count','actual_filed_date')]:
            sources.append({'statistics':{'metrics':[_metric_contract(name,0,{
                'status':'bounded','field':field,'start':month+'-01','end':end},[])]}})
    assert not task_evidence_issues(query, sources, internal_casework=True)
    assert task_evidence_issues(query, sources[:-1], internal_casework=True)
    wrong = copy.deepcopy(sources)
    wrong[0]['statistics']['metrics'][0]['time']['field'] = 'draft_due_date'
    assert task_evidence_issues(query, wrong, internal_casework=True)
    assert not task_evidence_issues('请统计公开资料中的2026-05各月收案数量', [{'type':'web'}])
    # A base date plus filing exclusions must not create a monthly requirement.
    deadline = '以2026年5月6日为基准，列出初稿逾期及未来14天案号和数量，排除已核实实际交局。'
    assert not any('月度' in issue for issue in task_evidence_issues(deadline, [], internal_casework=True))


def test_iso_month_is_scope_not_a_year_and_negative_number():
    from tools.statistics_check import database_statement_units, reported_time
    assert reported_time('2026-05') == [{'start':'2026-05-01','end':'2026-06-01'}]
    item = monthly_source()
    assert not database_statement_units('2026-05、2026-06 均无已核实实际交局记录。', [item])
    assert table_verdict('## 实际收案\n|月份|数量|\n|---|---|\n|2026-05|2件 [S3]|', [item])['status'] == 'passed'


def test_old_global_contract_is_retained_but_not_forced_into_client_answer():
    from tools.answer_contracts import bind_task_scope
    query = '统计客户甲2026-05每月实际收案数量。'
    global_metric = monthly_source()
    client_metric = copy.deepcopy(global_metric)
    client_metric.update(id='S5')
    client_metric['statistics']['metrics'][0]['filters'] = [{'field':'client_alias','value':'客户甲'}]
    old = {'id':'S6','type':'database','casework':True,'snippet':json.dumps({'input_evidence_ids':['S3']}),
           'answer_contract':{'kind':'monthly_report','version':1,'start_month':'2026-05','end_month':'2026-05',
                              'body':'全库月度事实表'}}
    new = {'id':'S7','type':'database','casework':True,'answer_contract':{
        'kind':'monthly_report','version':1,'start_month':'2026-05','end_month':'2026-05',
        'client':'客户甲','body':'客户甲月度事实表'}}
    original = [global_metric, client_metric, old, new]
    projected = bind_task_scope(query, original)
    assert len(projected) == len(original)
    assert projected[2]['answer_contract_applicable'] is False
    assert 'answer_contract_applicable' not in old
    report, _ = render_answer('待整理', projected, query)
    assert '客户甲月度事实表' in report and '全库月度事实表' not in report
    # Wrong claims the model itself wrote remain visible and are rejected.
    wrong = '2026年5月实际收案数量2件 [S3]。'
    report, _ = render_answer(wrong, projected, query)
    assert wrong in report
    units = statement_units(wrong)
    assert validate_claims(wrong, projected, [{'unit_id':0,'metric_text':'实际收案数量',
        'value_text':'2件'}], units=units)['status'] == 'failed'


def test_generic_prose_quantity_uses_its_metric_heading():
    report = '## 2026-05 实际收案\n\n数量：2 件 [S3]。'
    claim = [{'unit_id':0,'metric_text':'数量','value_text':'2 件'}]
    assert validate_claims(report, [monthly_source()], claim)['status'] == 'passed'
    assert validate_claims(report.replace('2 件','3 件'), [monthly_source()],
        [{'unit_id':0,'metric_text':'数量','value_text':'3 件'}])['status'] == 'failed'
    from tools.casework_tools import _metric_contract
    missing = {'id':'S8','type':'database','rows':[[1]],'statistics':{'dimensions':[],
        'metrics':[_metric_contract('missing_received_count',0,{'status':'unbounded'},[])]}}
    text = '## 实际收案日期缺失\n\n数量：1 件 [S8]。'
    assert validate_claims(text, [monthly_source(),missing],
        [{'unit_id':0,'metric_text':'数量','value_text':'1 件'}])['status'] == 'passed'
    assert validate_claims(text.replace('1 件','2 件'), [monthly_source(),missing],
        [{'unit_id':0,'metric_text':'数量','value_text':'2 件'}])['status'] == 'failed'


def test_no_verified_filing_is_not_misread_as_all_verified():
    from tools.date_completeness import date_completeness_issues
    proof = {'id':'S1','rows':[['A','未核实'],['B','未核实']],
        'completeness':{'has_more':False,'fetch_truncated':False},
        'date_completeness':{'predicate':'unknown_filed','complete':True,'stage_scope':'all',
            'matched_count':2,'population_count':2,'time_scope':'unassigned',
            'filters':[{'field':'client_alias','value':'客户甲'}]}}
    report = '统计范围：客户甲。\n2026年5月均未见已核实实际交局记录。[S1]'
    assert not date_completeness_issues(report, [proof])
    partial = copy.deepcopy(proof)
    partial['rows'] = partial['rows'][:1]
    partial['date_completeness']['matched_count'] = 1
    assert date_completeness_issues(report, [partial])
    assert date_completeness_issues('客户甲的所有案件均已核实实际交局日期。[S1]', [proof])
    assert date_completeness_issues(report, [{**proof,'task_scope_compatible':False}])


def test_monthly_event_details_are_the_actual_counted_identities_and_dates():
    from tools.casework_tools import _monthly_event_rows
    result = {'cases':[{'case_key':'A','case_id':'TEST-A','received_date':'2026-05-03',
        'actual_filed_date':'2026-05-21','filing_verified':True}],
        'rows':[{'month':'2026-05','received_count':1,'filed_count':1,
            'received_case_keys':['A'],'filed_case_keys':['A']}]}
    assert _monthly_event_rows(result) == [['2026-05','实际收案','TEST-A','2026-05-03'],
        ['2026-05','已核实实际交局','TEST-A','2026-05-21']]
    import pytest
    bad = copy.deepcopy(result)
    bad['cases'][0]['filing_verified'] = False
    with pytest.raises(ValueError):
        _monthly_event_rows(bad)
    bad = copy.deepcopy(result)
    bad['rows'][0]['received_count'] = 2
    with pytest.raises(ValueError):
        _monthly_event_rows(bad)


def test_equal_empty_results_with_different_date_scopes_keep_distinct_ids():
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from api import evidence, storage
    with tempfile.TemporaryDirectory() as folder, patch.object(storage, '_DB_PATH', Path(folder)/'app.db'):
        storage.init_db()
        storage.create_run('scope-proof', '问题', '问题', False)
        evidence.reset('scope-proof')
        base = {'type':'database','title':'日期冲突候选','rows':[],'columns':['所编号'],
                'date_completeness':{'predicate':'date_conflicts','filters':[],'complete':True}}
        global_id = evidence.add('scope-proof', base)
        filtered = copy.deepcopy(base)
        filtered['date_completeness']['filters'] = [{'field':'client_alias','value':'客户甲'}]
        client_id = evidence.add('scope-proof', filtered)
        assert global_id != client_id
        assert evidence.add('scope-proof', filtered) == client_id
        evidence.clear('scope-proof')


def test_source_complete_candidate_outranks_earlier_incomplete_low_error_candidate():
    from tools.answer_repair import rank_candidate
    missing = rank_candidate({}, ['missing scope'], None, ['月度客户来源缺失'])
    later = rank_candidate({}, ['引用待核验','表达待核验'], None, [])
    assert later < missing
    assert rank_candidate({}, [], None, []) < later


def test_pure_monthly_delivery_renders_the_applicable_structured_body():
    item = monthly_source()
    item['statistics']['metrics'][0]['filters'] = [{'field':'client_alias','value':'客户甲'}]
    body = '统计范围：客户＝客户甲。\n| 月份 | 事件指标 | 所编号 | 实际日期 | 来源 |\n| --- | --- | --- | --- | --- |\n|2026-05|实际收案|A|2026-05-03|[S3]|\n实际收案日期缺失数量0件。'
    contract = {'id':'S6','type':'database','casework':True,'answer_contract':{
        'kind':'monthly_report','version':1,'client':'客户甲',
        'start_month':'2026-05','end_month':'2026-05','body':body}}
    query = '统计客户甲2026-05每月实际收案，给完整案号及对应实际日期，另列收案日期缺失。'
    report, audit = render_answer('模型重复抄写了其他范围的数字', [item,contract], query)
    assert report == body
    assert any(entry['kind']=='monthly_structured_delivery' for entry in audit)
    wrong = copy.deepcopy(item)
    wrong['statistics']['metrics'][0]['definition_verified'] = False
    _, audit = render_answer('原回答', [wrong,contract], query)
    assert not any(entry['kind']=='monthly_structured_delivery' for entry in audit)
