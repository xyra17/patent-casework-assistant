import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from api import context, source_policy, storage, task_sources, server
from agent import main_agent


class TaskSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(storage, '_DB_PATH', Path(self.tmp.name) / 'app.db')
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.output_patch = patch.object(server.path_utils, 'OUTPUT_DIR', Path(self.tmp.name)/'output')
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)
        self.privacy_patch = patch('casework.privacy.protected',return_value=False)
        self.privacy_patch.start()
        self.addCleanup(self.privacy_patch.stop)
        storage.init_db()
        self.addCleanup(context.reset_session)

    def test_api_defaults_and_empty_selection_are_distinct(self):
        self.assertEqual(server.TaskRequest(query='分析').allowed_sources, ['db_query','knowledge'])
        self.assertEqual(server.TaskRequest(query='分析',allowed_sources=[]).allowed_sources, [])
        for value in (None, ['sql_console'], ['db_query','knowledge','web_search','web_search']):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                server.TaskRequest(query='分析',allowed_sources=value)

    def test_selection_survives_reload_and_history_snapshots(self):
        first = storage.create_run('scope-history','分析','分析',False,allowed_sources=['knowledge'])
        self.assertEqual(storage.get_run('scope-history')['allowed_sources'], ['knowledge'])
        storage.create_run('scope-history','分析','分析',False,allowed_sources=['db_query','web_search'])
        self.assertEqual(storage.get_run('scope-history')['allowed_sources'], ['db_query','web_search'])
        self.assertEqual(storage.get_execution('scope-history',first['execution_id'])['allowed_sources'], ['knowledge'])

    def test_old_records_do_not_invent_past_permissions(self):
        storage.create_run('old','分析','分析',False)
        with storage._connect() as connection:
            connection.execute('UPDATE runs SET allowed_sources_json=NULL WHERE thread_id=?', ('old',))
        self.assertIsNone(storage.get_run('old')['allowed_sources'])
        storage.create_run('old','分析','分析',False)
        self.assertEqual(storage.get_run('old')['allowed_sources'], ['db_query','knowledge'])

    def test_no_plan_reconfiguration_can_expand_user_selection(self):
        context.set_session('blocked',self.tmp.name)
        context.set_round(1)
        with task_sources.scope(['db_query']):
            for _ in range(2):
                policy = source_policy.configure(task_sources.SOURCES)
                self.assertEqual(policy.active, frozenset({'db_query'}))
                self.assertEqual(policy.forbidden, frozenset({'knowledge','web_search'}))
                self.assertIn('禁止执行',source_policy.source_denial('web_search'))
            policy=source_policy.configure(['db_query'],['db_query'])
            self.assertFalse(policy.active)

    def test_deployment_privacy_can_further_restrict_web(self):
        with patch('casework.privacy.protected',return_value=True), task_sources.scope(['web_search']):
            self.assertFalse(source_policy.configure(['web_search']).active)
            options=server.task_source_options()
            self.assertFalse(options['web_search']['available'])
            self.assertIn('保密模式', options['web_search']['reason'])

    def test_unselected_tools_stop_before_io(self):
        import tools.internet_search_tool as web
        import tools.knowledge_base_tool as kb
        import tools.mysql_tools as db
        context.set_session('no-sources',self.tmp.name)
        context.set_round(1)
        with task_sources.scope([]), patch.object(web._client,'search') as search, \
                patch.object(kb,'_retrieve_sync') as retrieve, patch.object(db,'_get_connection') as connect:
            source_policy.configure(task_sources.SOURCES)
            self.assertIn('禁止执行',web.internet_search.invoke({'query':'test'}))
            self.assertIn('禁止执行',db.execute_sql_query('SELECT 1'))
            self.assertIn('禁止执行',asyncio.run(kb.search_knowledge_base.ainvoke({'query':'test'})))
        search.assert_not_called()
        retrieve.assert_not_called()
        connect.assert_not_called()

    def test_plan_and_text_limits_take_intersection(self):
        plan=main_agent.TaskPlan(task_type='qa',require_report=False,require_pdf=False,required_sources=['db_query','knowledge','web_search'])
        with task_sources.scope(['db_query']):
            normalized=main_agent._normalize_plan(plan,'只查知识库，不联网')
        self.assertEqual(normalized.required_sources,[])
        self.assertEqual(set(normalized.forbidden_sources),task_sources.SOURCES)
        self.assertTrue(any('数据源设置未开放' in text for text in normalized.assumptions))

    def test_direct_runner_defaults_do_not_grant_web_even_if_prompt_requests_it(self):
        async def fake_session(*args):
            self.assertEqual(task_sources.allowed_sources(), ['db_query','knowledge'])
            source_policy.configure(['web_search'])
            self.assertFalse(source_policy.current_policy().active)
            return '请先开启联网检索'
        with patch.object(main_agent,'_run_session',side_effect=fake_session):
            self.assertEqual(asyncio.run(main_agent.run_deep_agent('联网查询最新消息','direct')), '请先开启联网检索')
        self.assertFalse(task_sources.configured())

    def test_async_and_worker_thread_permissions_are_isolated(self):
        async def child(values):
            with task_sources.scope(values):
                await asyncio.sleep(0)
                actual=await asyncio.to_thread(task_sources.allowed_sources)
                self.assertEqual(actual,values)
        async def run():
            await asyncio.gather(child([]),child(['web_search']),child(['knowledge']))
        asyncio.run(run())
        self.assertFalse(task_sources.configured())

    def test_server_runner_uses_persisted_selection_without_new_prompt_instructions(self):
        storage.create_run('server-scope','统计订单','统计订单',False,allowed_sources=[])
        async def run(*args):
            self.assertEqual(task_sources.allowed_sources(),[])
            return '当前仅使用问题与附件，未开放业务数据库。'
        with patch.object(server,'run_deep_agent',side_effect=run), \
                patch.object(server,'build_query_result',new_callable=AsyncMock,return_value={}), \
                patch('api.execution_files.write_manifest'):
            asyncio.run(server._run_task('server-scope','统计订单'))
        self.assertFalse(task_sources.configured())
        self.assertEqual(storage.get_run('server-scope')['status'],'completed')

    def test_api_submission_persists_defaults_and_explicit_selection(self):
        from fastapi.testclient import TestClient
        with patch('casework.privacy.ensure_model_allowed'), \
                patch.object(server.rule_engine,'resolve',new_callable=AsyncMock,return_value={'conflicts':[]}), \
                patch.object(server,'_run_task',new_callable=AsyncMock), \
                patch.object(server.path_utils,'OUTPUT_DIR',Path(self.tmp.name)/'output'):
            client=TestClient(server.app)
            for index, values in enumerate((None, [], ['knowledge'], ['web_search'])):
                body={'query':'统计订单','thread_id':f'selection{index}'}
                if values is not None:body['allowed_sources']=values
                response=client.post('/api/task',json=body)
                self.assertEqual(response.status_code,200,response.text)
                expected=['db_query','knowledge'] if values is None else values
                self.assertEqual(response.json()['allowed_sources'],expected)
                self.assertEqual(storage.get_run(body['thread_id'])['allowed_sources'],expected)
            self.assertEqual(client.post('/api/task',json={'query':'test','allowed_sources':['write_sql']}).status_code,422)
