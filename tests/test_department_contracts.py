"""Mechanism regressions: routes, source authority and corrupted fact bindings."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_department_casework as fixtures
from tests.unit_fixtures import route_cases
ROOT = fixtures.ROOT


class ContractTests(unittest.TestCase):
    setUp = fixtures.DepartmentCaseworkTests.setUp
    tearDown = fixtures.DepartmentCaseworkTests.tearDown

    def test_whole_business_pack_has_one_route_owner(self):
        from casework.routing import select_route, apply_route
        from agent.main_agent import TaskPlan, PlannerPlan, _explicitly_forbidden_sources
        pack = route_cases()
        with patch('casework.workflow.resolve', AsyncMock(side_effect=AssertionError('covered contract cannot use model vote'))):
            for c in pack['cases']:
                operation = c['expected']['workflow']
                d = asyncio.run(select_route(c['prompt'], _explicitly_forbidden_sources(c['prompt']),allow_semantic=False))
                self.assertEqual(d.status, 'matched', (c['id'], d.audit()))
                self.assertEqual(d.request.operation, operation)
                for field, value in c['expected'].get('request_parameters', {}).items():
                    self.assertEqual(getattr(d.request, field), value, (c['id'], field))
                # Simulate a general planner disagreement. Fixed action owns the
                # minimal requirements, while the forbidden cap remains intact.
                plan = TaskPlan(task_type='qa', require_report=False, require_pdf=False, required_sources=[])
                updated = apply_route(plan, d)
                self.assertEqual(updated.department_request, d.request)
                self.assertTrue(updated.required_sources)
        self.assertNotIn('department_request', PlannerPlan.model_json_schema()['properties'])

    def test_unsupported_conditions_are_never_silently_dropped(self):
        from casework.routing import select_route
        from agent.main_agent import _explicitly_forbidden_sources
        negatives = [
            '不联网。查客户不存在的三方会议。',
            '不联网。按代理师统计2026年9月收案数量。',
            '不联网。统计2026年9月和10月收案环比。',
            '不联网。查2025年9月客户甲的三方会议。',
            '不联网。为向量数据库新案推荐代理师并自动分案。',
            '不联网。对比DEMO-2026-013和DEMO-2026-019的权利要求。',
            '不联网。查未来30天初稿到期的案件。',
            '不联网。查客户乙的历史向量数据库案件正文。',
            '不联网。只查PDF申请文件里写过HNSW的历史案件正文。',
            '联网查相似历史案件正文，再查公开论文。',
            '不联网。找历史向量数据库案件正文，不要已完成的。',
            '不联网。查代理师王某的逾期初稿案件。',
            '不联网。查询量子领域的初稿逾期案件。',
        ]
        for query in negatives:
            d = asyncio.run(select_route(query, _explicitly_forbidden_sources(query), allow_semantic=False))
            self.assertNotEqual(d.status, 'matched', (query, d.audit()))
        for query, forbidden in [('查DEMO-2026-013的案件信息和技术正文', ['knowledge']),
                                  ('为向量数据库案件推荐代理师', ['db_query'])]:
            self.assertEqual(asyncio.run(select_route(query, forbidden,allow_semantic=False)).status, 'blocked')

    def test_supported_session_does_not_call_general_planner_or_open_agent(self):
        from agent import main_agent
        from api import context
        query = route_cases()['cases'][0]['prompt']
        result = {'report': '当前资料未核实。', 'called_sources': ['knowledge'], 'evidence': [],
                  'required_sources': ['knowledge'], 'require_pdf': False, 'pdf_produced': False}
        context.set_session('single-route-owner', str(self.base))
        from casework.routing import select_route
        selected=asyncio.run(select_route(query,allow_semantic=False))
        try:
            with patch('casework.routing.select_route',AsyncMock(return_value=selected)), \
                 patch.object(main_agent, 'build_task_plan', AsyncMock(side_effect=AssertionError('second routing vote'))) as planner, \
                 patch.object(main_agent, 'main_agent_run', AsyncMock(side_effect=AssertionError('open agent'))) as agent, \
                 patch('casework.workflow.execute', AsyncMock(return_value=result)) as execute, \
                 patch.object(main_agent, 'soft_review', AsyncMock(return_value=None)), \
                 patch.object(main_agent, 'check_statistics', AsyncMock(return_value={'status': 'not_required'})), \
                 patch.object(main_agent.rule_engine, 'current', return_value={}), \
                 patch.object(main_agent.monitor, 'report', AsyncMock()), \
                 patch.object(main_agent, '_prepare_session'), \
                 patch.object(main_agent.path_utils, 'get_upload_dir', return_value=self.base/'uploads'):
                asyncio.run(main_agent._run_session(query, 'single-route-owner', self.base, 1))
            planner.assert_not_called(); agent.assert_not_called(); execute.assert_awaited_once()
        finally:
            context.clear_tool_budgets('single-route-owner'); context.reset_session()

    def test_contract_checks_reject_mutation_and_inconsistent_model_gaps(self):
        from casework.review import IntentReview, validate_review
        good = IntentReview(gaps=[], satisfied=True)
        self.assertFalse(validate_review(good, '查询初稿逾期案件'))
        for decision in [
            IntentReview(gaps=[], satisfied=False),
            IntentReview(gaps=[{'kind':'lost_filter','task_quote':'不在原任务里的条件','reason':'遗漏'}], satisfied=False),
            IntentReview(gaps=[{'kind':'lost_filter','task_quote':'初稿','reason':'改成向局提交'}], satisfied=True),
        ]:
            self.assertTrue(validate_review(decision, '查询初稿逾期案件'))
        _, result = self.execute(dict(operation='case_brief', query='DEMO-2026-013'))
        from casework.review import review
        from unittest.mock import MagicMock
        llm = MagicMock()
        result['report'] += '\n该技术保证准确率提升。'
        with patch('agent.llm.get_llm', return_value=llm):
            issue = asyncio.run(review(result))
        self.assertTrue(issue); self.assertEqual(result['soft_review_status'], 'failed')
        llm.assert_not_called()
        from casework.verification import language_issues
        from casework.workflow import DepartmentRequest
        for text in ['| 所编号 | 法定期限 |', '实际日期缺失证明该案尚未交局。',
                     '交底书全文完全没有任何实现条件。', '该技术必然提升准确率。']:
            self.assertTrue(language_issues(DepartmentRequest(operation='case_brief', query='DEMO-2026-013'), text), text)

    def test_multiple_clients_do_not_include_a_third_client(self):
        from casework import catalog
        from tools.casework_tools import query_department_cases
        from casework.workflow import DepartmentRequest, plan_details
        records = catalog.meetings()
        third = {**records[0], 'client':'客户丙（模拟）', 'case_id':'DEMO-2026-99999'}
        request = DepartmentRequest(operation='meetings', clients=['客户甲（模拟）','客户乙（模拟）'])
        self.assertIn('客户范围', plan_details(request)['intent_summary'])
        with patch('casework.catalog.meetings', return_value=[*records,third]), patch('api.evidence.add', return_value='S1'):
            result = json.loads(query_department_cases.invoke(request.model_dump(exclude_none=True, exclude={'completed_only','filing_month','result_limit'})))
        self.assertNotIn('客户丙（模拟）', {r['client'] for r in result['rows']})
        self.assertNotIn('DEMO-2026-99999', {r['case_id'] for r in result['rows']})

    def test_shared_quote_displays_distinct_deadline_conditions(self):
        from casework.workflow import DepartmentRequest, plan_details
        from casework.requirements import requirement_issues
        quote='准备催一下初稿，下两周该出的和已经拖过期的都给我看看。'
        request=DepartmentRequest(operation='cases',due_field='draft_due_date',due_statuses=['next14','overdue'],
            requirements=[{'quote':quote,'kind':'deadline_upcoming'},{'quote':quote,'kind':'deadline_overdue'}])
        steps=plan_details(request)['steps']
        self.assertIn('查询近期到期的初稿案件（基准日起14天内）',steps)
        self.assertIn('查询已经逾期的初稿案件（期限早于基准日）',steps)
        self.assertEqual(len(steps),6)
        self.assertEqual(len(request.requirements),2)
        self.assertEqual(set(request.due_statuses),{'next14','overdue'})
        self.assertEqual(requirement_issues(quote,request),[])

    def test_schema_valid_semantic_slots_still_receive_bounded_repair(self):
        from casework.workflow import DepartmentRequest, DepartmentRoute, resolve
        from unittest.mock import MagicMock
        llm = MagicMock(); model = llm.with_structured_output.return_value
        requirements=[{'kind':'archive_materials','quote':'找一个采用知识蒸馏的历史案件正文'}]
        missing = DepartmentRoute(supported=True, request=DepartmentRequest(operation='archive', query='知识蒸馏',requirements=requirements))
        corrected = DepartmentRoute(supported=True, request=DepartmentRequest(operation='archive', query='知识蒸馏', result_limit=1,requirements=requirements))
        model.ainvoke = AsyncMock(side_effect=[{'parsed': missing}, {'parsed': corrected}])
        with patch('agent.llm.get_llm', return_value=llm):
            request = asyncio.run(resolve('找一个采用知识蒸馏的历史案件正文'))
        self.assertEqual(request.result_limit, 1)
        self.assertEqual(model.ainvoke.await_count, 2)

    def execute(self, params):
        from api import context, source_policy
        from casework.workflow import DepartmentRequest, execute, sources_for
        from casework import analytics
        for person in json.loads((ROOT/'examples/department_shared_drive/expected.json').read_text())['people']:
            if not any(p['name'] == person['name'] for p in analytics.list_people()):
                analytics.save_person(**person)
        request = DepartmentRequest(**params)
        context.set_session('contract-test', str(self.base))
        source_policy.configure(sources_for(request), [])
        entries = []
        def add(thread, item):
            entry = {**item, 'id': 'S'+str(len(entries)+1)}
            entries.append(entry)
            return entry['id']
        try:
            with patch('api.evidence.add', side_effect=add), patch('api.evidence.snapshot', return_value=entries), \
                 patch('api.monitor.monitor.report', AsyncMock()):
                result = asyncio.run(execute(request, 'contract-test', 1, sources_for(request)))
            return request, result
        finally:
            context.clear_tool_budgets('contract-test')
            context.reset_session()

    def test_all_fixed_actions_validate_against_execution_evidence(self):
        for params in [dict(operation='archive', query='HNSW近邻检索', completed_only=True),
                       dict(operation='cases', query='DEMO-2026-024'),
                       dict(operation='cases', due_field='draft_due_date', due_status='overdue', as_of='2026-10-26'),
                       dict(operation='monthly', start_month='2026-09', end_month='2026-10'),
                       dict(operation='meetings', client='客户甲（模拟）', ipr='IPR林（模拟）'),
                       dict(operation='recommend', query='向量数据库近邻检索', as_of='2026-10-26'),
                       dict(operation='case_brief', query='DEMO-2026-013')]:
            _, result = self.execute(params)
            self.assertEqual(result['business_check']['status'], 'passed', (params, result['business_check']['issues']))
            self.assertGreater(result['business_check']['checked_claims'], 0)
            if params.get('query') == 'DEMO-2026-024':
                self.assertIn('冲突待核实', result['report'])
                self.assertNotIn('未登记', result['report'])

    def test_interrogative_queries_do_not_disable_positive_mechanism_gate(self):
        from casework import parser
        from casework.search import search
        for tag, aliases in parser.TAG_RULES.items():
            query = '有没有采用'+aliases[0]+'的相似案件？'
            self.assertIn(tag, parser.query_tags(query))
            self.assertNotIn(tag, parser.tags(query))
        result = search('有没有采用教师模型、学生模型和蒸馏损失的相似案件？')
        self.assertEqual({r['case_id'] for r in result['results']}, {'DEMO-2026-003','DEMO-2026-009','DEMO-2026-015','DEMO-2026-021'})
        self.assertFalse(parser.query_tags('不要检索本案不采用向量数据库的内容。'))

    def test_mutations_cannot_receive_hard_check_pass(self):
        from tools.reflect_check import hard_check
        _, original = self.execute(dict(operation='case_brief', query='DEMO-2026-013'))
        from casework.verification import verify
        # Exercise source corruption independently of rendered prose: a valid
        # citation ID is insufficient when its case, value or body is wrong.
        from casework.workflow import DepartmentRequest, render
        from tools.casework_tools import search_case_archive
        entries = []
        def add(thread, item):
            entries.append({**item, 'id': 'S'+str(len(entries)+1)})
            return entries[-1]['id']
        with patch('api.evidence.add', side_effect=add):
            payload = json.loads(search_case_archive.invoke({'query': 'HNSW', 'completed_only': True}))
        request = DepartmentRequest(operation='archive', query='HNSW', completed_only=True)
        report = render(payload, request)
        self.assertEqual(verify(request, payload, entries, report)['status'], 'passed')
        proof_id = next(p for p in payload['results'][0]['sources'] if p['kind'] in ('application', 'disclosure'))['evidence_id']
        body_index = next(i for i, e in enumerate(entries) if e['id'] == proof_id)
        mutations = [
            lambda e: e[0].update(case_id='S99P999999'),
            lambda e: e[0].update(type='database'),
            lambda e: e[0].update(snippet='代理师：别人'),
            lambda e: e[body_index].update(snippet='改写后的技术效果'),
            lambda e: e[body_index].update(revision='stale'),
        ]
        for mutate in mutations:
            altered = copy.deepcopy(entries)
            mutate(altered)
            checked = verify(request, payload, altered, report)
            self.assertEqual(checked['status'], 'failed')
            self.assertTrue(hard_check({**original, 'business_check': checked, 'require_report': False}))
        for changed in [report.replace('原文件记载承办人', '法定代理人'),
                        report+'\n该技术保证检索准确率提升。', report.replace('[S1]', '[S999]')]:
            self.assertEqual(verify(request, payload, entries, changed)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
