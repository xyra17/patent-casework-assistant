"""A material recommendation gap needs a durable user decision, never a silent fallback."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from api import clarification, context, server, storage
from casework.workflow import DepartmentRequest, DepartmentIntent
from tests import test_department_contracts as fixtures


class ClarificationTests(unittest.TestCase):
    execute = fixtures.ContractTests.execute

    def setUp(self):
        fixtures.ContractTests.setUp(self)
        self.patches = [patch.object(storage,'_DB_PATH',self.base/'app.db'),
                        patch.object(server.path_utils,'OUTPUT_DIR',self.base/'output'),
                        patch.object(server.path_utils,'UPLOAD_DIR',self.base/'uploads')]
        for item in self.patches:item.start()
        storage.init_db()

    def tearDown(self):
        context.reset_session()
        for item in reversed(self.patches):item.stop()
        fixtures.ContractTests.tearDown(self)

    def held(self,thread='held'):
        _,result=self.execute(dict(operation='recommend',query='量子纠错做逻辑量子比特'))
        storage.create_run(thread,'谁适合接这个量子纠错案子','谁适合接这个量子纠错案子',False,round_mode='auto')
        storage.append_event(thread,{'event':'task_clarification','data':{
            'clarification':result['clarification'],'result':result['report'],'evidence':result['evidence']}})
        return storage.get_run(thread)

    def answer(self,run,choice='workload_only',text=''):
        return dict(execution_id=run['execution_id'],question_id=run['clarification']['id'],choice=choice,text=text)

    def test_unknown_mechanism_is_searched_then_held_without_ranked_answer(self):
        from casework.search import search
        with patch('casework.search.search',wraps=search) as lookup:
            _,result=self.execute(dict(operation='recommend',query='量子纠错做逻辑量子比特'))
        self.assertEqual(lookup.call_count,1)
        self.assertEqual(lookup.call_args.args[0],'量子纠错做逻辑量子比特')
        self.assertEqual(result['business_check']['status'],'awaiting_input')
        self.assertIn('已暂停推荐',result['report'])
        self.assertNotIn('许宁',result['report'])
        self.assertEqual({x['id'] for x in result['clarification']['options']},{'supplement','workload_only'})

    def test_known_technology_with_no_qualified_history_also_needs_decision(self):
        from casework import analytics
        original=analytics.recommend
        def no_history(*args,**kwargs):
            r=original(*args,**kwargs)
            for c in r['candidates']:c['history_matches']=[];c['history_match_count']=0
            return r
        with patch('casework.analytics.recommend',side_effect=no_history):
            _,result=self.execute(dict(operation='recommend',query='向量检索'))
        self.assertEqual(result['clarification']['reason'],'historical_evidence_missing')
        self.assertNotIn('描述不清',result['report'])

    def test_question_distinguishes_possible_cases_from_confirmed_experience(self):
        request=DepartmentRequest(operation='recommend',query='量子纠错 逻辑量子比特')
        question=clarification.recommendation_question(request,{'technical_intent_unresolved':True},
            {'results':[{'case_id':'C1','sources':[{'evidence_id':'S1'},{'evidence_id':'S2'}]}]})
        self.assertIn('1个可能相关的案件',question['detail'])
        self.assertIn('不足以判断',question['detail'])
        self.assertEqual(question['reason'],'technical_mapping_unconfirmed')
        self.assertEqual(question['lookup_evidence_ids'],['S1','S2'])
        self.assertIn('查找内容：量子纠错 逻辑量子比特',clarification.question_report(question))

    def test_registered_positive_history_still_completes_without_prompt(self):
        _,result=self.execute(dict(operation='recommend',query='模型蒸馏'))
        self.assertNotIn('clarification',result)
        self.assertEqual(result['business_check']['status'],'passed')

    def test_confirmed_workload_excludes_ranking_from_evidence_even_if_history_becomes_available(self):
        from casework.workflow import sources_for
        from tools.chart_utils import build_chart_items
        with clarification.scope({'choice':'workload_only'}):
            request,result=self.execute(dict(operation='recommend',query='模型蒸馏',recommendation_mode='workload_only'))
        self.assertEqual(sources_for(request),['db_query'])
        self.assertEqual(result['called_sources'],['db_query'])
        self.assertEqual(result['business_check']['status'],'passed',result['business_check'])
        self.assertTrue(result['evidence'])
        for item in result['evidence']:
            self.assertEqual(item['type'],'database')
            self.assertNotIn('排序分',item.get('columns',[]))
            self.assertNotIn('相关历史案件数量',item.get('columns',[]))
        self.assertNotIn('推荐依据：',result['report'])
        self.assertNotIn('historical_owner_evidence',result['business_delivery']['fields'])
        self.assertNotIn('排序分',json.dumps(build_chart_items(result['evidence'],'只看负荷'),ensure_ascii=False))

    def test_question_survives_reload_restart_and_is_not_counted_as_completed(self):
        run=self.held()
        storage.mark_interrupted()
        reloaded=storage.get_run('held')
        self.assertEqual(reloaded['clarification'],run['clarification'])
        self.assertEqual(reloaded['status'],'awaiting_input')
        stats=storage.run_page()['stats']
        self.assertEqual((stats['awaiting_input'],stats['completed'],stats['failed']),(1,0,0))
        storage.append_event('held',{'event':'task_result','data':{'result':'late automatic pass'}})
        self.assertEqual(storage.get_run('held')['status'],'awaiting_input')

    def test_only_current_question_can_be_answered_and_permissions_cannot_expand(self):
        run=self.held();good=self.answer(run)
        for changes in [dict(execution_id='stale'),dict(question_id='stale')]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                storage.create_run('held','','',False,clarification_answer={**good,**changes})
        with self.assertRaises(ValueError):
            storage.create_run('held','','',False,allowed_sources=['db_query','knowledge','web_search'],clarification_answer=good)
        with self.assertRaises(ValueError):
            storage.create_run('different','','',False,clarification_answer=good)
        storage.create_run('held','','',False,clarification_answer=good)
        resumed=storage.get_run('held')
        self.assertIsNone(resumed['clarification'])
        self.assertEqual(resumed['clarification_response']['choice'],'workload_only')
        previous=storage.get_execution('held',run['execution_id'])
        self.assertEqual(previous['status'],'awaiting_input')
        self.assertEqual(previous['clarification'],run['clarification'])
        with self.assertRaises(ValueError):storage.create_run('held','','',False,clarification_answer=good)

    def test_supplement_is_scoped_and_regular_rerun_does_not_reuse_approval(self):
        run=self.held()
        answer=self.answer(run,'supplement','这里其实是教师模型指导学生模型训练，参考软标签与蒸馏损失。')
        storage.create_run('held','','',False,clarification_answer=answer)
        current=storage.get_run('held')
        self.assertIn(answer['text'],current['execution_query'])
        self.assertEqual(current['query'],run['query'])
        self.assertEqual(current['intent_revisions'],[])
        storage.create_run('held','','',False)
        next_run=storage.get_run('held')
        self.assertIsNone(next_run['clarification_response'])
        self.assertNotIn(answer['text'],next_run['execution_query'])

    def test_unanswered_question_cannot_waive_hard_or_soft_checks(self):
        from agent import main_agent
        from casework.routing import RouteDecision
        request=DepartmentRequest(operation='recommend',query='量子纠错')
        context.set_session('orchestrated',str(self.base))
        with patch('casework.routing.select_route',AsyncMock(return_value=RouteDecision('matched','test',request))), \
             patch.object(main_agent,'_prepare_session'), \
             patch.object(main_agent.path_utils,'get_upload_dir',return_value=self.base/'missing'), \
             patch.object(main_agent.rule_engine,'current',return_value={}), \
             patch.object(main_agent,'soft_review',AsyncMock()) as review, \
             patch.object(main_agent.monitor,'report',AsyncMock()) as monitor:
            with self.assertRaises(clarification.ClarificationRequired):
                asyncio.run(main_agent._run_session('量子纠错谁接', 'orchestrated',self.base,1))
        review.assert_not_awaited()
        summary=[c.args[3] for c in monitor.await_args_list if c.args[1]=='quality_summary'][-1]
        self.assertFalse(summary['passed']);self.assertEqual(summary['stop_reason'],'awaiting_input')

    def test_successive_supplements_keep_the_entire_chain_but_regular_rerun_resets_it(self):
        run=self.held()
        first='使用量子纠错码编码逻辑量子比特。'
        storage.create_run('held','','',False,clarification_answer=self.answer(run,'supplement',first))
        question={**run['clarification'],'id':'second-question'}
        storage.append_event('held',{'event':'task_clarification','data':{
            'clarification':question,'result':'仍需补充','evidence':[]}})
        waiting=storage.get_run('held')
        second='请重点查找表面码和稳定子测量的历史材料。'
        response=clarification.validate_answer(waiting,self.answer(waiting,'supplement',second),waiting['allowed_sources'])
        preview=clarification.extend_query(storage.preview_execution_query('held','','',None),response,waiting)
        prepared=storage.create_run('held','','',False,clarification_answer=self.answer(waiting,'supplement',second))
        self.assertEqual(prepared['execution_query'],preview)
        self.assertEqual(preview.count(first),1)
        self.assertEqual(preview.count(second),1)
        storage.create_run('held','','',False)
        rerun=storage.get_run('held')['execution_query']
        self.assertNotIn(first,rerun)
        self.assertNotIn(second,rerun)

    def test_api_persists_question_and_resume_uses_only_confirmed_scope(self):
        from agent import main_agent
        from casework.routing import RouteDecision
        # Real orchestration, tools, storage and API task driver; only the model
        # extraction/review/projection are replaced with bounded deterministic stubs.
        for person in json.loads((fixtures.ROOT/'examples/department_shared_drive/expected.json').read_text())['people']:
            from casework import analytics
            analytics.save_person(**person)
        storage.create_run('api-held','量子纠错谁适合接','量子纠错谁适合接',False,round_mode='auto')
        async def reviewed(result):
            result['soft_review_status']='passed'
            return None
        async def projected(query,report,*args,**kwargs):return {'content':report,'status':'ready','evidence_ids':[]}
        request=DepartmentRequest(operation='recommend',query='量子纠错')
        with patch('casework.routing.select_route',AsyncMock(return_value=RouteDecision('matched','test',request))), \
             patch.object(main_agent.rule_engine,'current',return_value={}), \
             patch.object(main_agent,'soft_review',side_effect=reviewed), \
             patch.object(server,'build_query_result',side_effect=projected):
            asyncio.run(server._run_task('api-held','量子纠错谁适合接',1))
            held=storage.get_run('api-held')
            self.assertEqual(held['status'],'awaiting_input')
            self.assertFalse(any(e['event']=='task_result' for e in held['events']))
            answer=self.answer(held)
            prepared=storage.create_run('api-held','','',False,round_mode='auto',clarification_answer=answer)
            asyncio.run(server._run_task('api-held',prepared['execution_query'],1))
        finished=storage.get_run('api-held')
        self.assertEqual(finished['status'],'completed',finished['error'])
        self.assertFalse(finished['degraded'],finished['result'])
        self.assertIn('## 人员负荷',finished['result'])
        import re
        names=re.findall(r'^\| ([^|]+) \| \d+ \[S\d+\] ',finished['result'],re.M)
        self.assertTrue(names)
        self.assertEqual(names,sorted(names))
        self.assertNotIn('推荐依据：',finished['result'])
        self.assertEqual(finished['allowed_sources'],['db_query','knowledge'])

    def test_model_cannot_choose_its_own_workload_downgrade(self):
        self.assertNotIn('recommendation_mode',DepartmentIntent.model_fields)
        with self.assertRaises(ValueError):
            DepartmentIntent(requirements=[{'kind':'candidate_recommendation','quote':'谁接'}],recommendation_mode='workload_only')

    def test_general_agent_cannot_hide_same_tool_gap_in_a_completed_answer(self):
        from agent import main_agent
        from casework.routing import RouteDecision
        async def answer_with_gap(*args):
            clarification.record_recommendation_gap('量子纠错')
            return {'report':'按负荷建议许宁','evidence':[]}
        context.set_session('general-gap',str(self.base))
        with patch('casework.routing.select_route',AsyncMock(return_value=RouteDecision('general','complex'))), \
             patch.object(main_agent,'build_task_plan',AsyncMock(return_value=main_agent.TaskPlan(task_type='analysis',require_report=False,require_pdf=False,required_sources=['db_query','knowledge']))), \
             patch.object(main_agent,'main_agent_run',side_effect=answer_with_gap), \
             patch.object(main_agent,'_prepare_session'), \
             patch.object(main_agent.path_utils,'get_upload_dir',return_value=self.base/'missing'), \
             patch.object(main_agent.rule_engine,'current',return_value={}), \
             patch.object(main_agent,'soft_review',AsyncMock()) as reviewer, \
             patch.object(main_agent.monitor,'report',AsyncMock()):
            with self.assertRaises(clarification.ClarificationRequired) as caught:
                asyncio.run(main_agent._run_session('量子纠错谁接，还要对比技术方案', 'general-gap',self.base,1))
        reviewer.assert_not_awaited()
        self.assertEqual([x['id'] for x in caught.exception.question['options']],['supplement'])
        self.assertNotIn('按负荷建议许宁',caught.exception.report)

    def test_answer_is_rejected_if_intent_has_changed_since_question(self):
        run=self.held()
        revision=storage.save_intent_revision('held','改为统计九月收案',None)
        with self.assertRaises(ValueError):
            storage.create_run('held','','',False,expected_revision_id=revision['id'],clarification_answer=self.answer(run))
        self.assertEqual(storage.get_run('held')['status'],'awaiting_input')

    def test_waiting_api_never_publishes_unconfirmed_candidate_charts(self):
        from fastapi.testclient import TestClient
        self.held()
        with TestClient(server.app) as client:
            run=client.get('/api/runs/held').json()
        self.assertEqual(run['status'],'awaiting_input')
        self.assertEqual(run['charts'],[])


if __name__=='__main__':unittest.main()
