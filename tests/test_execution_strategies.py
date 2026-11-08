"""Behavioral checks for complete contracts, model-free delivery and safe reuse."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock,patch

from tests import test_department_casework as fixtures, test_department_contracts as contracts
from casework import store,analytics
from casework.execution_policy import closed_request,program_review_eligible,strategy


class ExecutionStrategyTests(unittest.TestCase):
    setUp=fixtures.DepartmentCaseworkTests.setUp
    tearDown=fixtures.DepartmentCaseworkTests.tearDown
    execute=contracts.ContractTests.execute

    def test_multi_field_read_and_closed_review_use_no_model(self):
        from casework.review import review
        from casework.routing import select_route
        query='查DEMO-2026-013的负责人、发明名称和实际收案日期？；注明来源'
        with patch('agent.llm.get_llm',side_effect=AssertionError('unexpected model')):
            route=asyncio.run(select_route(query))
            self.assertEqual(route.request.case_fields,['owner_name','title','received_date'])
            _,result=self.execute(route.request.model_dump())
            result['query']=query
            self.assertTrue(program_review_eligible(result))
            self.assertIsNone(asyncio.run(review(result)))
            self.assertEqual(result['soft_review_mode'],'program_closed_contract')
            case=store.get_case(self.ident+':DEMO-2026-013')
            for field in route.request.case_fields:self.assertIn(case[field],result['report'])

    def test_seal_cannot_waive_task_or_fact_changes(self):
        query='查DEMO-2026-013的负责人是谁？'
        request=closed_request(query)
        _,result=self.execute(request.model_dump());result['query']=query
        self.assertTrue(program_review_eligible(result))
        for change in ('task','report','evidence','permissions'):
            bad=copy.deepcopy(result)
            if change=='task':bad['query']+='；再比较技术方案'
            if change=='report':bad['report']+='\n该代理师能力最强。'
            if change=='evidence':bad['evidence'][0]['rows'][0][0]='OTHER'
            if change=='permissions':bad['required_sources']=['web_search']
            self.assertFalse(program_review_eligible(bad),change)

    def test_closed_monthly_list_and_due_keep_scope(self):
        from casework.review import review
        queries=['统计客户甲（模拟）的2026-09至2026-10每月实际收案数量和已核实实际交局数量',
                 '列出客户甲（模拟）的全部案件及负责人和三项计划期限',
                 '以2026-10-27为基准日，列出初稿计划期限已逾期的全部在办案件']
        for query in queries:
            request=closed_request(query)
            self.assertIsNotNone(request,query)
            _,result=self.execute(request.model_dump());result['query']=query
            self.assertEqual(result['business_check']['status'],'passed',result['business_check']['issues'])
            self.assertTrue(program_review_eligible(result),query)
            with patch('agent.llm.get_llm',side_effect=AssertionError('unexpected model')):
                self.assertIsNone(asyncio.run(review(result)))
        for suffix in ['；计算增长原因','，并判断创造性','；只查PDF','；不要查数据库']:
            self.assertIsNone(closed_request(queries[0]+suffix))

    def test_complete_paging_and_bound_do_not_hide_more_rows(self):
        rows=[{'case_id':f'X-{i}','case_key':f'k{i}'} for i in range(525)]
        def page(**kw):
            a,b=kw['offset'],kw['offset']+kw['limit']
            return {'rows':rows[a:b],'total':len(rows),'has_more':b<len(rows)}
        with patch('casework.analytics.cases_page',side_effect=page):
            result=analytics.complete_cases_page()
            self.assertEqual(len(result['rows']),500)
            self.assertTrue(result['has_more'])
            self.assertEqual(result['pages_read'],3)

    def test_material_packet_collects_all_current_files_and_marks_budget(self):
        from api import context,source_policy
        from casework.materials import collect_case_materials
        context.set_session('packet-test',str(self.base));source_policy.configure(['knowledge'],[])
        try:
            with patch('api.evidence.add',return_value='S1'):
                full=json.loads(collect_case_materials.invoke({'case_id':'DEMO-2026-013'}))
                small=json.loads(collect_case_materials.invoke({'case_id':'DEMO-2026-013','max_chars':1000}))
            self.assertTrue(full['documents'])
            self.assertTrue(all(d.get('revision') for d in full['documents']))
            self.assertTrue(any(d['kind'] in ('application','disclosure') and d['complete_published_text'] for d in full['documents']))
            self.assertLessEqual(small['characters_read'],1000)
            self.assertTrue(any(not d['complete_published_text'] for d in small['documents']))
            self.assertIn('不证明',full['scope'])
        finally:context.reset_session()

    def test_versioned_cache_reuses_across_rounds_but_not_changed_scope_or_data(self):
        from api import context,evidence,source_policy
        from casework import read_cache
        context.set_session('reuse-test',str(self.base));source_policy.configure(['db_query'],[])
        entries=[{'id':'S1','type':'database','rows':[['value']]}]
        raw=json.dumps({'evidence_id':'S1','rows':[['value']]})
        try:
            with patch.object(evidence,'snapshot',return_value=entries):
                key=read_cache.key('query_department_cases',{'operation':'cases'})
                read_cache.put(key,raw);context.set_round(2)
                self.assertEqual(read_cache.get(read_cache.key('query_department_cases',{'operation':'cases'})),raw)
                empty_key=read_cache.key('query_department_cases',{'operation':'summary'})
                read_cache.put(empty_key,json.dumps({'evidence_id':'S1','requires_user_confirmation':True}))
                self.assertIsNone(read_cache.get(empty_key))
                entries[0]['rows']=[['changed']]
                self.assertIsNone(read_cache.get(key))
                source_policy.configure([],['db_query'])
                self.assertIsNone(read_cache.key('query_department_cases',{'operation':'cases'}))
                source_policy.configure(['db_query'],[])
                with store.db(write=True) as conn:
                    conn.execute('UPDATE roots SET generation=generation+1')
                self.assertNotEqual(key,read_cache.key('query_department_cases',{'operation':'cases'}))
        finally:context.reset_session()
        context.set_session('reuse-test',str(self.base))
        self.assertFalse(context.task_read_cache()['entries'])
        context.reset_session()

    def test_four_strategies_describe_executor_not_authority(self):
        from casework.workflow import DepartmentRequest
        self.assertEqual(strategy(closed_request('查DEMO-2026-013的负责人是谁'))['kind'],'direct')
        self.assertEqual(strategy(DepartmentRequest(operation='case_brief',query='DEMO-2026-013'))['kind'],'workflow')
        self.assertEqual(strategy(DepartmentRequest(operation='archive',query='HNSW'))['kind'],'conditional')
        self.assertEqual(strategy(None)['kind'],'agent')
        self.assertEqual(strategy(closed_request('查DEMO-2026-013的负责人是谁'),uploads=True)['kind'],'agent')

    def test_full_session_direct_path_never_invokes_model(self):
        from contextlib import ExitStack
        from agent import main_agent
        from api import storage,context,evidence
        queries=['查DEMO-2026-013的负责人和发明名称？',
                 '以2026-10-27为基准日，列出初稿计划期限已逾期的全部在办案件']
        with ExitStack() as stack:
            stack.enter_context(patch.object(storage,'_DB_PATH',self.base/'app.db'))
            stack.enter_context(patch.object(main_agent.rule_engine,'current',return_value={}))
            stack.enter_context(patch.object(main_agent,'_prepare_session'))
            stack.enter_context(patch.object(main_agent.path_utils,'get_upload_dir',return_value=self.base/'uploads'))
            model=stack.enter_context(patch('agent.llm.get_llm',side_effect=AssertionError('unexpected model')))
            stack.enter_context(patch.object(main_agent,'get_llm',side_effect=AssertionError('unexpected model')))
            stack.enter_context(patch.object(main_agent,'main_agent_run',AsyncMock(side_effect=AssertionError('unexpected Agent'))))
            query=queries[0]
            storage.init_db();storage.create_run('direct-session',query,query,False,1)
            context.set_session('direct-session',str(self.base));evidence.reset('direct-session')
            try:
                answer=asyncio.run(main_agent._run_session(query,'direct-session',self.base,1))
                self.assertIn(store.get_case(self.ident+':DEMO-2026-013')['title'],answer)
                run=storage.get_run('direct-session')
                summary=next(e['data'] for e in run['events'] if e['event']=='quality_summary')
                self.assertTrue(summary['passed'],summary)
                metrics=next(e['data'] for e in run['events'] if e['event']=='execution_metrics')
                self.assertEqual(metrics['model_calls'],0)
                model.assert_not_called()
                query=queries[1]
                storage.create_run('due-session',query,query,False,1)
                context.set_session('due-session',str(self.base));evidence.reset('due-session')
                answer=asyncio.run(main_agent._run_session(query,'due-session',self.base,1))
                self.assertNotIn('【未达标说明】',answer)
                self.assertNotIn('## 兜底结果说明',answer)
                run=storage.get_run('due-session')
                summary=next(e['data'] for e in run['events'] if e['event']=='quality_summary')
                self.assertTrue(summary['passed'],summary)
                model.assert_not_called()
            finally:
                context.clear_tool_budgets('direct-session');context.reset_session();evidence.clear('direct-session')
                context.clear_tool_budgets('due-session');evidence.clear('due-session')


class ParallelAndTelemetryTests(unittest.TestCase):
    def test_parallel_tasks_are_bounded_ordered_and_cancelled_together(self):
        from casework.parallel import independent_pair
        async def scenario():
            running=0;peak=0
            async def work(value):
                nonlocal running,peak
                running+=1;peak=max(peak,running)
                await asyncio.sleep(.01)
                running-=1
                return value
            self.assertEqual(await independent_pair(lambda:work(1),lambda:work(2)),[1,2])
            self.assertEqual(peak,2)
            closed=asyncio.Event()
            async def fail():
                await asyncio.sleep(.01)
                raise ValueError('failure')
            async def wait():
                try:await asyncio.sleep(10)
                finally:closed.set()
            with self.assertRaises(ValueError):await independent_pair(fail,wait)
            self.assertTrue(closed.is_set())
        asyncio.run(scenario())

    def test_usage_summary_deduplicates_provider_calls(self):
        from api.telemetry import summarize
        row={'event':'model_call_end','call_id':'x','usage_available':True,
             'elapsed_seconds':1,'status':'ok','usage':{'input_tokens':10,'output_tokens':2,'total_tokens':12}}
        stats=summarize([row,row,{'event':'model_usage','usage':row['usage']},
                         {'event':'tool_cache_hit'}])
        self.assertEqual(stats['model_calls'],1)
        self.assertEqual(stats['total_tokens'],12)
        self.assertEqual(stats['cache_hits'],1)
        self.assertIsNone(stats['human_active_seconds'])
