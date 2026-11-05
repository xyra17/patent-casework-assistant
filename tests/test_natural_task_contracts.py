"""Regressions for lost clauses, distractor entities and false whole-task passes."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests import test_department_contracts as fixtures
from casework.workflow import DepartmentRequest, DepartmentRoute
from casework.verification import task_contract_issues


class NaturalTaskContracts(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    tearDown = fixtures.ContractTests.tearDown
    execute = fixtures.ContractTests.execute

    def test_counts_preserve_chinese_numerals_and_material_units(self):
        from casework.routing import result_limit
        for query,n in [('帮我找两份相似技术材料',2),('找三篇能参考的旧案',3),
                        ('提供十二件历史案件',12),('列出20份材料',20),('给我一个旧案',1)]:
            self.assertEqual(result_limit(query),n)
            req=DepartmentRequest(operation='archive',query='向量检索',result_limit=n)
            self.assertFalse(task_contract_issues(query,req,{}))
            self.assertTrue(task_contract_issues(query,req.model_copy(update={'result_limit':None}),{}))
        request,result=self.execute(dict(operation='archive',query='向量检索',result_limit=2))
        self.assertEqual(len(result['business_delivery']['case_ids']),2)
        self.assertEqual(result['business_check']['status'],'passed')

    def test_ipr_word_order_aliases_and_same_client_distractor(self):
        from casework.routing import known_entities
        from casework import catalog
        from tools.casework_tools import query_department_cases
        for query in ['客户甲的林IPR','客户甲的林 ipr','客户甲的IPR林']:
            entities=known_entities(query)
            self.assertEqual(entities['ipr'],['IPR林（模拟）'])
            wrong=DepartmentRequest(operation='meetings',client='客户甲（模拟）')
            self.assertTrue(task_contract_issues(query,wrong,{}))
        original=catalog.meetings
        decoy={**original()[0],'client':'客户甲（模拟）','iprs':['IPR顾（模拟）'],'agents':['无关同事'],'case_id':'DEMO-2026-99999'}
        def meetings(**filters):
            rows=[*original(),decoy]
            return [r for r in rows if (not filters.get('client') or filters['client']==r['client'])
                    and (not filters.get('ipr') or filters['ipr'] in r['iprs'])]
        with patch('casework.catalog.meetings',side_effect=meetings),patch('api.evidence.add',return_value='S1'):
            result=json.loads(query_department_cases.invoke({'operation':'meetings','client':'客户甲（模拟）','ipr':'IPR林（模拟）'}))
        self.assertEqual(len(result['rows']),2)
        self.assertNotIn('无关同事',{name for r in result['rows'] for name in r['agents']})

    def test_future_and_overdue_clauses_both_execute_and_render(self):
        requirements=[{'kind':'deadline_upcoming','quote':'接下来两周'}, {'kind':'deadline_overdue','quote':'已经晚了'}]
        request,result=self.execute(dict(operation='cases',due_field='client_review_due_date',
            due_statuses=['next14','overdue'],as_of='2026-10-27',requirements=requirements))
        delivered=result['business_delivery']
        self.assertEqual(set(delivered['due_groups']['next14']),{'DEMO-2026-013','DEMO-2026-014','DEMO-2026-019','DEMO-2026-020','DEMO-2026-022','DEMO-2026-023','DEMO-2026-028','DEMO-2026-029'})
        self.assertTrue(delivered['due_groups']['overdue'])
        self.assertFalse(set(delivered['due_groups']['overdue'])&set(delivered['due_groups']['next14']))
        self.assertIn('## 已过计划期限',result['report'])
        self.assertIn('## 未来两周计划到期',result['report'])
        self.assertEqual(result['business_check']['status'],'passed',result['business_check']['issues'])
        query='看看接下来两周哪些要送客户、哪些已经晚了'
        self.assertFalse(task_contract_issues(query,request,delivered))
        lost=request.model_copy(update={'due_statuses':None,'due_status':'next14'})
        self.assertTrue(task_contract_issues(query,lost,delivered))
        missing=copy.deepcopy(delivered);del missing['due_groups']['overdue']
        self.assertTrue(task_contract_issues(query,request,missing))

    def test_empty_deadline_group_is_not_omitted(self):
        _,result=self.execute(dict(operation='cases',due_field='client_review_due_date',
                                  due_statuses=['overdue','next14'],as_of='2030-01-01'))
        self.assertEqual(result['business_delivery']['due_groups']['next14'],[])
        self.assertIn('## 未来两周计划到期',result['report'])
        self.assertIn('当前接入的在办案件中，没有符合该项条件的案件',result['report'])

    def test_month_comparison_is_computed_bound_and_required(self):
        request,result=self.execute(dict(operation='monthly',start_month='2026-09',end_month='2026-10',compare_months=True,
            requirements=[{'kind':'monthly_counts','quote':'每月收案交局多少'}, {'kind':'monthly_comparison','quote':'变化怎么样'}]))
        comparison=result['business_delivery']['comparisons'][0]
        self.assertEqual((comparison['received_change_count'],comparison['filed_change_count']),(-1,0))
        self.assertEqual(result['business_check']['status'],'passed',result['business_check']['issues'])
        self.assertIn('实际收案数量减少',result['report']);self.assertIn('实际交局数量持平',result['report'])
        self.assertFalse(task_contract_issues('每月收案交局多少，变化怎么样',request,result['business_delivery']))
        missing={**result['business_delivery'],'comparisons':[]}
        self.assertTrue(task_contract_issues('每月收案交局多少，变化怎么样',request,missing))

    def test_month_comparison_passes_statistics_without_waiving_value_or_scope(self):
        from tools.statistics_check import check_statistics
        _,result=self.execute(dict(operation='monthly',start_month='2026-08',end_month='2026-10',compare_months=True))
        with patch('agent.llm.get_llm',side_effect=AssertionError('table claims are deterministic')):
            checked=asyncio.run(check_statistics(result['report'],result['evidence']))
        self.assertEqual(checked['status'],'passed',checked['issues'])
        self.assertEqual(checked['checked_claims'],10)
        for bad in [result['report'].replace('-1 [','99 ['),
                    result['report'].replace('[2026-09-01，2026-11-01)','[2026-08-01，2026-11-01)'),
                    result['report'].replace('实际收案数量变化（件）','实际交局数量变化（件）')]:
            with patch('agent.llm.get_llm',side_effect=AssertionError('table claims are deterministic')):
                self.assertEqual(asyncio.run(check_statistics(bad,result['evidence']))['status'],'failed')

    def test_case_brief_has_body_and_relative_planned_nodes(self):
        request,result=self.execute(dict(operation='case_brief',query='DEMO-2026-013',as_of='2026-10-27',requirements=[
            {'kind':'case_owner','quote':'谁跟进'}, {'kind':'case_technology','quote':'做什么的'},
            {'kind':'case_deadlines','quote':'要盯什么时间'}]))
        self.assertIn('林澄',result['report']);self.assertIn('HNSW',result['report'])
        self.assertIn('本案主要技术',result['report']);self.assertIn('初稿：已过计划日',result['report'])
        self.assertIn('客户送审：未来两周内计划到期',result['report'])
        self.assertEqual(result['business_check']['status'],'passed',result['business_check']['issues'])
        self.assertFalse(task_contract_issues('DEMO-2026-013谁跟进，做什么的，要盯什么时间',request,result['business_delivery']))
        wrong=request.model_copy(update={'operation':'cases'})
        self.assertTrue(task_contract_issues('DEMO-2026-013谁跟进，做什么的，要盯什么时间',wrong,{}))

    def test_production_semantics_can_replace_a_lossy_grammar_match(self):
        from casework.routing import select_route
        query='DEMO-2026-013谁跟进，做什么的，要盯什么时间'
        request=DepartmentRequest(operation='case_brief',query='DEMO-2026-013',requirements=[{'kind':'case_technology','quote':'做什么的'}])
        with patch('casework.workflow.resolve',AsyncMock(return_value=request)) as resolve:
            selected=asyncio.run(select_route(query))
        resolve.assert_awaited_once_with(query)
        self.assertEqual(selected.request.operation,'case_brief')
        self.assertEqual(selected.method,'semantic')
        with patch('casework.workflow.resolve',AsyncMock(return_value=request)):
            self.assertEqual(asyncio.run(select_route(query,['knowledge'])).status,'blocked')

    def test_requirements_are_not_allowed_to_expand_action_or_sources(self):
        from casework.routing import select_route
        request=DepartmentRequest(operation='cases',query='DEMO-2026-013',requirements=[{'kind':'other','quote':'联网查公开材料'}])
        with patch('casework.workflow.resolve',AsyncMock(return_value=request)):
            self.assertEqual(asyncio.run(select_route('DEMO-2026-013联网查公开材料')).status,'general')

    def test_action_is_compiled_from_requirements_not_a_second_classification(self):
        from casework.workflow import DepartmentIntent,compile_intent,DepartmentIntentRoute
        self.assertNotIn('operation',DepartmentIntent.model_fields)
        intent=DepartmentIntent(client='甲',ipr='顾 IPR',requirements=[
            {'kind':'meeting_attendance','quote':'之前哪几位同事和他聊过方案'}])
        request=compile_intent(intent,'之前哪几位同事和他聊过方案')
        self.assertEqual(request.operation,'meetings')
        self.assertEqual(request.client,'客户甲（模拟）');self.assertEqual(request.ipr,'IPR顾（模拟）')
        llm=MagicMock();llm.with_structured_output.return_value.ainvoke=AsyncMock(return_value={
            'parsed':DepartmentIntentRoute(request=intent)})
        from casework.workflow import resolve
        with patch('agent.llm.get_llm',return_value=llm):
            resolved=asyncio.run(resolve('客户甲的顾IPR，之前哪几位同事和他聊过方案'))
        self.assertEqual(resolved.operation,'meetings')
        unknown=DepartmentIntent(requirements=[{'kind':'case_owner','quote':'谁负责'},
            {'kind':'meeting_attendance','quote':'谁开会'}])
        with self.assertRaises(ValueError):compile_intent(unknown,'谁负责，谁开会')

    def test_open_agent_tool_rejects_unused_filters_and_binds_exact_aliases(self):
        from tools.casework_tools import query_department_cases
        from api import context,source_policy
        context.set_session('filter-contract',str(self.base));source_policy.configure(['db_query'],[])
        try:
            with patch('api.evidence.add',return_value='S1'):
                result=json.loads(query_department_cases.invoke({'operation':'meetings','client':'甲','ipr':'顾'}))
                self.assertEqual([r['date'] for r in result['rows']],['2025-09-19'])
                for params in [{'operation':'meetings','query':'顾'},
                               {'operation':'cases','ipr':'顾'},
                               {'operation':'meetings','client':'不存在的客户'}]:
                    self.assertIn('查询未完成',query_department_cases.invoke(params))
        finally:context.clear_tool_budgets('filter-contract');context.reset_session()

    def test_ambiguous_role_alias_is_rejected_instead_of_broadened(self):
        from casework.entities import bind
        with self.assertRaises(ValueError):bind('甲',['客户甲','甲'],'client')
        with self.assertRaises(ValueError):bind('其他客户',['客户甲','客户乙'],'client')

    def test_contract_route_still_gets_whole_question_semantic_review(self):
        from casework.review import review,IntentReview
        _,result=self.execute(dict(operation='monthly',start_month='2026-09',end_month='2026-10'))
        result.update(query='每月收案交局多少，另给一段给客户看的说明',department_route_method='contract')
        llm=MagicMock();llm.with_structured_output.return_value.ainvoke=AsyncMock(return_value={'parsed':IntentReview(
            gaps=[{'kind':'missing_delivery','task_quote':'另给一段给客户看的说明','reason':'没有给出客户说明'}],satisfied=False)})
        with patch('agent.llm.get_llm',return_value=llm):issue=asyncio.run(review(result))
        self.assertIn('没有给出客户说明',issue);self.assertEqual(result['soft_review_status'],'failed')
        llm.with_structured_output.return_value.ainvoke.assert_awaited_once()

    def test_missing_month_comparison_is_rejected_before_model_review(self):
        from casework.review import review
        _,result=self.execute(dict(operation='monthly',start_month='2026-09',end_month='2026-10'))
        result.update(query='每月收案交局多少，变化怎么样',department_route_method='contract')
        with patch('agent.llm.get_llm',side_effect=AssertionError('program rejection cannot be waived')):
            issue=asyncio.run(review(result))
        self.assertIn('比较月份',issue)
        self.assertEqual(result['soft_review_status'],'failed')

    def test_reviewer_cannot_claim_pass_while_skipping_requirement(self):
        from casework.review import IntentReview,validate_review
        req=DepartmentRequest(operation='case_brief',query='DEMO-2026-013',requirements=[
            {'kind':'case_owner','quote':'谁跟进'},{'kind':'case_technology','quote':'做什么的'}])
        self.assertTrue(validate_review(IntentReview(gaps=[],satisfied=True,checked_requirements=[0]),'谁跟进，做什么的',req.requirements))
        self.assertFalse(validate_review(IntentReview(gaps=[],satisfied=True,checked_requirements=[0,1]),'谁跟进，做什么的',req.requirements))

    def test_recommendation_uses_one_aggregate_call(self):
        _,result=self.execute(dict(operation='recommend',query='语义向量近邻索引',domain='AI',as_of='2026-10-27',
            requirements=[{'kind':'candidate_recommendation','quote':'交给谁写更合适'}]))
        self.assertEqual(result['execution_contract']['tools'],['query_department_cases'])
        self.assertTrue(result['business_delivery']['selected_candidates'])
        self.assertEqual(result['business_check']['status'],'passed',result['business_check']['issues'])

    def test_semantic_concept_reaches_positive_evidence_gate(self):
        from casework.workflow import DepartmentIntent,compile_intent
        from casework import parser
        query='大模型把输出分布教给小模型，这种案子谁来做'
        intent=DepartmentIntent(query='大模型把输出分布教给小模型',requirements=[
            {'kind':'candidate_recommendation','quote':'谁来做'}],technical_concepts=[
            {'tag':'模型蒸馏','quote':'大模型把输出分布教给小模型'}])
        request=compile_intent(intent,query)
        self.assertIn('模型蒸馏',parser.query_tags(request.query))
        _,result=self.execute(request.model_dump())
        self.assertFalse(result['business_delivery']['technical_intent_unresolved'])
        excerpts=result['business_delivery']['technical_excerpts']
        self.assertTrue(excerpts)
        self.assertTrue(all(h['case_id'] in {'DEMO-2026-003','DEMO-2026-009','DEMO-2026-015','DEMO-2026-021'} for h in excerpts))
        self.assertEqual(result['business_check']['status'],'passed',result['business_check']['issues'])
        intent.technical_concepts[0].quote='不在原话里的技术'
        with self.assertRaises(ValueError):compile_intent(intent,query)

    def test_unknown_mechanism_cannot_gain_experience_from_broad_similarity(self):
        _,result=self.execute(dict(operation='recommend',query='还没登记过的奇异新机制',as_of='2026-10-27'))
        self.assertTrue(result['business_delivery']['technical_intent_unresolved'])
        self.assertEqual(result['business_delivery']['technical_excerpts'],[])
        self.assertIn('已暂停推荐',result['report'])
        self.assertEqual(result['business_check']['status'],'awaiting_input')
        self.assertEqual(result['clarification']['reason'],'technical_mapping_unconfirmed')
        self.assertNotIn('## 辅助分案候选',result['report'])

    def test_canonical_concepts_and_uncertain_source_statements(self):
        from casework import parser
        for tag in parser.TAG_RULES:
            self.assertIn(tag,parser.query_tags('找'+tag+'相关的材料'))
            self.assertIn(tag,parser.tags('本案采用'+tag+'处理输入数据。'))
            for prefix in ['本案不采用','是否采用','能否采用','计划采用','拟采用']:
                self.assertNotIn(tag,parser.tags(prefix+tag+'。'))
            self.assertIn(tag,parser.query_tags('是否采用'+tag+'的案件有参考材料？'))

    def test_stale_catalog_label_cannot_establish_experience(self):
        from casework import analytics,store
        self.execute(dict(operation='recommend',query='模型蒸馏'))
        cases=copy.deepcopy(store.list_cases())
        distillation={'DEMO-2026-003','DEMO-2026-009','DEMO-2026-015','DEMO-2026-021'}
        for case in cases:
            if case['case_id'] not in distillation:
                for tag in case['tags']:tag['tag']='模型蒸馏'
        with patch('casework.store.list_cases',return_value=cases):
            result=analytics.recommend('模型蒸馏',as_of='2026-10-27')
        matches=[h for r in result['candidates'] for h in r['history_matches']]
        self.assertTrue(matches)
        self.assertTrue(all(h['case_id'] in distillation for h in matches))

    def test_fact_correct_optical_answer_cannot_pass_distillation_hard_check(self):
        from casework.verification import verify
        from casework.workflow import render
        request,result=self.execute(dict(operation='recommend',query='光通信',as_of='2026-10-27'))
        self.assertTrue(result['business_delivery']['technical_excerpts'])
        self.assertEqual(result['business_check']['status'],'passed')
        # Reuse the genuine optical result and all its valid citations. Only the
        # requested mechanism changes: fact accuracy cannot waive relevance.
        captured=[]
        def digest(value,operation):
            captured.append(value)
            return json.dumps(value,ensure_ascii=False)
        with patch('tools.casework_tools._digest',side_effect=digest):
            _,optical=self.execute(request.model_dump())
        wrong=request.model_copy(update={'query':'模型蒸馏'})
        checked=verify(wrong,captured[0],optical['evidence'],render(captured[0],wrong))
        self.assertEqual(checked['status'],'failed')
        self.assertTrue(any('正向机制' in issue for issue in checked['issues']))

    def test_meeting_review_receives_actual_colleagues_without_archive_filters(self):
        from casework.review import IntentReview,review
        query='客户甲那边的顾 IPR我还没接触过，之前哪几位同事和他聊过方案？'
        _,result=self.execute(dict(operation='meetings',client='客户甲（模拟）',ipr='IPR顾（模拟）',
            requirements=[{'kind':'meeting_attendance','quote':'之前哪几位同事和他聊过方案？'}]))
        expected=['周行（模拟）','许宁（模拟）']
        self.assertEqual(result['business_delivery']['actual_agents'],expected)
        self.assertIn('实际参会同事：'+'、'.join(expected),result['report'])
        for field in ('completed_only','compare_months','technical_concepts'):
            self.assertNotIn(field,result['business_check']['request'])
        result['query']=query
        llm=MagicMock()
        llm.with_structured_output.return_value.ainvoke=AsyncMock(return_value={'parsed':
            IntentReview(gaps=[],satisfied=True,checked_requirements=[0])})
        with patch('agent.llm.get_llm',return_value=llm):
            self.assertIsNone(asyncio.run(review(result)))
        payload=json.loads(llm.with_structured_output.return_value.ainvoke.call_args.args[0][1]['content'])
        self.assertEqual(payload['task'],query)
        self.assertEqual(payload['delivery']['actual_agents'],expected)

    def test_catalog_keeps_mechanism_evidence_after_character_500(self):
        from casework import store,parser,sync
        path=self.source/'DEMO-2026-003.申请文件_补充.md'
        path.write_text('背景说明。'*110+'本案采用模型蒸馏，通过教师模型的软标签训练学生模型。')
        self.assertEqual(sync.run(self.ident)['state'],'ready')
        case=store.get_case(self.ident+':DEMO-2026-003')
        proof=next(p for tag in case['tags'] if tag['tag']=='模型蒸馏'
            for p in tag['sources'] if p['path']==path.name)
        self.assertIn('模型蒸馏',parser.tags(proof['excerpt']))

    def test_candidate_subrequirements_compile_and_retain_workload(self):
        from casework.workflow import DepartmentIntent,compile_intent
        query='有个新案子谁接合适，看看过去做过什么、现在还有多少活'
        intent=DepartmentIntent(query='模型蒸馏',requirements=[
            {'kind':'candidate_recommendation','quote':'谁接合适'},
            {'kind':'candidate_history','quote':'过去做过什么'},
            {'kind':'candidate_workload','quote':'现在还有多少活'}])
        request=compile_intent(intent,query)
        _,result=self.execute(request.model_dump())
        self.assertEqual(request.operation,'recommend')
        self.assertFalse(task_contract_issues(query,request,result['business_delivery']))
        missing={**result['business_delivery'],'fields':['historical_owner_evidence']}
        self.assertTrue(task_contract_issues(query,request,missing))
        intent.requirements=intent.requirements[1:]
        with self.assertRaises(ValueError):compile_intent(intent,query)

    def test_positive_concept_quote_keeps_punctuation_and_rejects_negative_span(self):
        from casework.routing import affirmative_quote
        from casework.workflow import DepartmentIntent,compile_intent
        query='大模型指导小模型，想找人接；不要找光通信案子。'
        intent=DepartmentIntent(query='大模型指导小模型',requirements=[
            {'kind':'candidate_recommendation','quote':'想找人接'}],technical_concepts=[
            {'tag':'模型蒸馏','quote':'大模型指导小模型，想找人接'}])
        self.assertEqual(compile_intent(intent,query).operation,'recommend')
        self.assertFalse(affirmative_quote(query,'光通信'))
        self.assertFalse(affirmative_quote(query,'想找人接；不要找光通信'))
        self.assertTrue(affirmative_quote('不要找光通信；还是找光通信','光通信'))

    def test_old_cases_do_not_silently_become_completed_only(self):
        request=DepartmentRequest(operation='archive',query='模型蒸馏',completed_only=True)
        self.assertTrue(task_contract_issues('找三份能借鉴的旧案材料',request,{}))
        self.assertFalse(task_contract_issues('找已办结的模型蒸馏旧案材料',request,{}))

    def test_resolution_failure_audit_retains_failed_contract_without_extra_retries(self):
        from casework.workflow import DepartmentIntent,DepartmentIntentRoute
        from casework.routing import select_route
        intent=DepartmentIntent(query='模型蒸馏',owner='林澄（模拟）',requirements=[{'kind':'archive_materials','quote':'找材料'}])
        llm=MagicMock();call=AsyncMock(return_value={'parsed':DepartmentIntentRoute(request=intent)})
        llm.with_structured_output.return_value.ainvoke=call
        with patch('agent.llm.get_llm',return_value=llm):
            decision=asyncio.run(select_route('找材料'))
        self.assertEqual(call.await_count,2)
        self.assertEqual(decision.status,'general')
        self.assertEqual(len(decision.audit()['diagnostics']),2)
        self.assertEqual(decision.audit()['diagnostics'][0]['extracted']['requirements'][0]['quote'],'找材料')

    def test_unknown_technology_is_not_a_separate_model_capability_veto(self):
        from casework.workflow import DepartmentIntent,DepartmentIntentRoute,resolve
        self.assertNotIn('supported',DepartmentIntentRoute.model_fields)
        intent=DepartmentIntent(query='量子纠错',requirements=[
            {'kind':'candidate_recommendation','quote':'谁来接'},
            {'kind':'candidate_workload','quote':'手头多少活'}])
        llm=MagicMock();call=AsyncMock(return_value={'parsed':DepartmentIntentRoute(request=intent)})
        llm.with_structured_output.return_value.ainvoke=call
        with patch('agent.llm.get_llm',return_value=llm):request=asyncio.run(resolve('量子纠错谁来接，手头多少活'))
        self.assertEqual(call.await_count,1)
        self.assertEqual(request.operation,'recommend')
        _,result=self.execute(request.model_dump())
        self.assertTrue(result['business_delivery']['technical_intent_unresolved'])
        self.assertFalse(result['business_delivery']['technical_excerpts'])
        call.return_value={'parsed':DepartmentIntentRoute(request=DepartmentIntent(
            requirements=[{'kind':'other','quote':'自动保存分案'}]))}
        call.reset_mock()
        with patch('agent.llm.get_llm',return_value=llm):self.assertIsNone(asyncio.run(resolve('自动保存分案')))
        self.assertEqual(call.await_count,1)


if __name__=='__main__':unittest.main()
