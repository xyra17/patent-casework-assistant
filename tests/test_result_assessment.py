import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.result_assessment import assess_result, report_hash


def run_fixture(*, checks=None, passed=True, degraded=False, text='答案正文'):
    return {'status': 'completed', 'result': text, 'degraded': degraded, 'events': [
        {'event': 'reflection', 'data': {'round': 1, 'candidate_report_ref': report_hash(text),
            'verification': {'checks': checks or []}}},
        {'event': 'quality_summary', 'data': {'passed': passed, 'returned_round': 1,
            'stop_reason': 'passed' if passed else 'max_rounds', 'issues': []}},
    ]}


def checks(facts='passed', completeness='passed'):
    return [{'criterion': 'evidence_support', 'status': facts},
            {'criterion': 'task_completion', 'status': completeness}]


def test_fact_review_can_pass_while_task_is_incomplete_and_delivery_is_partial():
    result = assess_result(run_fixture(checks=checks(completeness='failed'), passed=False, degraded=True))
    assert result['facts']['status'] == 'passed'
    assert result['completeness']['status'] == 'failed'
    assert result['delivery']['status'] == 'partial'
    assert result['independent_review'] is False


def test_gate_failure_without_content_review_is_unknown_not_factually_wrong():
    run = run_fixture(passed=False, degraded=True)
    run['events'][0]['data'].update(hard_issues=['引用格式错误'], verification={'status': 'not_run', 'checks': []})
    result = assess_result(run)
    assert result['facts']['status'] == result['completeness']['status'] == 'unverified'
    assert result['delivery']['status'] == 'partial'


def test_unknown_review_is_not_converted_to_failed_or_passed():
    result = assess_result(run_fixture(checks=checks(facts='unverified'), passed=False, degraded=True))
    assert result['facts']['status'] == 'unverified'
    assert result['completeness']['status'] == 'passed'


def test_fallback_text_cannot_inherit_a_candidate_pass():
    run = run_fixture(checks=checks(), passed=False, degraded=True)
    run['result'] += '\n## 未完成部分\n尚未完成核验'
    result = assess_result(run)
    assert result['facts']['status'] == result['completeness']['status'] == 'unverified'
    assert result['review_round'] is None


def test_selected_round_is_used_instead_of_last_round():
    run = run_fixture(checks=checks())
    run['events'].insert(1, {'event': 'reflection', 'data': {'round': 2,
        'candidate_report_ref': report_hash('另一个版本'), 'verification': {'checks': checks('failed', 'failed')}}})
    result = assess_result(run)
    assert result['review_round'] == 1
    assert result['facts']['status'] == 'passed'


def test_legacy_completed_or_absent_review_is_not_an_accuracy_claim():
    result = assess_result({'status': 'completed', 'result': '旧答案', 'degraded': False})
    assert result['facts']['status'] == 'unverified'
    assert result['completeness']['status'] == 'unverified'
    assert result['delivery']['status'] == 'unverified'


def test_running_cannot_reuse_an_old_review():
    run = run_fixture(checks=checks())
    run['status'] = 'running'
    result = assess_result(run)
    assert result['facts']['status'] == 'unverified'
    assert result['delivery']['status'] == 'running'


def test_storage_read_exposes_the_same_three_axes_without_schema_migration():
    from api import storage
    run = run_fixture(checks=checks(completeness='failed'), passed=False, degraded=True)
    with tempfile.TemporaryDirectory() as folder, patch.object(storage, '_DB_PATH', Path(folder) / 'app.db'):
        storage.init_db()
        storage.create_run('axes', '问题', '问题', False)
        for event in run['events']:
            storage.append_event('axes', event)
        storage.append_event('axes', {'event': 'task_result', 'data': {'result': run['result'], 'degraded': True}})
        stored = storage.get_run('axes')['result_assessment']
        assert stored['facts']['status'] == 'passed'
        assert stored['completeness']['status'] == 'failed'
        assert stored['delivery']['status'] == 'partial'
        execution_id = storage.get_run('axes')['execution_id']
        storage.create_run('axes', '新问题', '新问题', False)
        archived = storage.get_execution('axes', execution_id)['result_assessment']
        assert archived == stored
        assert storage.get_run('axes')['result_assessment']['facts']['status'] == 'unverified'


def test_c05_quoted_source_statement_is_not_an_uncited_absence_claim():
    from tools.content_verification import archive_absence_issues
    quote = '不是公开申请文件，没有真实申请号、公开号、申请人或法律状态。'
    evidence = [{'id': 'S11', 'type': 'knowledge', 'casework': True, 'snippet': quote}]
    report = f'该申请文件首页自述：“{quote}”“{quote}”[S11][S11]（第1页·字符65–95）'
    assert archive_absence_issues(report, evidence) == []
    assert archive_absence_issues('该申请文件没有任何实验记录。', evidence)
