"""不调用外部 LLM/MySQL 的执行链路回归测试。"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

# 测试使用独立 SQLite，不能污染本地历史任务 data/app.db
_TEST_DB_DIR = tempfile.mkdtemp(prefix="tanyuan-test-db-")
os.environ["APP_DB_PATH"] = str(Path(_TEST_DB_DIR) / "app.db")

from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from agent import main_agent
from api import context, server, source_policy
from api.monitor import MonitorCallbackHandler
from tools import reflect_check
from tools.mysql_tools import execute_sql_query
from utils import path_utils
from utils.converter_utils import convert_markdown_to_pdf


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output = self.root / 'output'
        self.upload = self.root / 'upload'
        self.output.mkdir()
        self.upload.mkdir()
        # General orchestration tests must not discover real local department roots or call its router.
        self.patches = [patch('casework.store.roots', return_value=[]),
                        patch.object(server.storage, '_DB_PATH', self.root / 'app.db'),
                        patch.object(path_utils, 'OUTPUT_DIR', self.output),
                        patch.object(path_utils, 'UPLOAD_DIR', self.upload)]
        for p in self.patches:
            p.start()
        server.storage.init_db()
        # 单元测试默认不调用真实规划模型；需要规划逻辑的测试使用
        # self.real_build_task_plan / 单独 patch。
        self.real_build_task_plan = main_agent.build_task_plan
        self.default_plan = main_agent.TaskPlan(
            task_type='qa', require_report=False, require_pdf=False,
            required_sources=[], steps=['直接回答'], output_artifacts=[], assumptions=[],
        )
        self.plan_patch = patch.object(
            main_agent, 'build_task_plan', AsyncMock(return_value=self.default_plan)
        )
        self.plan_patch.start()
        self.patches.append(self.plan_patch)
        self.client = TestClient(server.app)

    def tearDown(self):
        context.reset_session()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_short_source_prohibitions_do_not_cross_into_positive_instructions(self):
        for text, expected in [
            ('不联网、不查数据库、不查知识库', {'web_search', 'db_query', 'knowledge'}),
            ('不查询数据库，必须检索知识库', {'db_query'}),
            ('不访问知识库，使用数据库', {'knowledge'}),
            ('不要查数据库，查知识库', {'db_query'}),
            ('不要联网；请查询数据库和知识库', {'web_search'}),
            ('禁止使用公开网络搜索，请查询数据库', {'web_search'}),
            ('不知道联网结果是否准确，请联网核实', set()),
            ('只查业务库，不生成文件', {'web_search', 'knowledge'}),
            ('仅依据知识库里的阅读卡，不联网', {'web_search', 'db_query'}),
            ('仅使用上传文件和数据库，不联网', {'web_search', 'knowledge'}),
            ('只查附件和知识库，不查数据库', {'web_search', 'db_query'}),
        ]:
            self.assertEqual(main_agent._explicitly_forbidden_sources(text), expected, text)

    def test_plan_normalization_preserves_visible_intent_summary(self):
        plan = main_agent.TaskPlan(task_type='qa', require_report=False, require_pdf=False,
                                   intent_summary='  根据句内库存和预留量计算可用数量，不查询外部来源。  ')
        normalized = main_agent._normalize_plan(plan, '库存12盒，预留2盒，不联网、不查数据库、不查知识库')
        self.assertEqual(normalized.intent_summary, plan.intent_summary.strip())
        self.assertEqual(set(normalized.forbidden_sources), {'web_search', 'db_query', 'knowledge'})

    def test_named_uploaded_disclosure_closes_unrequested_sources(self):
        uploads = ['AI-2026-001_技术交底_模拟.md']
        plan = main_agent.TaskPlan(task_type='qa', require_report=False, require_pdf=False, required_sources=['db_query', 'knowledge'])
        normalized = main_agent._normalize_plan(plan, '只查AI-2026-001的当前交底，准确率提升多少？不联网。', uploads)
        self.assertEqual(normalized.required_sources, [])
        self.assertEqual(set(normalized.forbidden_sources), {'db_query', 'knowledge', 'web_search'})
        for query, expected in [('只查AI-2026-001当前交底和案件信息，不联网。', {'db_query'}), ('仅依据AI-2026-001当前交底和知识库，不联网。', {'knowledge'})]:
            normalized = main_agent._normalize_plan(plan, query, uploads)
            self.assertEqual(set(normalized.required_sources), expected)
            self.assertEqual(set(normalized.forbidden_sources), {'db_query', 'knowledge', 'web_search'} - expected)

    def test_importing_api_does_not_mark_live_tasks_interrupted(self):
        server.storage.create_run('still-running', 'hello', 'hello', False)
        env = {**os.environ, 'APP_DB_PATH': str(self.root / 'app.db')}
        result = subprocess.run([sys.executable, '-c',
                                 'from api import server,storage; assert storage.get_run("still-running")["status"]=="running"'],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        with patch.object(server.storage, 'mark_interrupted') as recover:
            asyncio.run(server.recover_interrupted_tasks())
        recover.assert_called_once_with()

    def test_task_response_exposes_persisted_execution_start_time(self):
        with patch.object(server, '_run_task', AsyncMock(return_value=None)):
            response = self.client.post('/api/task', json={'thread_id': 'audit-start-time', 'query': 'hello'})
        self.assertEqual(response.status_code, 200)
        run = server.storage.get_run('audit-start-time')
        self.assertEqual(response.json()['started_at'], run['created_at'])

    def test_intent_revision_preserves_original_and_applies_only_on_rerun(self):
        storage = server.storage
        tid = 'intent-persistence'
        storage.create_run(tid, '只查知识库，不联网', '只查知识库，不联网\n【任务类型：快速问答】不要生成文件', False)
        storage.append_event(tid, {'event': 'plan_created', 'data': {'intent_summary': '查内部制度', 'required_sources': ['knowledge']}})
        response = self.client.post(f'/api/runs/{tid}/intent-revisions', json={
            'revised_query': '只联网查询公开政策，不查知识库', 'expected_revision_id': None})
        self.assertEqual(response.status_code, 200)
        saved = response.json()
        revision = saved['intent_revisions'][0]
        self.assertEqual(saved['saved_revision_id'], revision['id'])
        self.assertIsNone(saved['applied_intent_id'])  # Saving during execution must not mutate it.
        self.assertEqual(saved['plan']['intent_summary'], '查内部制度')
        self.assertEqual(revision['plan'], saved['plan'])
        prepared = storage.create_run(tid, '客户端不能覆盖原始问题', 'stale', False, expected_revision_id=revision['id'])
        self.assertEqual(prepared['execution_query'], '只联网查询公开政策，不查知识库\n【任务类型：快速问答】不要生成文件')
        restored = storage.get_run(tid)
        self.assertEqual(restored['query'], '只查知识库，不联网')
        self.assertEqual(restored['execution_query'], prepared['execution_query'])
        self.assertEqual(restored['applied_intent_id'], revision['id'])
        self.assertEqual(restored['intent_revisions'], saved['intent_revisions'])
        self.assertEqual(restored['events'], [])
        # Source constraints derive from the amended task, not the superseded original.
        banned = main_agent._explicitly_forbidden_sources(prepared['execution_query'])
        self.assertNotIn('web_search', banned)
        self.assertIn('knowledge', banned)
        storage.delete_run(tid)
        storage.create_run(tid, 'new task', 'new task', False)
        self.assertEqual(storage.get_run(tid)['intent_revisions'], [])

    def test_intent_revision_conflicts_do_not_destroy_current_run(self):
        storage = server.storage
        tid = 'intent-conflict'
        storage.create_run(tid, 'original', 'original', False)
        first = storage.save_intent_revision(tid, 'revision one', None)
        response = self.client.post(f'/api/runs/{tid}/intent-revisions', json={'revised_query': 'stale edit'})
        self.assertEqual(response.status_code, 409)
        second = storage.save_intent_revision(tid, 'revision two', first['id'])
        storage.append_event(tid, {'event': 'task_result', 'data': {'result': 'keep this result'}})
        with self.assertRaises(ValueError):
            storage.create_run(tid, 'original', 'original', False, expected_revision_id=first['id'])
        current = storage.get_run(tid)
        self.assertEqual(current['result'], 'keep this result')
        self.assertEqual(len(current['intent_revisions']), 2)
        self.assertEqual(current['intent_revisions'][0]['id'], second['id'])
        response = self.client.post('/api/task', json={'thread_id': tid, 'query': 'original', 'expected_revision_id': first['id']})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(storage.get_run(tid)['result'], 'keep this result')

    def test_intent_revision_input_validation(self):
        storage = server.storage
        tid = 'intent-validation'
        storage.create_run(tid, 'original', 'original', False)
        for text, expected in [('', 422), ('   ', 400), ('x' * 10001, 422)]:
            response = self.client.post(f'/api/runs/{tid}/intent-revisions', json={'revised_query': text})
            self.assertEqual(response.status_code, expected)
        response = self.client.post('/api/runs/intent-missing/intent-revisions', json={'revised_query': 'valid'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(storage.get_run(tid)['intent_revisions'], [])
        for invalid in (True, 1.5, '1'):
            response = self.client.post(f'/api/runs/{tid}/intent-revisions', json={'revised_query': 'valid', 'expected_revision_id': invalid})
            self.assertEqual(response.status_code, 422)
            response = self.client.post('/api/task', json={'query': 'valid', 'expected_revision_id': invalid})
            self.assertEqual(response.status_code, 422)

    def test_thread_traversal(self):
        for value in ['../outside', 'a/../../outside', 'a\\b', '', 'a' * 129]:
            with self.assertRaises(ValueError):
                path_utils.get_session_dir(value)
        response = self.client.post('/api/task', json={'query': 'hello', 'thread_id': 'x/../../oops'})
        self.assertEqual(response.status_code, 400)

    def test_upload_rejects_paths(self):
        for name in ['../secret.txt', '/tmp/secret.txt', 'a\\b.txt']:
            res = self.client.post('/api/upload', data={'thread_id': 'safe'}, files={'files': (name, b'data')})
            self.assertEqual(res.status_code, 400, name)

    def test_upload_and_prepare(self):
        response = self.client.post('/api/upload', data={'thread_id': 'safe'}, files={'files': ('sales.txt', b'42')})
        self.assertEqual(response.status_code, 200)
        directory = path_utils.get_session_dir('safe')
        main_agent._prepare_session('safe', directory)
        self.assertEqual((directory / 'attachments' / 'sales.txt').read_text(), '42')

    def test_attachment_lifecycle_and_other_session_preserved(self):
        self.client.post('/api/upload', data={'thread_id': 'safe'},
                         files={'files': ('sales.txt', b'42')})
        directory = path_utils.get_session_dir('safe')
        main_agent._prepare_session('safe', directory)
        (directory / 'report.md').write_text('historical answer')
        other = path_utils.get_upload_dir('other')
        other.mkdir()
        (other / 'sales.txt').write_text('other data')
        res = self.client.get('/api/attachments', params={'thread_id': 'safe'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['files'][0]['name'], 'sales.txt')
        self.assertEqual(res.json()['files'][0]['size'], 2)
        params = {'thread_id': 'safe', 'filename': 'sales.txt'}
        self.assertEqual(self.client.delete('/api/attachments', params=params).status_code, 200)
        self.assertFalse((path_utils.get_upload_dir('safe') / 'sales.txt').exists())
        self.assertFalse((directory / 'sales.txt').exists())
        self.assertTrue((directory / 'report.md').exists())
        self.assertEqual((other / 'sales.txt').read_text(), 'other data')
        self.assertEqual(self.client.get('/api/attachments', params={'thread_id': 'safe'}).json()['files'], [])
        self.assertEqual(self.client.delete('/api/attachments', params=params).status_code, 404)

    def test_attachment_delete_rejects_paths_links_and_running_tasks(self):
        upload = path_utils.get_upload_dir('safe')
        upload.mkdir()
        (upload / 'data.txt').write_text('data')
        for name in ['../data.txt', '/tmp/data.txt', 'a\\b', '.', '']:
            res = self.client.delete('/api/attachments', params={'thread_id': 'safe', 'filename': name})
            self.assertEqual(res.status_code, 400)
        server._starting.add('safe')
        try:
            self.assertEqual(self.client.delete('/api/attachments', params={
                'thread_id': 'safe', 'filename': 'data.txt'}).status_code, 409)
            self.assertEqual(self.client.delete('/api/runs/safe').status_code, 409)
        finally:
            server._starting.discard('safe')
        other = path_utils.get_session_dir('other')
        other.mkdir()
        protected = other / 'data.txt'
        protected.write_text('keep')
        session = path_utils.get_session_dir('safe')
        session.mkdir()
        (session / 'data.txt').symlink_to(protected)
        self.assertEqual(self.client.delete('/api/attachments', params={
            'thread_id': 'safe', 'filename': 'data.txt'}).status_code, 403)
        self.assertEqual(protected.read_text(), 'keep')
        self.assertTrue((upload / 'data.txt').exists())
        (upload / 'linked.txt').symlink_to(protected)
        names = [f['name'] for f in self.client.get('/api/attachments', params={'thread_id': 'safe'}).json()['files']]
        self.assertEqual(names, ['data.txt'])

    def test_upload_in_progress_blocks_task_and_deletion(self):
        server._uploading.add('safe')
        try:
            self.assertEqual(self.client.delete('/api/runs/safe').status_code, 409)
            self.assertEqual(self.client.delete('/api/attachments', params={
                'thread_id': 'safe', 'filename': 'data.txt'}).status_code, 409)
            self.assertEqual(self.client.post('/api/task', json={
                'thread_id': 'safe', 'query': 'hello'}).status_code, 409)
            self.assertEqual(self.client.post('/api/upload', data={'thread_id': 'safe'},
                files={'files': ('data.txt', b'hello')}).status_code, 409)
        finally:
            server._uploading.discard('safe')

    def test_recycle_preserves_files_then_permanent_delete_cleans_records(self):
        server.storage.create_run('cleanup', 'hello', 'hello', False)
        for directory in (path_utils.get_upload_dir('cleanup'), path_utils.get_session_dir('cleanup')):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'file.txt').write_text('private')
        other = path_utils.get_session_dir('keep')
        other.mkdir()
        (other / 'report.md').write_text('keep')
        # Nested links must not cause rmtree to follow into another session.
        (path_utils.get_session_dir('cleanup') / 'link').symlink_to(other, target_is_directory=True)
        server.storage.append_event('cleanup', {'event':'task_result','data':{'result':'done'}})
        res = self.client.delete('/api/runs/cleanup')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['status'], 'trashed')
        self.assertFalse(res.json()['files_cleaned'])
        self.assertTrue(path_utils.get_upload_dir('cleanup').exists())
        self.assertIsNone(server.storage.get_run('cleanup'))
        res = self.client.delete('/api/trash/cleanup')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()['files_cleaned'])
        self.assertFalse(path_utils.get_upload_dir('cleanup').exists())
        self.assertFalse(path_utils.get_session_dir('cleanup').exists())
        self.assertIsNone(server.storage.get_run('cleanup'))
        self.assertEqual((other / 'report.md').read_text(), 'keep')
        self.assertEqual(self.client.delete('/api/trash/cleanup').status_code, 404)

    def test_delete_session_rejects_linked_directory_and_reports_cleanup_failure(self):
        other = path_utils.get_upload_dir('other')
        other.mkdir()
        (other / 'keep.txt').write_text('keep')
        (self.upload / 'session_linked').symlink_to(other, target_is_directory=True)
        self.assertEqual(self.client.delete('/api/runs/linked').status_code, 403)
        self.assertTrue((other / 'keep.txt').exists())
        server.storage.create_run('failure', 'hello', 'hello', False)
        path_utils.get_upload_dir('failure').mkdir()
        server.storage.append_event('failure', {'event':'task_result','data':{'result':'done'}})
        self.assertEqual(self.client.delete('/api/runs/failure').status_code, 200)
        with patch('api.recycle_bin.shutil.rmtree', side_effect=OSError('disk error')):
            self.assertEqual(self.client.delete('/api/trash/failure').status_code, 500)
        self.assertIsNotNone(server.recycle_bin.entry('failure'))

    def test_files_isolated_by_thread(self):
        d = path_utils.get_session_dir('a')
        d.mkdir()
        f = d / 'report.md'
        f.write_text('test')
        for endpoint, path in [('/api/files', d), ('/api/download', f)]:
            self.assertEqual(self.client.get(endpoint, params={'path': str(path), 'thread_id': 'a'}).status_code, 200)
            self.assertEqual(self.client.get(endpoint, params={'path': str(path), 'thread_id': 'b'}).status_code, 403)

    def test_symlink_escape(self):
        d = path_utils.get_session_dir('a')
        d.mkdir()
        (d / 'escape').symlink_to(self.root, target_is_directory=True)
        self.assertIsNone(path_utils.validate_path(str(d / 'escape'), d))
        with self.assertRaises(ValueError):
            path_utils.resolve_within(d, 'escape/file')

    def test_absolute_tool_path(self):
        d = path_utils.get_session_dir('a')
        self.assertEqual(path_utils.resolve_within(d, str(d / 'report.md')), d / 'report.md')

    def test_empty_task(self):
        for query in ['', '   ']:
            self.assertIn(self.client.post('/api/task', json={'query': query}).status_code, (400, 422))

    def test_ws_then_submit_and_cancel(self):
        entered = asyncio.Event()
        async def slow(query, thread_id):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(server, 'run_deep_agent', side_effect=slow):
            with TestClient(server.app) as client, client.websocket_connect('/ws/tasktest') as ws:
                response = client.post('/api/task', json={'query': 'test', 'thread_id': 'tasktest'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(ws.receive_json()['event'], 'session_created')
                self.assertEqual(client.post('/api/task', json={'query': 'again', 'thread_id': 'tasktest'}).status_code, 409)
                self.assertEqual(client.post('/api/upload', data={'thread_id': 'tasktest'}, files={'files': ('a.txt', b'a')}).status_code, 409)
                self.assertEqual(client.post('/api/task/tasktest/cancel').status_code, 200)
                self.assertEqual(ws.receive_json()['event'], 'task_cancelled')
                ws.send_text('ping')
                self.assertEqual(ws.receive_json()['type'], 'pong')
                self.assertNotIn('tasktest', server._tasks)

    def test_custom_budget_flows_through_api_and_quality_history(self):
        async def fake_agent(query, thread_id, max_rounds=3):
            await server.monitor.report(thread_id, 'quality_summary', '质量处理结束', {
                'stop_reason': 'passed', 'rounds_used': 2, 'max_rounds': max_rounds,
                'passed': True, 'issues': [],
            })
            return '已验证的回答'
        with patch.object(server, 'run_deep_agent', side_effect=fake_agent) as runner:
            with TestClient(server.app) as client, client.websocket_connect('/ws/api-budget') as ws:
                response = client.post('/api/task', json={'query':'test', 'thread_id':'api-budget', 'max_rounds':5})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(ws.receive_json()['event'], 'session_created')
                self.assertEqual(ws.receive_json()['data']['max_rounds'], 5)
                self.assertEqual(ws.receive_json()['event'], 'task_result')
                history = client.get('/api/runs/api-budget').json()
                self.assertEqual(history['max_rounds'], 5)
                summary = next(e['data'] for e in history['events'] if e['event'] == 'quality_summary')
                self.assertEqual(summary['rounds_used'], 2)
                self.assertEqual(history['status'], 'completed')
                self.assertEqual(runner.call_args.args, ('test', 'api-budget', 5))
                self.assertEqual(client.post('/api/task', json={'query':'test','max_rounds':7}).status_code, 422)

    def test_source_guards_stop_io_before_any_access(self):
        import tools.internet_search_tool as web
        import tools.knowledge_base_tool as kb
        import tools.mysql_tools as db
        from tools.semantic_query import semantic_query
        context.set_session('restricted', str(self.output))
        context.set_round(1)
        source_policy.configure([], ['web_search'])
        with patch.object(web._client, 'search') as network, patch.object(db, '_get_connection') as connect, \
                patch.object(kb, '_retrieve_sync') as retrieve:
            self.assertIn('禁止执行', web.internet_search.invoke({'query':'test'}))
            self.assertIn('禁止执行', db.list_sql_tables())
            self.assertIn('禁止执行', db.get_table_data('drugs'))
            self.assertIn('禁止执行', db.execute_sql_query('SELECT 1'))
            self.assertIn('禁止执行', semantic_query.invoke({'metric':'drug_count'}))
            self.assertIn('禁止执行', asyncio.run(kb.search_knowledge_base.ainvoke({'query':'test'})))
        network.assert_not_called()
        connect.assert_not_called()
        retrieve.assert_not_called()
        self.assertTrue(source_policy.gaps_for_round())

    def test_source_policy_without_plan_is_closed_and_cleanup_is_complete(self):
        context.set_session('no-policy', str(self.output))
        context.set_round(1)
        self.assertIn('禁止执行', source_policy.source_denial('web_search'))
        context.reset_session()
        self.assertIsNone(source_policy.current_policy())
        self.assertIsNone(source_policy.source_denial('web_search'))
        context.set_session('no-policy', str(self.output))
        context.set_round(1)
        self.assertEqual(source_policy.gaps_for_round(), [])

    def test_agent_assembly_only_exposes_active_sources(self):
        context.set_session('assembly', str(self.output))
        for active in [[], ['knowledge'], ['db_query', 'knowledge']]:
            source_policy.configure(active, ['web_search'])
            with patch('deepagents.create_deep_agent') as create, patch.object(main_agent, 'get_llm', return_value=object()):
                main_agent.create_deep_agent()
            names = {a['name'] for a in create.call_args.kwargs['subagents']}
            self.assertEqual(names, {main_agent._SOURCE_LABELS[k] for k in active})
            self.assertNotIn(main_agent._SOURCE_LABELS['web_search'], names)
        source_policy.configure(['knowledge', 'web_search'], ['web_search'])
        self.assertEqual(source_policy.current_policy().active, frozenset({'knowledge'}))

    def test_source_policies_are_isolated_across_tasks_and_worker_threads(self):
        async def child(thread_id, source):
            context.set_session(thread_id, str(self.output))
            context.set_round(1)
            source_policy.configure([source])
            await asyncio.sleep(0)
            policy = await asyncio.to_thread(source_policy.current_policy)
            self.assertEqual(policy.active, frozenset({source}))
            other = 'web_search' if source != 'web_search' else 'knowledge'
            self.assertIsNotNone(await asyncio.to_thread(source_policy.source_denial, other))
            self.assertEqual(len(source_policy.gaps_for_round()), 1)
            context.reset_session()
        async def run():
            await asyncio.gather(child('route-a','knowledge'), child('route-b','web_search'))
        asyncio.run(run())
        self.assertIsNone(source_policy.current_policy())

    def test_gap_tool_records_request_but_does_not_grant_access(self):
        from tools.source_gap_tool import report_source_gap
        context.set_session('gap-request', str(self.output))
        context.set_round(1)
        source_policy.configure(['knowledge'])
        with patch('api.evidence.snapshot', return_value=[{'id':'S1'}]):
            bad = report_source_gap.invoke({'missing':'需要实际金额','requested_source':'db_query','evidence_ids':['S999']})
            self.assertIn('不存在', bad)
            self.assertEqual(source_policy.gaps_for_round(), [])
            text = report_source_gap.invoke({'missing':'需要实际金额','requested_source':'db_query','evidence_ids':['S1']})
        self.assertIn('pending_plan_review', text)
        self.assertEqual(source_policy.current_policy().active, frozenset({'knowledge'}))
        context.set_round(2)
        self.assertEqual(source_policy.gaps_for_round(), [])
        self.assertEqual(source_policy.task_gaps()[0]['missing'], '需要实际金额')

    def test_gap_routing_validates_sources_ids_and_budget(self):
        from agent import plan_routing
        gap = {'id':'G1','missing':'订单实际金额','requested_source':'db_query'}
        async def run():
            fake = Mock()
            fake.ainvoke = AsyncMock()
            fake.with_structured_output.return_value = fake
            proposals = [
                {'add_sources':['db_query'],'gap_ids':['G1'],'reason':'原问题需要订单金额'},
                {'add_sources':['web_search'],'gap_ids':['G1'],'reason':'尝试绕过限制'},
                {'add_sources':['db_query'],'gap_ids':['G999'],'reason':'无效缺口'},
                {'add_sources':['db_query'],'gap_ids':[],'reason':'没有依据'},
            ]
            with patch.object(plan_routing, 'get_llm', return_value=fake):
                for index, proposal in enumerate(proposals):
                    fake.ainvoke.return_value = proposal
                    decision = await plan_routing.evaluate_gap_routing('查询订单金额', {'required_sources':['knowledge']}, [gap], {'web_search'}, 2)
                    self.assertEqual(decision['status'], 'approved' if index == 0 else 'denied')
                fake.reset_mock()
                decision = await plan_routing.evaluate_gap_routing('q', {'required_sources':['knowledge']}, [gap], set(), 0)
                self.assertEqual(decision['status'], 'budget_exhausted')
                fake.ainvoke.assert_not_called()
        asyncio.run(run())

    def test_gap_routing_does_not_call_model_for_forbidden_request(self):
        from agent import plan_routing
        async def run():
            with patch.object(plan_routing, 'get_llm') as model:
                decision = await plan_routing.evaluate_gap_routing('不联网', {'required_sources':['knowledge']},
                    [{'id':'G1','missing':'缺资料','requested_source':'web_search'}], {'web_search'}, 2)
            self.assertEqual(decision['status'], 'denied')
            model.assert_not_called()
        asyncio.run(run())

    def test_gap_routing_model_failure_keeps_existing_plan(self):
        from agent import plan_routing
        async def run():
            fake = Mock()
            fake.ainvoke = AsyncMock()
            fake.with_structured_output.return_value = fake
            fake.ainvoke.side_effect = RuntimeError('unavailable')
            with patch.object(plan_routing, 'get_llm', return_value=fake):
                decision = await plan_routing.evaluate_gap_routing('q', {'required_sources':['knowledge']},
                    [{'id':'G1','missing':'金额未知','requested_source':'db_query'}], set(), 2)
            self.assertEqual(decision['status'], 'unavailable')
            self.assertEqual(decision['add_sources'], [])
        asyncio.run(run())

    def test_gap_driven_plan_update_opens_source_only_on_next_round(self):
        self.default_plan.required_sources = ['knowledge']
        seen = []
        async def execute(query, round_num, required_sources, **kwargs):
            seen.append(set(source_policy.current_policy().active))
            if round_num == 1:
                source_policy.record_gap('knowledge', '需要订单金额', requested_source='db_query')
            return {'report':'回答', 'called_sources':['knowledge'] if round_num == 1 else ['db_query'],
                    'required_sources':required_sources, 'pdf_produced':False}
        async def run():
            from api import storage
            storage.create_run('updated-plan','订单金额结合制度','订单金额结合制度',False)
            decision = {'status':'approved','add_sources':['db_query'],'gap_ids':['G1'],'reason':'判断审批需要实际金额'}
            with patch.object(main_agent, 'main_agent_run', side_effect=execute), \
                    patch.object(main_agent, 'evaluate_gap_routing', AsyncMock(return_value=decision)), \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value=None)):
                text = await main_agent.run_deep_agent('结合订单金额判断审批', 'updated-plan')
            self.assertEqual(text, '回答')
            self.assertEqual(seen, [{'knowledge'}, {'knowledge','db_query'}])
            run = storage.get_run('updated-plan')
            self.assertEqual(set(run['plan']['required_sources']), {'knowledge','db_query'})
            self.assertEqual(sum(e['event'] == 'plan_updated' for e in run['events']), 1)
        asyncio.run(run())

    def test_forbidden_source_cannot_be_granted_by_adjustment(self):
        self.default_plan.required_sources = ['knowledge']
        async def execute(query, round_num, required_sources, **kwargs):
            source_policy.record_gap('knowledge', '缺少内部资料', requested_source='web_search')
            self.assertNotIn('web_search', source_policy.current_policy().active)
            return {'report':'现有材料不足，无法核实', 'called_sources':['knowledge'],
                    'required_sources':required_sources, 'pdf_produced':False}
        async def run():
            decision = {'status':'approved','add_sources':['web_search'],'gap_ids':['G1'],'reason':'错误建议'}
            with patch.object(main_agent, 'main_agent_run', side_effect=execute), \
                    patch.object(main_agent, 'evaluate_gap_routing', AsyncMock(return_value=decision)), \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value=None)) as review:
                text = await main_agent.run_deep_agent('只查知识库，不联网', 'forbidden-update')
            self.assertEqual(text, '现有材料不足，无法核实')
            self.assertEqual(review.call_args.args[0]['evidence_gaps'][-1]['routing_decision']['status'], 'denied')
        asyncio.run(run())

    def test_plan_reassessment_has_two_attempt_limit(self):
        self.default_plan.required_sources = ['knowledge']
        async def execute(query, round_num, required_sources, **kwargs):
            source_policy.record_gap('knowledge', f'缺失事实{round_num}', requested_source='db_query')
            return {'report':f'回答{round_num}', 'called_sources':['knowledge'],
                    'required_sources':required_sources, 'pdf_produced':False}
        async def run():
            decision = {'status':'unchanged','add_sources':[],'gap_ids':['G1'],'reason':'保留计划'}
            with patch.object(main_agent, 'main_agent_run', side_effect=execute), \
                    patch.object(main_agent, 'evaluate_gap_routing', AsyncMock(return_value=decision)) as replan, \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value='未完整回答')):
                await main_agent.run_deep_agent('q', 'bounded-replan', 5)
            self.assertEqual(replan.await_count, 2)
        asyncio.run(run())

    def test_unplanned_actual_sources_are_hard_failures(self):
        issues = reflect_check.hard_check({'report':'回答','require_report':False,
            'called_sources':['knowledge','web_search'],'required_sources':['knowledge'],
            'allowed_sources':['knowledge']})
        self.assertTrue(any('超出任务计划' in issue for issue in issues))

    def test_semantic_tools_count_as_database_source(self):
        async def run():
            callback = MonitorCallbackHandler('semantic-source')
            await callback.on_tool_start({'name':'semantic_query'}, '', run_id='success')
            await callback.on_tool_end('[证据 S1] 统计结果', run_id='success')
            self.assertEqual(callback.called_sources, {'db_query'})
            failed = MonitorCallbackHandler('semantic-source')
            await failed.on_tool_start({'name':'semantic_drilldown'}, '', run_id='failed')
            await failed.on_tool_end('语义下钻失败：禁止执行', run_id='failed')
            self.assertEqual(failed.called_sources, set())
        asyncio.run(run())

    def test_attachment_only_and_semantic_prohibitions_are_retained(self):
        plan = self.default_plan.model_copy(update={'required_sources':['db_query','knowledge','web_search']})
        normalized = main_agent._normalize_plan(plan, '仅基于附件回答')
        self.assertEqual(normalized.required_sources, [])
        self.assertEqual(set(normalized.forbidden_sources), source_policy.SOURCES)
        plan.forbidden_sources = ['web_search']
        normalized = main_agent._normalize_plan(plan, '分析这份资料')
        self.assertNotIn('web_search', normalized.required_sources)
        self.assertIn('web_search', normalized.forbidden_sources)

    def test_new_session_does_not_inherit_old_source_permissions(self):
        context.set_session('old-policy', str(self.output))
        source_policy.configure(['web_search'])
        context.set_session('new-policy', str(self.output))
        self.assertIsNone(source_policy.current_policy())
        self.assertIn('禁止执行', source_policy.source_denial('web_search'))

    def test_database_connection_boundary_denies_before_connect(self):
        import tools.mysql_tools as db
        context.set_session('connection-boundary', str(self.output))
        source_policy.configure(['knowledge'])
        with patch.object(db.mysql.connector, 'connect') as connect:
            with self.assertRaises(PermissionError):
                db.run_read_query('SELECT 1')
        connect.assert_not_called()

    def test_no_remaining_rounds_never_opens_new_source(self):
        server.storage.create_run('last-round','q','q',False,1)
        self.default_plan.required_sources = ['knowledge']
        async def execute(query, round_num, required_sources, **kwargs):
            source_policy.record_gap('knowledge', '缺少实际金额', requested_source='db_query')
            return {'report':'金额尚未核实', 'called_sources':['knowledge'], 'pdf_produced':False}
        async def run():
            decision = {'status':'approved','add_sources':['db_query'],'gap_ids':['G1'],'reason':'错误地在末轮加来源'}
            with patch.object(main_agent, 'main_agent_run', side_effect=execute) as runner, \
                    patch.object(main_agent, 'evaluate_gap_routing', AsyncMock(return_value=decision)), \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value='无法完整回答')):
                text = await main_agent.run_deep_agent('q', 'last-round', 1)
            self.assertEqual(runner.await_count, 1)
            from tools.result_assessment import report_hash
            self.assertEqual(server.storage.load_context_artifact('last-round',report_hash('金额尚未核实')),'金额尚未核实')
            self.assertIn('尚未核对通过',text)
            record=server.storage.get_run('last-round')
            gaps=[g for e in record['events'] if e['event']=='reflection' for g in e['data'].get('evidence_gaps',[])]
            self.assertTrue(any(g['missing']=='缺少实际金额' for g in gaps))
            self.assertIn('【未达标说明】', text)
        asyncio.run(run())

    def test_budget_exhaustion_does_not_count_as_web_evidence(self):
        async def run():
            callback = MonitorCallbackHandler('web-failed')
            await callback.on_tool_start({'name':'internet_search'}, '', run_id='limited')
            await callback.on_tool_end('本轮网络检索已达到上限（3 次）', run_id='limited')
            self.assertEqual(callback.called_sources, set())
        asyncio.run(run())

    def test_real_graph_replans_before_database_agent_becomes_available(self):
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        import tools.semantic_query as semantic_tools
        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self
        model = ToolModel(responses=[
            AIMessage(content='', tool_calls=[{'id':'gap1','name':'report_source_gap','args':{
                'missing':'原问题需要订单实际金额','requested_source':'db_query'}}]),
            AIMessage(content='金额尚未查询，无法核实'),
            AIMessage(content='', tool_calls=[{'id':'delegate','name':'task','args':{
                'subagent_type': main_agent._SOURCE_LABELS['db_query'], 'description':'查询订单金额'}}]),
            AIMessage(content='', tool_calls=[{'id':'sql','name':'semantic_query','args':{'metric':'total_sales'}}]),
            AIMessage(content='查询金额是100'),
            AIMessage(content='订单实际金额为100'),
        ])
        observed = []
        def query(*args, **kwargs):
            observed.append((context.get_round(), source_policy.current_policy().active))
            return '金额\n100'
        async def run():
            from api import storage
            storage.create_run('graph-routing', '查询订单实际金额', '查询订单实际金额', False)
            decision = {'status':'approved','add_sources':['db_query'],'gap_ids':['G1'],'reason':'原问题需要数据库金额'}
            with patch.object(main_agent, 'get_llm', return_value=model), \
                    patch.object(main_agent, 'evaluate_gap_routing', AsyncMock(return_value=decision)), \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value=None)), \
                    patch.object(semantic_tools, 'run_semantic_query', side_effect=query):
                report = await main_agent.run_deep_agent('查询订单实际金额', 'graph-routing')
            self.assertEqual(report, '订单实际金额为100')
            self.assertEqual(observed, [(2, frozenset({'db_query'}))])
            history = storage.get_run('graph-routing')['events']
            actors = [e['data']['agent_name'] for e in history if e['event'] == 'agent_activity' and e['data']['phase'] == 'start']
            self.assertIn(main_agent._SOURCE_LABELS['db_query'], actors)
            child_index = actors.index(main_agent._SOURCE_LABELS['db_query'])
            self.assertEqual(actors[0], '主智能体')
            self.assertIn('主智能体', actors[child_index + 1:])
            tool = next(e for e in history if e['event'] == 'tool_start' and e['data']['tool_name'] == 'semantic_query')
            self.assertEqual(tool['data']['agent_name'], main_agent._SOURCE_LABELS['db_query'])
            self.assertIsNone(source_policy.current_policy())
        asyncio.run(run())

    def test_tool_failures_feed_gap_loop_without_elevating_access(self):
        import tools.internet_search_tool as web
        import tools.mysql_tools as db
        context.set_session('tool-gaps', str(self.output))
        context.set_round(1)
        source_policy.configure(['db_query','web_search'])
        with patch.object(web._client, 'search', return_value={'results':[]}):
            self.assertIn('未检索到', web.internet_search.invoke({'query':'test'}))
        with patch.object(db, '_get_connection', side_effect=RuntimeError('offline')):
            self.assertIn('连接数据库失败', db.list_sql_tables())
        gaps = source_policy.gaps_for_round()
        self.assertEqual({g['source'] for g in gaps}, {'db_query','web_search'})
        self.assertEqual({g['reason'] for g in gaps}, {'tool_error','no_hits'})
        self.assertEqual(source_policy.current_policy().active, frozenset({'db_query','web_search'}))

    def test_existing_source_request_does_not_trigger_another_planner_call(self):
        from agent import plan_routing
        async def run():
            with patch.object(plan_routing, 'get_llm') as model:
                decision = await plan_routing.evaluate_gap_routing('内部规则', {'required_sources':['knowledge']},
                    [{'id':'G1','missing':'再找内部条款','requested_source':'knowledge'}], set(), 2)
            self.assertEqual(decision['status'], 'unchanged')
            model.assert_not_called()
        asyncio.run(run())

    def test_monitor_resolves_agent_ownership_through_runtime_parent_chain(self):
        async def run():
            callback = MonitorCallbackHandler('trace-owner')
            with patch('api.monitor.monitor.report', new_callable=AsyncMock) as report:
                await callback.on_chain_start(None, {}, run_id='root')
                await callback.on_chat_model_start({}, [['private input']], run_id='main-model', parent_run_id='root')
                await callback.on_llm_end(None, run_id='main-model')
                await callback.on_tool_start({'name':'task'}, '', run_id='delegate', parent_run_id='root',
                    inputs={'subagent_type':'内部知识库助手','description':'完整问题'})
                await callback.on_chain_start(None, {}, run_id='subgraph', parent_run_id='delegate')
                await callback.on_chain_start(None, {}, run_id='node', parent_run_id='subgraph')
                await callback.on_chat_model_start({}, [['private input']], run_id='child-model', parent_run_id='node')
                await callback.on_llm_end(None, run_id='child-model')
                await callback.on_tool_start({'name':'search_knowledge_base'}, '', run_id='search', parent_run_id='node')
                await callback.on_tool_end('原文', run_id='search')
                await callback.on_tool_end('子智能体结果', run_id='delegate')
                await callback.on_chat_model_start({}, [], run_id='resume', parent_run_id='root')
                await callback.on_llm_end(None, run_id='resume')
            calls = [call.args for call in report.await_args_list]
            actors = [args[3]['agent_name'] for args in calls if args[1] == 'agent_activity' and args[3]['phase'] == 'start']
            self.assertEqual(actors, ['主智能体','内部知识库助手','主智能体'])
            search = next(args[3] for args in calls if args[1]=='tool_start')
            self.assertEqual(search['agent_run_id'], 'delegate')
            self.assertEqual(search['parent_run_id'], 'node')
            self.assertNotIn('private input', str(calls))
        asyncio.run(run())

    def test_monitor_does_not_mix_parallel_same_name_delegations(self):
        async def run():
            callback = MonitorCallbackHandler('parallel-trace')
            with patch('api.monitor.monitor.report', new_callable=AsyncMock) as report:
                for delegate in ['a','b']:
                    await callback.on_tool_start({'name':'task'}, '', run_id=delegate,
                        inputs={'subagent_type':'内部知识库助手'})
                    await callback.on_chain_start(None, {}, run_id=delegate+'-chain', parent_run_id=delegate)
                await callback.on_tool_start({'name':'search_knowledge_base'}, '', run_id='tb', parent_run_id='b-chain')
                await callback.on_tool_start({'name':'search_knowledge_base'}, '', run_id='ta', parent_run_id='a-chain')
                await callback.on_tool_error(RuntimeError('failed'), run_id='tb')
            starts = [c.args[3] for c in report.await_args_list if c.args[1]=='tool_start']
            self.assertEqual([d['agent_run_id'] for d in starts], ['b','a'])
            error = next(c.args[3] for c in report.await_args_list if c.args[1]=='tool_error')
            self.assertEqual(error['agent_run_id'],'b')
        asyncio.run(run())

    def test_monitor_nested_delegation_returns_to_actual_caller(self):
        async def run():
            callback = MonitorCallbackHandler('nested-trace')
            with patch('api.monitor.monitor.report', new_callable=AsyncMock) as report:
                await callback.on_tool_start({'name':'task'}, '', run_id='outer', inputs={'subagent_type':'内部知识库助手'})
                await callback.on_chain_start(None, {}, run_id='outer-chain', parent_run_id='outer')
                await callback.on_tool_start({'name':'task'}, '', run_id='inner', parent_run_id='outer-chain', inputs={'subagent_type':'数据库查询助手'})
                await callback.on_tool_end('结果',run_id='inner')
            returned = report.await_args_list[-1].args[3]
            self.assertEqual(returned['agent_name'],'内部知识库助手')
            self.assertEqual(returned['assistant_name'],'数据库查询助手')
        asyncio.run(run())

    def test_monitor_supports_json_args_and_model_error_without_recording_messages(self):
        async def run():
            callback = MonitorCallbackHandler('trace-error')
            with patch('api.monitor.monitor.report', new_callable=AsyncMock) as report:
                await callback.on_tool_start({'name':'task'}, '{"subagent_type":"内部知识库助手","description":"查询"}', run_id='delegated')
                await callback.on_chat_model_start({}, [['hidden thoughts']], run_id='model', parent_run_id='delegated')
                await callback.on_llm_error(RuntimeError('timeout'), run_id='model')
            self.assertEqual(report.await_args_list[0].args[3]['assistant_name'], '内部知识库助手')
            self.assertEqual(report.await_args_list[-1].args[3]['phase'],'error')
            self.assertNotIn('hidden thoughts', str(report.await_args_list))
            self.assertEqual(callback._models, {})
        asyncio.run(run())

    def test_plain_answer_length(self):
        self.assertEqual(reflect_check.hard_check({'report': '答案', 'require_report': False}), [])
        self.assertEqual(reflect_check.hard_check({'report': '短报告', 'require_report': True}), [])

    def test_placeholder_pdf_and_source_checks(self):
        issues = reflect_check.hard_check({'report': '正文' * 600 + '等待任务', 'require_pdf': True,
                                           'required_sources': ['knowledge']})
        self.assertEqual(len(issues), 3)

    def test_sql_reject_before_connect(self):
        for sql in ['CALL dangerous()', 'SET GLOBAL max_connections=1', 'SELECT 1; SELECT 2',
                    "SELECT * INTO OUTFILE '/tmp/x' FROM drugs", 'SELECT SLEEP(60)', 'DELETE FROM drugs']:
            with patch('tools.mysql_tools._get_connection') as connect:
                result = execute_sql_query(sql)
                self.assertIn('禁止执行', result)
                connect.assert_not_called()

    def test_failed_source_is_not_coverage(self):
        async def run():
            callback = MonitorCallbackHandler('a')
            await callback.on_tool_start({'name': 'execute_sql_query'}, '', run_id='one')
            await callback.on_tool_end('连接数据库失败：unavailable', run_id='one')
            self.assertEqual(callback.called_sources, set())
            await callback.on_tool_start({'name': 'execute_sql_query'}, '', run_id='two')
            await callback.on_tool_end('total\n10', run_id='two')
            self.assertEqual(callback.called_sources, {'db_query'})
        asyncio.run(run())

    def test_report_uses_written_content(self):
        msg = AIMessage(content='', tool_calls=[{'name': 'generate_markdown', 'args': {'filename': 'new', 'content': '未落盘内容'}, 'id': '1'}])
        self.assertEqual(main_agent._extract_report(self.output, [msg, AIMessage(content='最终回答')]), '最终回答')
        (self.output / 'new.md').write_text('实际文件正文')
        self.assertEqual(main_agent._extract_report(self.output, [msg]), '实际文件正文')

    def test_soft_failure_visible_after_three_rounds(self):
        server.storage.create_run('review','生成报告','生成报告',False,3)
        async def run():
            result = {'report': '正文' * 600, 'require_pdf': False, 'pdf_produced': False,
                      'called_sources': [], 'required_sources': []}
            with patch.object(main_agent, 'main_agent_run', AsyncMock(return_value=result)) as run_mock, patch.object(main_agent, 'soft_review', AsyncMock(return_value='不通过：结论无依据')):
                text = await main_agent.run_deep_agent('生成报告', 'review')
                self.assertIn('尚未核对通过', text)
                summary=next(e['data'] for e in server.storage.get_run('review')['events'] if e['event']=='quality_summary')
                self.assertTrue(any('结论无依据' in issue for issue in summary['issues']))
                self.assertEqual(run_mock.await_count, 3)
                self.assertIsNone(context.get_thread_id())
        asyncio.run(run())

    def test_retry_receives_feedback_report_and_raw_evidence(self):
        self.default_plan.required_sources = ["knowledge"]
        async def run():
            first = {'report': '错误断言：没有资料', 'evidence': [{'id': 'S1', 'snippet': '风险评估原文', 'location': '第65页'}],
                     'called_sources': ['knowledge'], 'required_sources': ['knowledge'], 'pdf_produced': False}
            second = {**first, 'report': '已依据原文修正', 'called_sources': []}
            with patch.object(main_agent, 'main_agent_run', AsyncMock(side_effect=[first, second])) as runner, \
                    patch.object(main_agent, 'soft_review', AsyncMock(side_effect=['不通过：漏读风险评估', None])):
                text = await main_agent.run_deep_agent('只查知识库', 'correct', 5)
            prompt = runner.await_args_list[1].args[0]
            self.assertNotIn('风险评估原文',prompt)
            prompt += '\n'.join(m.content for m in runner.await_args_list[1].kwargs['revision_context'])
            for expected in ['漏读风险评估', '错误断言', '风险评估原文', '第65页', '系统任务计划']:
                self.assertIn(expected, prompt)
            self.assertEqual(text, '已依据原文修正')
            self.assertEqual(runner.await_count, 2)
        asyncio.run(run())

    def test_structured_review_repair_and_history_replay(self):
        from api import storage
        from tools.content_verification import CRITERIA, review_anchor_index
        query = '概括当前资料，不确定时说明'
        storage.create_run('structured-repair', query, query, False, 3)
        first = {'report': '资料结论已确定', 'evidence': [], 'called_sources': [], 'pdf_produced': False}
        second = {**first, 'report': '资料不足，当前无法核实结论。'}
        task_anchor = review_anchor_index({'query': query, 'report': first['report']})['task'][0]['id']
        checks = [{'criterion': key, 'status': 'passed', 'detail': '已按要求核对',
                   'task_anchor_id': task_anchor,
                   'task_quote': query if key == 'task_completion' else ''} for key in CRITERIA]
        failed_checks = [dict(item) for item in checks]
        failed_checks[-1].update(status='failed', detail='没有依据却声称确定', answer_quote='资料结论已确定',
                                 action='revise_answer', suggestion='明确当前资料不足，不能核实结论。')
        model = AsyncMock()
        checks[-1]['followup_ids'] = ['R1-V5']
        model.with_structured_output=Mock(return_value=model)
        model.ainvoke.side_effect = [{'parsed':{'verdict':'failed','checks':failed_checks},'parsing_error':None},
                                     {'parsed':{'verdict':'passed','checks':checks},'parsing_error':None}]
        async def run():
            with patch.object(main_agent, 'main_agent_run', AsyncMock(side_effect=[first,second])) as runner, \
                    patch.object(main_agent, 'get_llm', return_value=model):
                answer = await main_agent.run_deep_agent(query,'structured-repair',3)
            self.assertEqual(answer,second['report'])
            prompt=runner.await_args_list[1].args[0]
            self.assertIn('分项修正清单',prompt)
            self.assertIn('V5',prompt)
            self.assertIn('明确当前资料不足',prompt)
            self.assertEqual(runner.await_count,2)
        asyncio.run(run())
        rounds=[event['data'] for event in storage.get_run('structured-repair')['events'] if event['event']=='reflection']
        self.assertEqual([item['verification']['status'] for item in rounds],['failed','passed'])
        self.assertEqual(rounds[0]['verification']['checks'][-1]['answer_quote'],'资料结论已确定')
        self.assertEqual(rounds[1]['verification']['checks'][-1]['action'],'none')
        self.assertEqual(rounds[1]['issue_lifecycle'][0]['issue_id'], 'R1-V5')
        self.assertEqual(rounds[1]['issue_lifecycle'][0]['status'], 'resolved')
        self.assertEqual(rounds[1]['issue_lifecycle'][0]['history'][-1]['outcome'], 'corrected')
        summary = next(event['data'] for event in storage.get_run('structured-repair')['events'] if event['event']=='quality_summary')
        self.assertEqual(summary['issue_lifecycle'][0]['history'], rounds[1]['issue_lifecycle'][0]['history'])
        self.assertFalse(summary['issue_lifecycle'][0]['returned_report_unverified'])
        self.assertEqual(model.ainvoke.await_count,2)

    def test_hard_citation_failure_cannot_be_waived_by_model(self):
        from api import storage
        storage.create_run('hard-citation','问题','问题',False,1)
        result={'report':'依据不存在的资料[S99]','evidence':[],'called_sources':[],'pdf_produced':False}
        async def run():
            with patch.object(main_agent,'main_agent_run',AsyncMock(return_value=result)), \
                    patch.object(main_agent,'get_llm') as model:
                answer=await main_agent.run_deep_agent('问题','hard-citation',1)
            model.assert_not_called()
            self.assertIn('未达标',answer)
        asyncio.run(run())
        record=next(event['data'] for event in storage.get_run('hard-citation')['events'] if event['event']=='reflection')
        self.assertEqual(record['verification']['status'],'not_run')
        self.assertIn('引用了不存在的来源编号：S99',record['hard_issues'])

    def test_invalid_verifier_stops_without_reexecuting_task(self):
        from api import storage
        storage.create_run('invalid-review','问题','问题',False,3)
        result={'report':'当前回答','evidence':[],'called_sources':[],'pdf_produced':False}
        model=AsyncMock()
        model.ainvoke.return_value=AIMessage(content='通过')
        async def run():
            with patch.object(main_agent,'main_agent_run',AsyncMock(return_value=result)) as runner, \
                    patch.object(main_agent,'get_llm',return_value=model):
                answer=await main_agent.run_deep_agent('问题','invalid-review',3)
            self.assertIn('未达标',answer)
            self.assertEqual(runner.await_count,1)
        asyncio.run(run())
        stored=storage.get_run('invalid-review')
        summary=next(event['data'] for event in stored['events'] if event['event']=='quality_summary')
        self.assertEqual(summary['stop_reason'],'review_unavailable')
        self.assertFalse(summary['passed'])

    def test_revision_context_prioritizes_evidence_named_by_reviewer(self):
        items = [{'id': f'S{i}', 'snippet': f'原文{i}' * 2000} for i in range(1, 30)]
        text = main_agent._revision_evidence(items, ['S1 的原文已完整给出定义'])
        blocks=[json.loads(block) for block in text.split('\n\n')]
        self.assertEqual(blocks[1]['id'],'S1')
        self.assertTrue(blocks[1]['requires_readback'])
        self.assertIn('回读原始证据',text)
        self.assertLessEqual(len(text), 20000)

    def test_custom_round_budget_can_reach_fifth_round(self):
        async def run():
            results = [{'report': f'修订结果{i}',
                        'evidence': [{'id': 'S1', 'type': 'knowledge', 'snippet': f'本轮新增资料{i}'}],
                        'called_sources': [],
                        'required_sources': [], 'pdf_produced': False} for i in range(5)]
            with patch.object(main_agent, 'main_agent_run', AsyncMock(side_effect=results)) as runner, \
                    patch.object(main_agent, 'soft_review', AsyncMock(side_effect=['尚有缺口'] * 4 + [None])):
                text = await main_agent.run_deep_agent('普通问答', 'five', 5)
            self.assertEqual(runner.await_count, 5)
            self.assertNotIn('未达标说明', text)
        asyncio.run(run())

    def test_unchanged_results_stop_early_and_report_reason(self):
        server.storage.create_run('stalled','普通问答','普通问答',False,6)
        async def run():
            result = {'report': '相同回答', 'evidence': [], 'called_sources': [], 'required_sources': [], 'pdf_produced': False}
            with patch.object(main_agent, 'main_agent_run', AsyncMock(return_value=result)) as runner, \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value='缺少依据')):
                text = await main_agent.run_deep_agent('普通问答', 'stalled', 6)
            self.assertEqual(runner.await_count, 3)
            summary=next(e['data'] for e in server.storage.get_run('stalled')['events'] if e['event']=='quality_summary')
            self.assertEqual(summary['stop_reason'],'no_progress')
            self.assertEqual((summary['rounds_used'],summary['max_rounds']),(3,6))
            self.assertIn('尚未核对通过', text)
            self.assertNotIn('已用完本次选择', text)
        asyncio.run(run())

    def test_new_evidence_counts_as_progress_and_latest_tie_is_kept(self):
        async def run():
            results = [{'report': '候选结果', 'evidence': [{'snippet': f'新增依据{i}'}],
                        'called_sources': [], 'required_sources': [], 'pdf_produced': False} for i in range(4)]
            results[-1]['report'] = '最新结果'
            with patch.object(main_agent, 'main_agent_run', AsyncMock(side_effect=results)) as runner, \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value='仍有缺口')):
                text = await main_agent.run_deep_agent('普通问答', 'novel', 4)
            self.assertEqual(runner.await_count, 4)
            from tools.result_assessment import report_hash
            self.assertEqual(server.storage.load_context_artifact('novel',report_hash('最新结果')),'最新结果')
            self.assertFalse(text.startswith('最新结果'))
            self.assertIn('未达标',text)
        asyncio.run(run())

    def test_unavailable_review_is_degraded_instead_of_passed(self):
        async def unavailable(result):
            result['soft_review_status'] = 'skipped'
        async def run():
            result = {'report': '回答', 'evidence': [], 'called_sources': [], 'required_sources': [], 'pdf_produced': False}
            with patch.object(main_agent, 'main_agent_run', AsyncMock(return_value=result)) as runner, \
                    patch.object(main_agent, 'soft_review', side_effect=unavailable):
                text = await main_agent.run_deep_agent('普通问答', 'unreviewed', 6)
            self.assertEqual(runner.await_count, 1)
            self.assertIn('核对服务暂时不可用', text)
            self.assertIn('【未达标说明】', text)
        asyncio.run(run())

    def test_corrected_unreviewed_draft_outranks_known_failed_first_draft(self):
        from api import storage
        storage.create_run('prefer-correction','普通问答','普通问答',False,3)
        drafts=[{'report':text,'evidence':[],'called_sources':[],'pdf_produced':False}
                for text in ('已知错误的首稿','已修正但审查尚未完成')]
        async def review(result):
            if result['report']==drafts[0]['report']:
                result['soft_review_status']='failed'
                result['verification']={'checks':[{'id':'V1','criterion':'task_completion','status':'failed',
                    'detail':'结论与证据矛盾','suggestion':'修正结论','action':'revise_answer'}]}
                return '结论与证据矛盾'
            result['soft_review_status']='skipped'
            result['soft_review_detail']='审查服务不可用'
            result['verification']={'checks':[],'status':'unverified'}
        async def run():
            with patch.object(main_agent,'main_agent_run',AsyncMock(side_effect=drafts)),patch.object(main_agent,'soft_review',side_effect=review):
                answer=await main_agent.run_deep_agent('普通问答','prefer-correction',3)
            from tools.result_assessment import report_hash
            self.assertEqual(storage.load_context_artifact('prefer-correction',report_hash(drafts[1]['report'])),drafts[1]['report'])
            self.assertNotIn(drafts[0]['report'],answer)
            self.assertFalse(answer.startswith(drafts[1]['report']))
            self.assertIn('未达标',answer)
        asyncio.run(run())
        summary=next(e['data'] for e in storage.get_run('prefer-correction')['events'] if e['event']=='quality_summary')
        self.assertFalse(summary['passed']);self.assertEqual(summary['stop_reason'],'review_unavailable')
        self.assertEqual(summary['returned_round'],2)

    def test_auto_budget_stops_at_recommendation_and_persists_selection(self):
        from api import storage
        storage.create_run('auto-budget', '普通问答', '普通问答', False, round_mode='auto')
        async def run():
            result = {'report': '待核对回答', 'evidence': [], 'called_sources': [], 'required_sources': [], 'pdf_produced': False}
            with patch.object(main_agent, 'main_agent_run', AsyncMock(return_value=result)) as runner, \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value='缺少依据')):
                text = await main_agent.run_deep_agent('普通问答', 'auto-budget', None)
            self.assertEqual(runner.await_count, 2)
            self.assertIn('【未达标说明】', text)
        asyncio.run(run())
        stored = storage.get_run('auto-budget')
        self.assertEqual(stored['round_mode'], 'auto')
        self.assertEqual(stored['max_rounds'], 2)
        self.assertEqual(stored['plan']['round_budget']['recommended_rounds'], 2)
        self.assertEqual(storage.list_runs()[0]['round_mode'], 'auto')
        storage.create_run('auto-budget', '普通问答', '普通问答', False, 6, round_mode='manual')
        self.assertEqual(storage.get_run('auto-budget')['round_mode'], 'manual')
        with storage._connect() as conn:
            previous = conn.execute('SELECT snapshot_json FROM execution_history WHERE thread_id=?', ('auto-budget',)).fetchone()[0]
        import json
        self.assertEqual(json.loads(previous)['round_mode'], 'auto')
        self.assertEqual(json.loads(previous)['max_rounds'], 2)

    def test_api_auto_budget_reaches_executor_without_fixed_three_override(self):
        async def fake_agent(query, thread_id, max_rounds=3):
            self.assertIsNone(max_rounds)
            await server.monitor.report(thread_id, 'plan_created', '推荐轮次', {'max_rounds': 4, 'round_budget': {'mode': 'auto', 'max_rounds': 4}})
            return '已验证的回答'
        with patch.object(server, 'run_deep_agent', side_effect=fake_agent):
            with TestClient(server.app) as client, client.websocket_connect('/ws/api-auto-budget') as ws:
                response = client.post('/api/task', json={'query': 'test', 'thread_id': 'api-auto-budget', 'round_mode': 'auto'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(ws.receive_json()['event'], 'session_created')
                self.assertEqual(ws.receive_json()['event'], 'plan_created')
                self.assertEqual(ws.receive_json()['event'], 'task_result')
                run = client.get('/api/runs/api-auto-budget').json()
                self.assertEqual(run['round_mode'], 'auto')
                self.assertEqual(run['max_rounds'], 4)
                self.assertEqual(client.post('/api/task', json={'query': 'q', 'round_mode': 'unlimited'}).status_code, 422)

    def test_round_budget_validation_and_storage(self):
        from pydantic import ValidationError
        from api import storage
        for invalid in [0, 7, -1, 2.5, True, '5']:
            with self.assertRaises(ValidationError):
                server.TaskRequest(query='test', max_rounds=invalid)
        self.assertEqual(server.TaskRequest(query='test').max_rounds, 3)
        storage.create_run('budget-storage', 'q', 'q', False, 5)
        self.assertEqual(storage.get_run('budget-storage')['max_rounds'], 5)
        storage.create_run('budget-storage', 'q', 'q', False)
        self.assertEqual(storage.get_run('budget-storage')['max_rounds'], 3)
        for invalid in [0, 7, True]:
            with self.assertRaises(ValueError):
                asyncio.run(main_agent.run_deep_agent('q', 'invalid-budget', invalid))

    def test_empty_soft_review_is_failure(self):
        async def run():
            fake = AsyncMock()
            fake.ainvoke.return_value = AIMessage(content='')
            with patch.object(main_agent, 'get_llm', return_value=fake):
                result = {'report': 'test', 'query': 'test'}
                issue = await main_agent.soft_review(result)
            self.assertIsNone(issue)
            self.assertEqual(result['soft_review_status'], 'skipped')
        asyncio.run(run())

    def test_context_cleanup_on_exception(self):
        async def run():
            with patch.object(main_agent, 'main_agent_run', AsyncMock(side_effect=RuntimeError('boom'))):
                with self.assertRaises(RuntimeError):
                    await main_agent.run_deep_agent('hello', 'context')
            self.assertIsNone(context.get_session_dir())
        asyncio.run(run())

    def test_concurrent_session_contexts(self):
        from tools.markdown_tools import generate_markdown
        async def fake_run(query, round_num, required_sources=None):
            await asyncio.sleep(0.01)
            base_query = query.split('\n\n【系统任务计划')[0]
            generate_markdown.invoke({'content': base_query, 'filename': 'answer'})
            return {'report': base_query, 'called_sources': [], 'required_sources': [],
                    'require_pdf': False, 'pdf_produced': False}
        async def run():
            with patch.object(main_agent, 'main_agent_run', side_effect=fake_run), patch.object(main_agent, 'soft_review', AsyncMock(return_value=None)):
                results = await asyncio.gather(main_agent.run_deep_agent('答案甲', 'one'), main_agent.run_deep_agent('答案乙', 'two'))
            self.assertEqual(results, ['答案甲', '答案乙'])
            self.assertEqual((path_utils.get_session_dir('one') / 'answer.md').read_text(), '答案甲')
            self.assertEqual((path_utils.get_session_dir('two') / 'answer.md').read_text(), '答案乙')
        asyncio.run(run())

    def test_index_build_safety(self):
        from scripts.build_knowledge_base import _check_wipe_safety
        unsafe = self.root / 'source'
        unsafe.mkdir()
        (unsafe / 'main.py').write_text('important source')
        with self.assertRaises(ValueError):
            _check_wipe_safety(unsafe)
        self.assertEqual((unsafe / 'main.py').read_text(), 'important source')

    def test_real_graph_file_tools_with_fake_model(self):
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from tools.content_verification import review_anchor_index

        class ToolModel(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        content = '# 集成测试报告\n\n' + '测试数据仅用于验证编排与文件工具，不构成业务分析结论。' * 60
        task_anchor = review_anchor_index({'query': '生成一份 PDF 报告', 'report': content})['task'][0]['id']
        model = ToolModel(responses=[
            AIMessage(content='', tool_calls=[{'id': 'md1', 'name': 'generate_markdown', 'args': {'content': content, 'filename': 'integration'}}]),
            AIMessage(content='', tool_calls=[{'id': 'pdf1', 'name': 'convert_md_to_pdf', 'args': {'md_filename': 'integration.md'}}]),
            AIMessage(content='已成功创建'),
            AIMessage(content='',tool_calls=[{'id':'review-1','name':'ContentReviewDecision','args':{'verdict': 'passed', 'checks': [
                {'criterion': key, 'status': 'passed', 'detail': '按要求完成并核对。',
                 'task_anchor_id': task_anchor,
                 'task_quote': '生成一份 PDF 报告' if key == 'task_completion' else ''}
                for key in ('task_completion','evidence_support','business_rules','consistency','uncertainty')
            ]}}]),
        ])
        self.default_plan.task_type = 'report'
        self.default_plan.require_report = True
        self.default_plan.require_pdf = True
        self.default_plan.required_sources = []
        async def run():
            with patch.object(main_agent, 'get_llm', return_value=model):
                report = await main_agent.run_deep_agent('生成一份 PDF 报告', 'graph')
            self.assertEqual(report,main_agent._report_with_pdf_delivery(content,path_utils.get_session_dir('graph'),
                             [path_utils.get_session_dir('graph')/'integration.pdf']))
            directory = path_utils.get_session_dir('graph')
            self.assertTrue((directory / 'integration.pdf').read_bytes().startswith(b'%PDF-'))
            self.assertIsNone(context.get_session_dir())
        asyncio.run(run())

    def test_pdf_cross_platform_and_chinese(self):
        from pypdf import PdfReader
        p = convert_markdown_to_pdf('# 测试报告\n\n这是中文正文。\n\n| 药品 | 数量 |\n|---|---|\n| 阿莫西林 | 100 |', self.output / 'test.pdf')
        text = '\n'.join(page.extract_text() for page in PdfReader(p).pages)
        self.assertIn('阿莫西林', text)
        self.assertIn('100', text)

    def test_classify_request(self):
        cases = {
            '列出所有抗生素类药品': (False, False),
            '根据销售数据生成一份本周销售周报': (True, False),
            '生成一份阿莫西林药品市场分析报告（PDF）': (True, True),
            '不要生成 PDF，直接回答问题': (False, False),
            '不要生成 PDF 文件': (False, False),
            '不需要生成报告': (False, False),
            '盘点当前库存，列出库存量低于阈值的药品': (False, False),
        }
        for query, expected in cases.items():
            self.assertEqual(main_agent.classify_request(query), expected, query)

    def test_fallback_plan_respects_explicit_source_constraints(self):
        plan = main_agent._fallback_plan('只使用数据库中的数据，生成一份 PDF 市场分析报告')
        self.assertEqual(plan.task_type, 'report')
        self.assertTrue(plan.require_pdf)
        self.assertEqual(plan.required_sources, ['db_query'])

        qa = main_agent._fallback_plan('列出所有抗生素类药品')
        self.assertEqual(qa.task_type, 'qa')
        self.assertEqual(qa.required_sources, ['db_query'])

        kb = main_agent._fallback_plan('查询内部制度知识库中的采购审批要求')
        self.assertEqual(kb.required_sources, ['knowledge'])

    def test_normalize_plan_removes_forbidden_sources(self):
        plan = main_agent.TaskPlan(
            task_type='report', require_report=True, require_pdf=False,
            required_sources=['web_search', 'db_query', 'knowledge'],
            steps=['a'], output_artifacts=['x'], assumptions=[],
        )
        normalized = main_agent._normalize_plan(plan, '只使用数据库回答，不要联网，不要查知识库')
        self.assertEqual(normalized.required_sources, ['db_query'])
        self.assertTrue(normalized.require_report)
        self.assertFalse(normalized.require_pdf)

    def test_format_plan_prompt_exposes_required_sources_and_steps(self):
        plan = main_agent.TaskPlan(
            task_type='report', require_report=True, require_pdf=True,
            required_sources=['db_query', 'knowledge'],
            steps=['查库', '整理', '写报告'], output_artifacts=['PDF 报告'], assumptions=['示例假设'],
        )
        text = main_agent.format_plan_prompt(plan)
        self.assertIn('报告生成', text)
        self.assertIn('db_query', text)
        self.assertIn('knowledge', text)
        self.assertIn('1. 查库', text)
        self.assertIn('PDF 报告', text)

    def test_build_task_plan_uses_structured_output(self):
        class FakePlanner:
            async def ainvoke(self, messages):
                return main_agent.TaskPlan(
                    task_type='report', require_report=True, require_pdf=True,
                    required_sources=['db_query'], steps=['查库'], output_artifacts=['PDF 报告'],
                    assumptions=[],
                )

        class FakeLLM:
            def with_structured_output(self, schema, method=None):
                self.schema = schema
                return FakePlanner()

        llm = FakeLLM()
        async def run():
            with patch.object(main_agent, 'get_llm', return_value=llm):
                return await self.real_build_task_plan('生成 PDF 报告', [])
        plan = asyncio.run(run())
        self.assertEqual(plan.task_type, 'report')
        self.assertEqual(plan.required_sources, ['db_query'])
        self.assertIs(llm.schema, main_agent.PlannerPlan)
        self.assertNotIn('department_request', llm.schema.model_json_schema()['properties'])

    def test_build_task_plan_falls_back_without_text_scan(self):
        class BadLLM:
            def with_structured_output(self, schema, method=None):
                raise RuntimeError('planner unavailable')

        async def run():
            with patch.object(main_agent, 'get_llm', return_value=BadLLM()):
                return await self.real_build_task_plan('生成报告', [])
        plan = asyncio.run(run())
        self.assertTrue(plan.require_report)
        self.assertTrue(any('保守计划' in item for item in plan.assumptions))
        self.assertFalse(hasattr(main_agent, '_declare_required_sources'))
        self.assertFalse(hasattr(main_agent, '_parse_plan_sources'))

    def test_plan_sources_drive_main_agent_and_h4(self):
        async def run():
            plan = main_agent.TaskPlan(
                task_type='qa', require_report=False, require_pdf=False,
                required_sources=['knowledge'], steps=['检索'], output_artifacts=[], assumptions=[],
            )
            seen = {}
            async def fake_run(query, round_num, required_sources=None):
                seen['sources'] = required_sources
                return {'report': '答案', 'called_sources': ['knowledge'],
                        'required_sources': required_sources, 'require_pdf': False,
                        'pdf_produced': False}
            with patch.object(main_agent, 'build_task_plan', AsyncMock(return_value=plan)), \
                    patch.object(main_agent, 'main_agent_run', side_effect=fake_run), \
                    patch.object(main_agent, 'soft_review', AsyncMock(return_value=None)):
                text = await main_agent.run_deep_agent('测试计划流', 'planflow')
            return text, seen
        text, seen = asyncio.run(run())
        self.assertEqual(text, '答案')
        self.assertEqual(seen['sources'], ['knowledge'])
        self.assertIn('session_planflow', [p.name for p in self.output.iterdir() if p.is_dir()])

    def test_soft_review_requires_a_complete_checklist_instead_of_pass_words(self):
        async def run(text):
            fake = AsyncMock()
            fake.ainvoke.return_value = AIMessage(content=text)
            result = {'report': '正文', 'query': '问题'}
            with patch.object(main_agent, 'get_llm', return_value=fake):
                await main_agent.soft_review(result)
            return result
        for text in ['通过', '通过。', '**通过**', '审查通过', '通过，没有问题', '不通过：结论无依据', '']:
            result = asyncio.run(run(text))
            self.assertEqual(result['soft_review_status'], 'skipped', text)
            self.assertEqual(result['verification']['status'], 'unverified')

    def test_soft_review_exception_does_not_break_task(self):
        async def run():
            fake = AsyncMock()
            fake.ainvoke.side_effect = RuntimeError('review timeout')
            with patch.object(main_agent, 'get_llm', return_value=fake):
                return await main_agent.soft_review({'report': '正文', 'query': '问题'})
        self.assertIsNone(asyncio.run(run()))

    def test_report_extracts_markdown_extension(self):
        (self.output / 'report.markdown').write_text('新格式报告正文', encoding='utf-8')
        msg = AIMessage(content='', tool_calls=[{
            'name': 'generate_markdown',
            'args': {'filename': 'report.markdown', 'content': 'x'},
            'id': '1',
        }])
        self.assertEqual(main_agent._extract_report(self.output, [msg]), '新格式报告正文')

    def test_web_search_budget_per_round(self):
        import tools.internet_search_tool as search_tool

        class FakeClient:
            def search(self, **kwargs):
                return {'results': [{'title': '标题', 'url': 'https://example.com', 'content': '正文'}]}

        context.set_session('budget', '/tmp')
        source_policy.configure(['web_search'])
        context.set_round(1)
        context.reset_tool_budget()
        try:
            with patch.object(search_tool, '_client', FakeClient()):
                outputs = [
                    search_tool.internet_search.invoke({'query': f'q{i}'})
                    for i in range(5)
                ]
                self.assertEqual(sum('上限' in out for out in outputs), 2)
                # 新轮次预算重置，允许继续检索
                context.set_round(2)
                context.reset_tool_budget()
                self.assertIn('标题', search_tool.internet_search.invoke({'query': 'q'}))
        finally:
            context.reset_session()

    def test_concurrent_knowledge_search_is_serialized(self):
        import tools.knowledge_base_tool as kb

        active = 0
        peak = 0

        class FakeChroma:
            def __init__(self, **kwargs):
                pass

            def similarity_search(self, query, k):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                time.sleep(0.03)
                active -= 1
                return [Document(page_content=f'内容 {query}', metadata={'source': 'x.txt'})]

        async def run():
            return await asyncio.gather(*[
                kb.search_knowledge_base.ainvoke({'query': f'q{i}', 'k': 1})
                for i in range(4)
            ])

        vs_dir = self.root / 'vector_store_test'
        vs_dir.mkdir()
        (vs_dir / 'chroma.sqlite3').write_text('placeholder')
        with patch.dict(os.environ, {'VECTOR_STORE_DIR': str(vs_dir)}), \
                patch.object(kb, 'Chroma', FakeChroma), \
                patch.object(kb, '_get_embedding', return_value=object()):
            results = asyncio.run(run())

        self.assertTrue(all('内容' in result for result in results), results)
        self.assertEqual(peak, 1, '并发 Chroma 查询必须被串行化')

    def test_subagents_deny_file_writes(self):
        from agent.sub_agents import SUBAGENTS

        for spec in SUBAGENTS:
            rules = spec.get('permissions') or []
            self.assertTrue(
                any('write' in rule.operations and rule.mode == 'deny' for rule in rules),
                f'{spec["name"]} 缺少写权限拒绝规则',
            )

    def test_ws_sends_are_serialized(self):
        from api.monitor import ConnectionManager

        class FakeWS:
            def __init__(self):
                self.active = 0
                self.peak = 0
                self.messages = []

            async def accept(self):
                pass

            async def send_text(self, text):
                self.active += 1
                self.peak = max(self.peak, self.active)
                await asyncio.sleep(0.01)
                self.messages.append(text)
                self.active -= 1

        async def run():
            manager = ConnectionManager()
            ws = FakeWS()
            await manager.connect('ws', ws)
            await asyncio.gather(*[
                manager.send_event('ws', 'tool_end', f'm{i}', {}) for i in range(8)
            ])
            await manager.drain()
            await manager.shutdown()
            return ws

        ws = asyncio.run(run())
        self.assertEqual(ws.peak, 1, '同一 WebSocket 的发送必须串行化')
        self.assertEqual(len(ws.messages), 8)

    def test_task_cleanup_is_identity_safe(self):
        old_task = object()
        new_task = object()
        server._tasks['cleanup'] = old_task
        server._finish_task('cleanup', old_task)
        self.assertNotIn('cleanup', server._tasks)
        server._tasks['cleanup'] = new_task
        server._finish_task('cleanup', old_task)
        self.assertIs(server._tasks.get('cleanup'), new_task)
        server._tasks.pop('cleanup', None)

    def test_task_timeout_cleans_context(self):
        async def slow(query, thread_id, session_dir):
            await asyncio.sleep(1)

        async def run():
            with patch.dict(os.environ, {'TASK_TIMEOUT': '0.02'}), \
                    patch.object(main_agent, '_run_session', side_effect=slow):
                with self.assertRaisesRegex(TimeoutError, '任务执行超过'):
                    await main_agent.run_deep_agent('慢任务', 'timeout')
            self.assertIsNone(context.get_session_dir())
            self.assertIsNone(context.get_thread_id())

        asyncio.run(run())

    def test_inner_timeout_does_not_claim_task_deadline_expired(self):
        async def provider_timeout(*args):
            raise TimeoutError('模型连接读取超时')

        async def run():
            with patch.dict(os.environ, {'TASK_TIMEOUT': '960'}), \
                    patch.object(main_agent, '_run_session', side_effect=provider_timeout):
                with self.assertRaisesRegex(TimeoutError, '^模型连接读取超时$'):
                    await main_agent.run_deep_agent('核对案件', 'inner-timeout')
            self.assertIsNone(context.get_thread_id())

        asyncio.run(run())

    def test_model_list_success_only_reports_endpoint_reachable(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-only', 'DEEPSEEK_MODEL': 'test-model'}), \
                patch('casework.privacy.ensure_model_allowed'), \
                patch('urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.status = 200
            status = server._check_model()
        self.assertEqual(status['state'], 'reachable')
        self.assertEqual(status['label'], '端点可达')
        self.assertIn('尚未验证生成', status['detail'])

    def test_task_failure_distinguishes_provider_timeout_and_total_deadline(self):
        for thread, failure, reason in [
            ('provider-timeout', TimeoutError('模型读取超时'), 'execution_error'),
            ('total-deadline', main_agent.TaskDeadlineExceeded('任务执行超过 900 秒'), 'timeout'),
        ]:
            server.storage.create_run(thread, '核对案件', '核对案件', False)
            with patch.object(server, 'run_deep_agent', AsyncMock(side_effect=failure)):
                asyncio.run(server._execute_task(thread, '核对案件'))
            run = server.storage.get_run(thread)
            self.assertEqual(run['status'], 'failed')
            self.assertEqual(run['stop_reason'], reason)
            self.assertIn(str(failure), run['error'])


if __name__ == '__main__':
    unittest.main()
