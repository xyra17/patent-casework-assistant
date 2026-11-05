"""Supported conditions must execute; unsupported work must retain its clauses."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
from casework.workflow import DepartmentIntent, DepartmentIntentRoute, DepartmentRequest, compile_intent, sources_for
from tests import test_department_contracts as fixtures

class SemanticConstraintFixes(unittest.TestCase):
    setUp=fixtures.ContractTests.setUp
    tearDown=fixtures.ContractTests.tearDown
    execute=fixtures.ContractTests.execute

    def compile(self, text, kind, constraints, **params):
        return compile_intent(DepartmentIntent(requirements=[{'kind':kind,'quote':text}],
            constraints=[{'kind':k,'quote':q} for k,q in constraints],**params),text)

    def test_supported_conditions_are_preserved_and_execute(self):
        specs=[
            ('查DEMO-2026-024三个期限，说明来源冲突，不要修改案件信息','case_deadlines',
             [('show_conflicts','说明来源冲突'),('read_only','不要修改案件信息')],dict(query='DEMO-2026-024')),
            ('统计2026年10月实际收案和交局，未知单列，不用计划替代，同案去重','monthly_counts',
             [('unknown_separate','未知单列'),('actual_events','不用计划替代'),('unique_cases','同案去重')],dict(start_month='2026-10',end_month='2026-10')),
            ('找DEMO-2026-019绘图附件，图形连接由我自行核实','archive_materials',
             [('manual_diagram','图形连接由我自行核实')],dict(query='DEMO-2026-019',archive_kind='drawing')),
            ('查DEMO-2026-019三个期限，不看技术正文','case_deadlines',
             [('no_technical_body','不看技术正文')],dict(query='DEMO-2026-019')),
        ]
        for query,kind,conditions,params in specs:
            with self.subTest(query=query):
                req=self.compile(query,kind,conditions,**params)
                _,out=self.execute(req.model_dump())
                self.assertEqual(out['business_check']['status'],'passed',out['business_check']['issues'])
                from casework.verification import task_contract_issues
                self.assertEqual(task_contract_issues(query,req,out['business_delivery']),[])
                self.assertFalse(out['execution_contract']['source_files_written'])

    def test_unknown_deadlines_must_be_a_real_group(self):
        query='初稿未来两周、已逾期，未知另列'
        params=dict(due_field='draft_due_date',as_of='2026-10-30',due_statuses=['next14','overdue'])
        with self.assertRaisesRegex(ValueError,'未知另列'):
            self.compile(query,'deadline_upcoming',[('unknown_separate','未知另列')],**params)
        params['due_statuses'].append('unknown')
        req=self.compile(query,'deadline_upcoming',[('unknown_separate','未知另列')],**params)
        _,out=self.execute(req.model_dump())
        self.assertIn('unknown',out['business_delivery']['due_groups'])
        self.assertEqual(out['business_check']['status'],'passed')

    def test_workload_recommendation_is_ranked_without_history_or_knowledge(self):
        query='仅按在办和期限负荷推荐谁接案，不需要核对技术经历'
        intent=DepartmentIntent(requirements=[{'kind':k,'quote':query} for k in ('candidate_recommendation','candidate_workload')],
            constraints=[{'kind':'workload_basis','quote':query}])
        req=compile_intent(intent,query)
        self.assertEqual(req.recommendation_mode,'workload_rank')
        self.assertEqual(sources_for(req),['db_query'])
        from tools import casework_tools
        original=casework_tools._guard
        def guard(source):
            self.assertNotEqual(source,'knowledge')
            return original(source)
        with patch.object(casework_tools,'_guard',side_effect=guard):
            _,out=self.execute(req.model_dump())
        self.assertEqual(out['business_check']['status'],'passed',out['business_check']['issues'])
        self.assertIn('辅助分案候选',out['report'])
        self.assertIn('按在办与期限负荷排序的前三位候选',out['report'])
        self.assertNotIn('historical_owner_evidence',out['business_delivery']['fields'])
        self.assertNotIn('clarification',out)
        self.assertEqual(out['called_sources'],['db_query'])
        # A real ranking remains sensitive only to workload, never historical matches.
        from casework import analytics
        result=analytics.recommend('人员负荷',as_of='2026-10-30',workload_rank=True)
        self.assertTrue(all(not r['history_matches'] and not r['domain_match'] for r in result['candidates']))
        self.assertEqual([r['score'] for r in result['candidates']],sorted([r['score'] for r in result['candidates']],reverse=True))
        for row in result['candidates']:
            self.assertEqual(row['score'],-row['active_count']*2-len(row['near_due_cases'])*3-len(row['overdue_cases'])*4-min(len(row['unknown_stage_cases']),5)*2)

    def test_history_gap_cannot_authorize_workload_ranking(self):
        query='结合技术经历和负荷推荐，技术经历未查到'
        with self.assertRaises(ValueError):
            self.compile(query,'candidate_recommendation',[('workload_basis','技术经历未查到')],query='量子纠错')
        query='仅按负荷推荐但也要核对技术经历'
        with self.assertRaises(ValueError):
            compile_intent(DepartmentIntent(query='HNSW',requirements=[{'kind':k,'quote':query} for k in ('candidate_recommendation','candidate_history')],constraints=[{'kind':'workload_basis','quote':'仅按负荷推荐'}]),query)

    def test_vector_prohibition_reaches_the_actual_retriever(self):
        query='找机械卡扣的原文，不要检索向量数据库'
        req=self.compile(query,'archive_materials',[('lexical_only','不要检索向量数据库')],query='机械卡扣')
        with patch('casework.search.get_embedding',side_effect=AssertionError('vector retrieval prohibited')):
            _,out=self.execute(req.model_dump())
        self.assertEqual(out['business_check']['status'],'passed')
        self.assertTrue(out['business_delivery']['case_ids'])
        self.assertTrue(all('BGE' not in str(e.get('channels','')) for e in out['evidence']))

    def test_source_prohibition_is_blocked_before_execution(self):
        from casework.routing import select_route
        query='按HNSW历史经验和负荷推荐，不查业务数据库'
        req=self.compile(query,'candidate_recommendation',[('forbid_db','不查业务数据库')],query='HNSW')
        with patch('casework.workflow.resolve',AsyncMock(return_value=req)):
            decision=asyncio.run(select_route(query))
        self.assertEqual(decision.status,'blocked')
        from agent.main_agent import _explicitly_forbidden_sources
        self.assertIn('db_query',_explicitly_forbidden_sources(query))
        self.assertNotIn('db_query',_explicitly_forbidden_sources('找机械卡扣，不要检索向量数据库'))

    def test_general_tool_cannot_authorize_its_own_workload_downgrade(self):
        from api import context, source_policy
        from tools.casework_tools import query_department_cases
        context.set_session('no-downgrade',str(self.base))
        source_policy.configure(['db_query'])
        result=query_department_cases.invoke(dict(operation='recommend',query='HNSW',recommendation_mode='workload_rank'))
        self.assertIn('不能自行降级',result)

    def test_program_rejects_unsupported_monthly_grouping_and_extra_comparisons(self):
        from casework.verification import task_contract_issues
        req=DepartmentRequest(operation='monthly',start_month='2026-09',end_month='2026-10')
        self.assertTrue(task_contract_issues('按每位代理师统计2026年9月和10月交局',req,{}))
        self.assertTrue(task_contract_issues('按照每个客户统计2026年10月收案',req,{}))
        extra=req.model_copy(update={'compare_months':True})
        self.assertTrue(task_contract_issues('统计2026年9月和10月各收案多少',extra,{}))

    def test_supported_constraint_is_normalized_losslessly_without_mutating_raw_intent(self):
        from casework.workflow import resolve
        query='初稿未来两周到期的，未知日期另列'
        params=dict(due_field='draft_due_date',due_statuses=['next14','unknown'])
        good=DepartmentIntentRoute(request=DepartmentIntent(**params,requirements=[{'kind':'deadline_upcoming','quote':'初稿未来两周到期的'}],constraints=[{'kind':'unknown_separate','quote':'未知日期另列'}]))
        bad=good.model_copy(deep=True)
        from casework.requirements import TaskRequirement
        bad.request.requirements.append(TaskRequirement(kind='other',quote='未知日期另列'))
        invocation=AsyncMock(side_effect=[{'parsed':bad},{'parsed':good}])
        model=type('Model',(),{'ainvoke':invocation})()
        llm=type('LLM',(),{'with_structured_output':lambda *a,**kw:model})()
        with patch('agent.llm.get_llm',return_value=llm):req=asyncio.run(resolve(query))
        self.assertIsNotNone(req)
        self.assertEqual(invocation.await_count,1)
        self.assertEqual(req.normalization_events,[{'quote':'未知日期另列','from':'requirements.other','to':'constraints.unknown_separate'}])
        self.assertEqual(len(bad.request.requirements),2)

    def test_history_snapshot_is_not_a_duplicate_supported_constraint(self):
        from casework.requirements import normalize_constraints
        text='截至2026年9月15日当时已知的资料'
        intent=DepartmentIntent(requirements=[{'kind':'other','quote':text}],constraints=[{'kind':'require_technical_history','quote':text}])
        self.assertEqual(normalize_constraints(intent)[0].requirements,intent.requirements)

    def test_blocked_session_stops_before_general_planning(self):
        from agent import main_agent
        from api import context
        from casework.routing import RouteDecision
        context.set_session('blocked-no-general',str(self.base))
        try:
            with patch('casework.routing.select_route',AsyncMock(return_value=RouteDecision('blocked','所需来源被禁止：db_query'))), \
                 patch.object(main_agent,'build_task_plan',AsyncMock()) as planner, \
                 patch.object(main_agent,'main_agent_run',AsyncMock()) as agent, \
                 patch.object(main_agent.rule_engine,'current',return_value={}), \
                 patch.object(main_agent,'_prepare_session'), \
                 patch.object(main_agent.path_utils,'get_upload_dir',return_value=self.base/'missing'):
                with self.assertRaisesRegex(PermissionError,'db_query'):
                    asyncio.run(main_agent._run_session('根据HNSW经历和负荷推荐，不查业务数据库','blocked-no-general',self.base,1))
            planner.assert_not_awaited()
            agent.assert_not_awaited()
        finally:
            context.clear_tool_budgets('blocked-no-general')
            context.reset_session()

    def test_other_constraints_are_not_treated_as_new_capabilities(self):
        from casework.requirements import normalize_constraints
        for text,kind in [('交局只算实际确认的','actual_events'),('给依据，不用截止日期判断','actual_events'),('不限定有没有交局','any_completion_state')]:
            intent=DepartmentIntent(requirements=[{'kind':'other','quote':text}])
            self.assertEqual(normalize_constraints(intent)[0].constraints[0].kind,kind)
        for text in ['给依据，并自动修改截止日期','交局只算实际确认的并计算工时','不限定有没有交局，再导出PDF']:
            self.assertEqual(normalize_constraints(DepartmentIntent(requirements=[{'kind':'other','quote':text}]))[0].requirements[0].kind,'other')

    def test_supported_archive_subclauses_expand_without_losing_original_quote(self):
        from casework.requirements import normalize_constraints
        text='给出正文原句和实际承办人'
        raw=DepartmentIntent(query='HNSW',requirements=[{'kind':'archive_materials','quote':'找HNSW原文'},{'kind':'other','quote':text}])
        req=compile_intent(raw,'找HNSW原文，'+text)
        self.assertEqual(req.operation,'archive')
        self.assertEqual({r.kind for r in req.requirements},{'archive_materials','case_technology','case_owner'})
        self.assertTrue(all(r.quote==text for r in req.requirements[1:]))
        bad=raw.model_copy(deep=True)
        bad.requirements[-1].quote+='并评价专业能力'
        self.assertEqual(normalize_constraints(bad)[0].requirements[-1].kind,'other')

    def test_extractor_explicitly_uses_zero_temperature(self):
        from casework.workflow import resolve
        raw=DepartmentIntentRoute(request=DepartmentIntent(query='DEMO-2026-019',requirements=[{'kind':'case_deadlines','quote':'查DEMO-2026-019三个期限'}]))
        model=type('Model',(),{'ainvoke':AsyncMock(return_value={'parsed':raw})})()
        llm=type('LLM',(),{'with_structured_output':lambda *a,**kw:model})()
        with patch('agent.llm.get_llm',return_value=llm) as factory:
            self.assertIsNotNone(asyncio.run(resolve('查DEMO-2026-019三个期限')))
        factory.assert_called_once_with(temperature=0)

    def test_unsupported_clauses_are_never_removed_by_host(self):
        from casework.workflow import resolve
        for query in ['统计9月收案，并按每人分组','查询案件，并修改案件信息','截至2026年9月15日当时已知的资料，统计9月总量']:
            route=DepartmentIntentRoute(request=DepartmentIntent(requirements=[{'kind':'other','quote':query}]))
            model=type('Model',(),{'ainvoke':AsyncMock(return_value={'parsed':route})})()
            llm=type('LLM',(),{'with_structured_output':lambda *a,**kw:model})()
            with patch('agent.llm.get_llm',return_value=llm):self.assertIsNone(asyncio.run(resolve(query)))

if __name__=='__main__':unittest.main()
