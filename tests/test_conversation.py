"""Deterministic conversation acceptance: isolated DB/files, no external model or business writes."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from api import storage, server, conversation, evidence, context, recycle_bin
from api.conversation import Followup, Resolution, ConditionChange
from rules import store as rule_store, engine as rule_engine
from utils import path_utils


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [patch.object(storage, '_DB_PATH', self.base/'app.db'),
                        patch.object(path_utils, 'OUTPUT_DIR', self.base/'output'),
                        patch.object(path_utils, 'UPLOAD_DIR', self.base/'upload'),
                        patch.dict(os.environ, {'CASEWORK_DB_PATH':str(self.base/'cases.db'), 'DATA_MODE':'demo'}),
                        patch.object(rule_engine, 'resolve', AsyncMock(return_value={'applied':[], 'conflicts':[], 'overridden':[]}))]
        for item in self.patches:
            item.start()
        storage.init_db()
        self.client = TestClient(server.app)
        self.make_run()

    def tearDown(self):
        self.client.close()
        context.reset_session()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def make_run(self, thread='chat', answer='| 案号 | 承办人 |\n|---|---|\n| DEMO-2026-001 | 张三 |\n| DEMO-2026-002 | 李四 |', conditions=None):
        storage.create_run(thread, '列出在办案件', '列出在办案件', False)
        storage.append_event(thread, {'event':'plan_created', 'data':{'department_request':conditions or {'stage':'在办'}}})
        storage.append_event(thread, {'event':'task_result', 'data':{'result':answer, 'degraded':False,
            'evidence':[{'id':'S1','type':'database','title':'模拟清单','revision':'v1','rows':[['DEMO-2026-001'],['DEMO-2026-002']]}]}})
        return storage.get_run(thread)

    def body(self, text='这些案件里只看张三负责的', mode='continue', thread='chat', **changes):
        run = storage.get_run(thread)
        return Followup(text=text, mode=mode, expected_execution_id=run['execution_id'],
                        expected_turn_id=conversation.latest(thread)['turn_id'], **changes)

    def prepare(self, body=None, proposal=None, thread='chat'):
        body = body or self.body()
        proposal = proposal or Resolution(task='查询张三负责的在办案件', changes=[ConditionChange(key='owner',value='张三',quote='张三')])
        with patch.object(conversation, 'resolve', AsyncMock(return_value=proposal)):
            return asyncio.run(conversation.prepare(storage.get_run(thread), body))

    def submit(self, body=None, proposal=None, execute=None, sources=None):
        body = body or self.body()
        async def finish(thread_id, query, rounds=3):
            await server.monitor.report(thread_id, 'task_result', '模拟执行结束', {'result':'DEMO-2026-001 张三 [S1]',
                'evidence':[{'id':'S1','title':'本轮实时查询','revision':'v2','type':'database'}], 'degraded':False})
        with patch.object(conversation, 'resolve', AsyncMock(return_value=proposal or Resolution(task='查询张三的在办案件'))), \
             patch.object(server, '_run_task', side_effect=execute or finish):
            return self.client.post('/api/task', json={'query':'列出在办案件','thread_id':'chat',
                'allowed_sources':sources if sources is not None else ['db_query','knowledge'], 'followup':body.model_dump()})

    def test_initial_turn_has_immutable_answer_evidence_and_display_order(self):
        turn = conversation.latest('chat')
        self.assertEqual(turn['ordinal'],1)
        self.assertEqual(turn['state']['displayed_case_ids'],['DEMO-2026-001','DEMO-2026-002'])
        self.assertEqual(turn['state']['conditions']['stage'],'在办')
        self.assertEqual(turn['snapshot']['evidence'][0]['revision'],'v1')

    def test_scope_mentions_in_an_empty_result_are_not_selected_cases(self):
        self.assertEqual(conversation.displayed_cases('结果为 0 件；案号 DEMO-2026-001、DEMO-2026-002 均未命中。'),[])
        self.assertEqual(conversation.displayed_cases('| 案号 |\n|---|\n| DEMO-2026-002 |\n\n排除 DEMO-2026-001'),['DEMO-2026-002'])

    def test_followup_inherits_scope_and_applies_only_quoted_change(self):
        prepared = self.prepare()
        self.assertEqual(prepared['state']['case_ids'],['DEMO-2026-001','DEMO-2026-002'])
        self.assertEqual(prepared['state']['conditions'],{'stage':'在办','owner':'张三'})
        self.assertIn('本轮案件范围仅限案号',prepared['task'])

    def test_second_case_is_bound_to_display_order_not_retrieval_order(self):
        result = self.prepare(self.body('第二个案件为什么相关？'), Resolution(task='核对第二个案件相关性原文'))
        self.assertEqual(result['state']['case_ids'],['DEMO-2026-002'])

    def test_ambiguous_ordinal_requires_clarification_and_never_calls_model(self):
        self.make_run('tables', '第一组 DEMO-2026-001\n\n第二组 DEMO-2026-002')
        with patch.object(conversation, 'resolve', AsyncMock()) as model:
            with self.assertRaisesRegex(ValueError,'哪张表'):
                asyncio.run(conversation.prepare(storage.get_run('tables'),self.body('第二个案件',thread='tables')))
        model.assert_not_called()

    def test_fabricated_case_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'没有来源的案号'):
            self.prepare(proposal=Resolution(task='案件查询',case_ids=['DEMO-2026-999']))

    def test_model_cannot_expand_or_shrink_host_bound_collection(self):
        with self.assertRaisesRegex(ValueError,'改变了已确认案件范围'):
            self.prepare(proposal=Resolution(task='案件查询',case_ids=['DEMO-2026-001']))

    def test_unquoted_condition_change_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'未绑定本轮原话'):
            self.prepare(proposal=Resolution(task='案件查询',changes=[ConditionChange(key='owner',value='王五',quote='王五')]))

    def test_blank_followup_is_not_accepted(self):
        with self.assertRaises(ValueError):self.body('   ')

    def test_quoted_text_cannot_be_used_to_invent_a_different_owner(self):
        with self.assertRaisesRegex(ValueError,'必须来自本轮原话'):
            self.prepare(proposal=Resolution(task='案件查询',changes=[ConditionChange(key='owner',value='王五',quote='张三')]))

    def test_model_cannot_delete_a_condition_without_explicit_removal(self):
        with self.assertRaisesRegex(ValueError,'取消继承条件'):
            self.prepare(proposal=Resolution(task='案件查询',changes=[ConditionChange(key='stage',value=None,quote='只看张三负责的')]))

    def test_explicit_removal_preserves_other_conditions(self):
        result = self.prepare(self.body('取消阶段限制'),Resolution(task='查询全部阶段案件',changes=[ConditionChange(key='stage',value=None,quote='取消阶段限制')]))
        self.assertEqual(result['state']['conditions'],{})

    def test_new_topic_resets_scope_without_model_or_memory_promotion(self):
        with patch.object(conversation, 'resolve', AsyncMock()) as model:
            prepared=asyncio.run(conversation.prepare(storage.get_run('chat'),self.body('统计九月收案量',mode='new_topic')))
        model.assert_not_called()
        self.assertEqual(prepared['state']['conditions'],{})
        self.assertIsNone(prepared['state']['case_ids'])
        self.assertEqual(rule_store.list_rules(),[])

    def test_stale_parent_fails_before_model(self):
        body=self.body();body.expected_execution_id='stale'
        with patch.object(conversation,'resolve',AsyncMock()) as model:
            with self.assertRaisesRegex(ValueError,'会话已更新'):
                asyncio.run(conversation.prepare(storage.get_run('chat'),body))
        model.assert_not_called()

    def test_transaction_rechecks_parent_after_resolution(self):
        body=self.body();prepared=self.prepare(body)
        storage.create_run('chat','ignored','ignored',False)
        with self.assertRaisesRegex(ValueError,'会话已更新'):
            storage.create_run('chat','ignored','ignored',False,conversation_prepared=prepared,followup=body)
        self.assertEqual(conversation.page('chat')['total'],2)

    def test_saved_full_task_revision_resets_old_conversation_filters(self):
        revision=storage.save_intent_revision('chat','只看李四负责的全部阶段案件',None)
        storage.create_run('chat','ignored','ignored',False,expected_revision_id=revision['id'])
        turn=conversation.latest('chat')
        self.assertEqual(turn['kind'],'revision')
        self.assertEqual(turn['state']['conditions'],{})
        self.assertIsNone(turn['state']['case_ids'])
        self.assertEqual(turn['state']['goal'],'只看李四负责的全部阶段案件')

    def test_real_http_followup_preserves_both_results_and_versions(self):
        first=conversation.latest('chat')
        response=self.submit()
        self.assertEqual(response.status_code,200,response.text)
        turns=conversation.page('chat')['turns']
        self.assertEqual(len(turns),2)
        self.assertEqual(turns[0],first)
        self.assertEqual(turns[1]['parent_turn_id'],first['turn_id'])
        self.assertEqual(turns[1]['snapshot']['evidence'][0]['revision'],'v2')
        self.assertNotEqual(turns[0]['execution_id'],turns[1]['execution_id'])

    def test_source_permission_cannot_expand_on_followup(self):
        response=self.submit(sources=['db_query','knowledge','web_search'])
        self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(conversation.page('chat')['total'],1)

    def test_narrowed_source_selection_redacts_old_answers_before_model_resolution(self):
        with patch.object(conversation,'resolve',AsyncMock(return_value=Resolution(task='仅按当前允许来源回答'))) as model:
            asyncio.run(conversation.prepare(storage.get_run('chat'),self.body('请重新查询'),allowed_sources=[]))
        recent=model.call_args.args[0]['recent_turns']
        self.assertTrue(all(t['answer_excerpt']=='[该轮来源当前未开放，禁止复用事实]' for t in recent))

    def test_cross_conversation_turn_is_not_readable_or_referenceable(self):
        self.make_run('other')
        foreign=conversation.latest('other')['turn_id']
        self.assertEqual(self.client.get('/api/runs/chat/turns/'+foreign).status_code,404)
        self.assertEqual(self.submit(self.body(reference_turn_id=foreign)).status_code,404)

    def test_question_is_persisted_without_overwriting_answer(self):
        prior=storage.get_run('chat')['result']
        response=self.submit(proposal=Resolution(question='请明确初稿期限还是交局期限？'))
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(storage.get_run('chat')['result'],prior)
        storage.init_db()
        data=self.client.get('/api/runs/chat/conversation').json()
        self.assertIn('初稿期限',data['pending']['question'])
        self.assertEqual(data['total'],1)

    def test_clarification_is_passed_to_next_resolution_and_cleared_on_acceptance(self):
        self.submit(proposal=Resolution(question='请选择期限字段'))
        with patch.object(conversation,'resolve',AsyncMock(return_value=Resolution(task='查询初稿期限'))) as model:
            asyncio.run(conversation.prepare(storage.get_run('chat'),self.body('初稿期限')))
            self.assertIn('请选择期限字段',str(model.call_args.args[0]['pending_clarification']))
        self.assertEqual(self.submit(self.body('初稿期限')).status_code,200)
        self.assertIsNone(conversation.pending('chat'))

    def test_history_is_exact_snapshot_without_agent_or_new_facts(self):
        body=self.body('回看',mode='history')
        prepared=self.prepare(body)
        storage.create_run('chat','ignored','ignored',False,conversation_prepared=prepared,followup=body)
        with patch.object(server,'run_deep_agent',AsyncMock()) as agent:
            asyncio.run(server._run_task('chat','回看'))
        agent.assert_not_called()
        run=storage.get_run('chat')
        self.assertIn('未重新核验',run['result'])
        self.assertIn('DEMO-2026-002',run['result'])
        self.assertEqual(run['evidence'][0]['revision'],'v1')

    def test_narrowed_permissions_do_not_expose_history_snapshot(self):
        response=self.submit(self.body(mode='history'),sources=['db_query'])
        self.assertEqual(response.status_code,403,response.text)

    def test_restart_and_reopen_preserve_turns_and_finalize_interruption(self):
        storage.create_run('chat','ignored','ignored',False)
        storage.init_db();storage.mark_interrupted()
        self.assertEqual(conversation.latest('chat')['status'],'failed')
        self.assertIn('中断',conversation.latest('chat')['snapshot']['error'])
        self.assertEqual(conversation.page('chat')['turns'][0]['snapshot']['evidence'][0]['revision'],'v1')

    def test_cancelled_turn_cannot_be_overwritten_by_late_result(self):
        storage.create_run('chat','ignored','ignored',False)
        storage.append_event('chat',{'event':'task_cancelled','data':{}})
        before=conversation.latest('chat')
        storage.append_event('chat',{'event':'task_result','data':{'result':'late'}})
        self.assertEqual(conversation.latest('chat'),before)

    def test_soft_delete_restore_and_purge_include_conversation_memory(self):
        original=conversation.page('chat')
        recycle_bin.move('chat')
        self.assertEqual(self.client.get('/api/runs/chat/conversation').status_code,410)
        recycle_bin.restore('chat')
        self.assertEqual(conversation.page('chat'),original)
        recycle_bin.move('chat');recycle_bin.purge('chat')
        self.assertEqual(conversation.page('chat')['total'],0)

    def test_rule_promotion_requires_confirmation_and_keeps_turn_provenance(self):
        turn=conversation.latest('chat')
        with patch('agent.llm.get_llm',side_effect=RuntimeError('offline')):
            draft=self.client.post('/api/long-term-rules/draft',json={'thread_id':'chat','turn_id':turn['turn_id']})
        self.assertEqual(draft.status_code,200,draft.text)
        self.assertEqual(rule_store.list_rules(),[])
        rule={'title':'答复格式','category':'preference','subject':'答复格式','content':'案件清单先列案号和承办人',
              'match_terms':['案件'],'scope_note':'案件清单','active':True,
              'source_thread_id':'chat','source_turn_id':turn['turn_id']}
        self.assertEqual(self.client.post('/api/long-term-rules',json=rule).status_code,400)
        response=self.client.post('/api/long-term-rules',json={**rule,'confirmed':True})
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['source_snapshot'],turn['user_text'])
        self.assertEqual(response.json()['source_turn_id'],turn['turn_id'])
        storage.delete_run('chat')
        self.assertEqual(rule_store.get_rule(response.json()['id'])['source_snapshot'],turn['user_text'])

    def test_pagination_keeps_turn_order_and_latest_token(self):
        for i in range(4):
            storage.create_run('chat','ignored','ignored',False)
            storage.append_event('chat',{'event':'task_result','data':{'result':str(i)}})
        first=self.client.get('/api/runs/chat/conversation?limit=2').json()
        self.assertEqual([t['ordinal'] for t in first['turns']],[4,5])
        second=self.client.get('/api/runs/chat/conversation?limit=2&before='+str(first['next_before'])).json()
        self.assertEqual([t['ordinal'] for t in second['turns']],[2,3])
        self.assertEqual(first['current_turn_id'],second['current_turn_id'])

    def test_legacy_run_is_migrated_without_llm_and_has_snapshot(self):
        with storage._connect() as conn:
            conn.execute('DELETE FROM conversation_turns WHERE thread_id=?',('chat',))
            conn.execute('UPDATE runs SET execution_id=NULL WHERE thread_id=?',('chat',))
        response=self.client.get('/api/runs/chat/conversation')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['total'],1)
        self.assertEqual(response.json()['turns'][0]['snapshot']['evidence'][0]['revision'],'v1')


class ConversationCaseScopeTests(unittest.TestCase):
    def test_case_scope_filters_sqlite_reads_and_resets_between_tasks(self):
        from casework import store
        from casework.conversation_scope import scope
        from tests.test_department_casework import DepartmentCaseworkTests
        fixture=DepartmentCaseworkTests()
        fixture.setUp()
        try:
            all_cases=store.list_cases()
            self.assertEqual(len(all_cases),34)
            with scope(['DEMO-2026-001','DEMO-2026-002']):
                self.assertEqual({c['case_id'] for c in store.list_cases()},{'DEMO-2026-001','DEMO-2026-002'})
                with scope([]):self.assertEqual(store.list_cases(),[])
                self.assertEqual(len(store.list_cases()),2)
            with scope(None,{'client':'客户甲（模拟）','owner':'周行（模拟）'}):
                self.assertEqual(store.list_cases(),[])
                from casework import analytics
                self.assertEqual(analytics.cases_page()['total'],0)
            with scope(None,{'client':'客户乙','owner':'林澄'}):
                self.assertTrue(store.list_cases())
                self.assertTrue(all(c['client_alias']=='客户乙（模拟）' and c['owner_name']=='林澄（模拟）' for c in store.list_cases()))
            self.assertEqual(len(store.list_cases()),34)
        finally:
            fixture.tearDown();fixture.doCleanups()

    def test_free_sql_and_generic_semantic_query_cannot_bypass_selected_cases(self):
        from casework.conversation_scope import scope
        from tools.mysql_tools import execute_sql_query
        from tools.semantic_query import run_semantic_query,run_semantic_drilldown
        with scope(['DEMO-2026-001']), patch('tools.mysql_tools._get_connection') as connection:
            self.assertIn('案件',execute_sql_query('SELECT * FROM cw_cases'))
            self.assertIn('案件',run_semantic_query('total_count'))
            self.assertIn('案件',run_semantic_drilldown('x','x','x','x'))
            connection.assert_not_called()


if __name__ == '__main__':
    unittest.main()
