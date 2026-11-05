import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from api import storage, recycle_bin, server
from utils import path_utils


class RecycleBinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(storage, '_DB_PATH', root / 'app.db'),
                        patch.object(path_utils, 'OUTPUT_DIR', root / 'output'),
                        patch.object(path_utils, 'UPLOAD_DIR', root / 'upload')]
        for item in self.patches:
            item.start()
        storage.init_db()
        self.client = TestClient(server.app)
        self.make_run('one')

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def make_run(self, thread_id):
        storage.create_run(thread_id, '原始问题', '原始问题', False)
        storage.append_event(thread_id, {'event': 'task_result', 'data': {'result': '历史回答'}})
        for directory in [path_utils.get_session_root(thread_id), path_utils.get_upload_dir(thread_id)]:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'sample.txt').write_text('附件与报告')

    def test_soft_delete_hidden_everywhere_and_restore_exact_history(self):
        revision = storage.save_intent_revision('one', '纠正后的问题', None)
        before = storage.get_run('one')
        response = self.client.delete('/api/runs/one')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'trashed')
        deleted = response.json()['deleted_at']
        self.assertEqual(self.client.delete('/api/runs/one').json()['deleted_at'], deleted)
        self.assertIsNone(storage.get_run('one'))
        self.assertEqual(storage.list_runs(), [])
        page = storage.run_page(tracked_ids=['one'])
        self.assertEqual((page['total'], page['tracked']), (0, {}))
        self.assertEqual(self.client.get('/api/runs/one').status_code, 410)
        for url in ['/api/attachments', '/api/files', '/api/download']:
            self.assertEqual(self.client.get(url, params={'thread_id': 'one', 'path': str(path_utils.get_session_root('one'))}).status_code, 410)
        self.assertEqual(self.client.post('/api/upload', data={'thread_id':'one'}, files={'files':('new.txt', b'new')}).status_code, 410)
        with self.assertRaises(ValueError):
            storage.create_run('one', 'new', 'new', False)
        self.assertEqual(self.client.get('/api/trash').json()['total'], 1)
        storage.init_db()  # restarting/migrating must preserve deletion and the deadline
        self.assertEqual(recycle_bin.entry('one')['deleted_at'], deleted)
        self.assertEqual(self.client.post('/api/trash/one/restore').status_code, 200)
        after = storage.get_run('one')
        for key in ['result', 'events', 'execution_id', 'intent_revisions', 'created_at']:
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['intent_revisions'][0]['id'], revision['id'])
        self.assertEqual((path_utils.get_upload_dir('one') / 'sample.txt').read_text(), '附件与报告')
        self.assertEqual(storage.run_page()['total'], 1)

    def test_expiration_boundary_and_cleanup_excludes_active_or_restored_sessions(self):
        start = datetime(2026, 10, 27, tzinfo=timezone.utc)
        with patch.object(recycle_bin, 'now', return_value=start):
            recycle_bin.move('one')
        self.make_run('active')
        self.make_run('restored')
        with patch.object(recycle_bin, 'now', return_value=start):
            recycle_bin.move('restored')
            recycle_bin.restore('restored')
        with patch.object(recycle_bin, 'now', return_value=start + timedelta(days=30) - timedelta(seconds=1)):
            self.assertEqual(recycle_bin.purge_expired(), [])
            self.assertIsNotNone(recycle_bin.entry('one'))
        with patch.object(recycle_bin, 'now', return_value=start + timedelta(days=30)):
            with self.assertRaises(ValueError):
                recycle_bin.restore('one')
            self.assertEqual(recycle_bin.purge_expired(), [])
        self.assertIsNone(recycle_bin.entry('one'))
        self.assertFalse(path_utils.get_session_root('one').exists())
        self.assertFalse(path_utils.get_upload_dir('one').exists())
        self.assertIsNotNone(storage.get_run('active'))
        self.assertIsNotNone(storage.get_run('restored'))

    def test_running_uploading_and_active_sessions_cannot_be_purged(self):
        self.assertEqual(self.client.delete('/api/trash/one').status_code, 404)
        storage.create_run('running', '任务', '任务', False)
        self.assertEqual(self.client.delete('/api/runs/running').status_code, 409)
        server._uploading.add('one')
        try:
            self.assertEqual(self.client.delete('/api/runs/one').status_code, 409)
        finally:
            server._uploading.discard('one')
        self.assertIsNotNone(storage.get_run('one'))

    def test_failed_cleanup_retains_retry_record_and_never_follows_symlinks(self):
        recycle_bin.move('one')
        with patch('api.recycle_bin.shutil.rmtree', side_effect=OSError('disk error')):
            self.assertEqual(self.client.delete('/api/trash/one').status_code, 500)
        self.assertIsNotNone(recycle_bin.entry('one'))
        self.assertEqual(self.client.post('/api/trash/one/restore').status_code, 409)
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_text('keep')
        (path_utils.get_session_root('one') / 'link').symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.client.delete('/api/trash/one').status_code, 200)
        self.assertEqual((outside / 'keep.txt').read_text(), 'keep')
        self.assertIsNone(recycle_bin.entry('one'))
