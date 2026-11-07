"""Regressions for source planning, immutable facts and answer presentation."""

from agent.main_agent import TaskPlan, _explicitly_forbidden_sources, _normalize_plan
from tools.answer_contracts import contract_issues, render_answer
from tools.answer_repair import remove_repeated_paragraphs, repair_plan
from tools.evidence_refs import normalize_citations
from tools.reflect_check import hard_check


def test_monthly_fact_block_remains_immutable_after_citation_normalization():
    item = {'id': 'S1', 'type': 'database', 'answer_contract': {
        'version': 1, 'kind': 'monthly_report',
        'body': '| 月份 | 实际收案数量 |\n| --- | --- |\n| 2026年9月 | 10 [S1] |\n\n日期冲突：未查到。[S1]'}}
    draft = '2026年9月的案量如下。'
    normalized = normalize_citations(draft, [item])
    rendered, audit = render_answer(normalized, [item], draft)
    assert not contract_issues(rendered, [item])
    assert sum(x.get('inserted_missing_block', False) for x in audit) == 1
    assert render_answer(rendered, [item], draft)[0] == rendered


def test_repeated_revisions_drop_exact_long_paragraphs_only():
    repeated = '本案从原件确认处理步骤并给出对应位置。[S2] ' * 5
    report = '## 匹配点\n\n' + repeated + '\n\n## 结论\n\n' + repeated
    result = remove_repeated_paragraphs(report)
    assert result.count(repeated) == 1
    assert '## 匹配点' in result and '## 结论' in result


def test_explicit_internal_application_file_restores_knowledge_source():
    query = ('请查 AI-2026-032 的负责人和期限，再从申请文件说明核心步骤。'
             '只用内部案件信息和文件，直接回答，不生成文件。')
    assert 'knowledge' not in _explicitly_forbidden_sources(query)
    original = TaskPlan(task_type='report', require_report=True, require_pdf=False,
                        required_sources=['db_query'], forbidden_sources=['knowledge', 'web_search'])
    plan = _normalize_plan(original, query)
    assert set(plan.required_sources) == {'db_query', 'knowledge'}
    assert 'knowledge' not in plan.forbidden_sources
    assert not plan.require_report
    assert any(issue.startswith('申请文件正文未取得') for issue in hard_check({
        'query': query, 'report': '申请文件正文未取得。[S1]',
        'evidence': [{'id': 'S1', 'type': 'database', 'title': '案件信息'}],
        'called_sources': ['db_query', 'knowledge'],
        'required_sources': ['db_query', 'knowledge']}))


def test_known_case_stage_cannot_be_called_unknown_filing_status():
    query = '检查初稿期限，已核实交局的排除，未知状态单列。'
    item = {'id': 'S1', 'type': 'database', 'columns': ['所编号', '案件状态', '实际交局日期', '交局已核实'],
            'rows': [['AI-2026-007', '初稿撰写', None, False]],
            'completeness': {'has_more': False, 'fetch_truncated': False}}
    issues = hard_check({'query': query, 'report': '交局状态未知：1件。[S1]', 'evidence': [item]})
    assert any(issue.startswith('办理阶段与交局核实状态混淆') for issue in issues)


def test_review_of_wrong_table_citation_repairs_only_that_row():
    report = ('已核对摘要。[S2]\n\n| 要求 | 说明 | 来源 |\n|---|---|---|\n'
              '| 重叠语音 | 原文已写 | [S36] |\n| 可回听 | 原文已写 | [S19] |\n\n结论保留。')
    result = {'report': report, 'evidence': [], 'repair_hard_issues': [], 'verification': {'checks': [
        {'status': 'failed', 'action': 'revise_answer',
         'detail': '匹配表一行所引 S36 只讲验收记录，与重叠语音不匹配。',
         'answer_quote': report[:report.index('| 可回听')]}
    ]}}
    plan = repair_plan(result, ['证据支持不足'])
    assert plan is not None
    assert len(plan['units']) == 1
    assert plan['units'][0]['original'] == '| 重叠语音 | 原文已写 | [S36] |'
