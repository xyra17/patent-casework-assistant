"""知识库管理使用临时语料和真实 Chroma；向量模型替换为本地确定性嵌入。"""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.embeddings import Embeddings

from api import knowledge
from scripts import build_knowledge_base as builder
from tools import knowledge_base_tool as kb


class TinyEmbedding(Embeddings):
    def embed_documents(self, texts):
        return [[float(len(text)), 1.0, 0.5] for text in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kb-management-test-')
        self.root = Path(self.temp.name).resolve()
        self.source, self.index = self.root / 'source', self.root / 'index'
        self.source.mkdir()
        self.env = patch.dict('os.environ', {'KNOWLEDGE_BASE_DIR': str(self.source),
                                            'VECTOR_STORE_DIR': str(self.index)})
        self.env.start()
        self.model = TinyEmbedding()
        self.patches = [patch.object(builder, '_get_embedding', return_value=self.model),
                        patch.object(kb, '_get_embedding', return_value=self.model)]
        for item in self.patches:
            item.start()
        knowledge._busy = False
        knowledge._job = {'state': 'idle', 'message': '', 'finished_at': None}
        app = FastAPI()
        app.include_router(knowledge.router)
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        builder._close_store(self.index)
        self.env.stop()
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def upload(self, name='制度.txt', body='请假需提前三天申请', replace=''):
        return self.client.post('/api/knowledge/documents', data={'replace': replace},
                                files=[('files', (name, body.encode()))])

    def rebuild(self):
        response = self.client.post('/api/knowledge/rebuild')
        self.assertEqual(response.status_code, 202)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = self.client.get('/api/knowledge').json()
            if not result['busy']:
                return result
            time.sleep(.02)
        self.fail('索引更新未完成')

    def test_upload_publish_replace_search_delete_and_empty_publish(self):
        self.assertEqual(self.client.get('/api/knowledge').json()['documents'], [])
        self.assertEqual(self.upload().status_code, 200)
        before = self.client.get('/api/knowledge').json()
        self.assertTrue(before['needs_update'])
        self.assertFalse(before['documents'][0]['indexed'])
        ready = self.rebuild()
        self.assertEqual(ready['job']['state'], 'succeeded', ready)
        self.assertFalse(ready['needs_update'])
        self.assertTrue(ready['version_known'])
        self.assertTrue(ready['documents'][0]['indexed'])
        response = self.client.post('/api/knowledge/search', json={'query': '请假'}).json()
        original = response['result']
        passage = response['passages'][0]
        self.assertEqual(passage['source'], '制度.txt')
        self.assertIn('提前三天', passage['text'])
        self.assertTrue(passage['evidence_id'])
        self.assertTrue(passage['location'])
        self.assertIsInstance(response['warnings'], list)
        self.assertIn('提前三天', original)
        self.assertEqual(self.upload('新版.txt', '请假需提前五天申请', replace='制度.txt').status_code, 200)
        pending = self.client.get('/api/knowledge').json()
        self.assertTrue(pending['needs_update'])
        self.assertIn('提前三天', self.client.post('/api/knowledge/search', json={'query': '请假'}).json()['result'])
        self.assertEqual(self.rebuild()['job']['state'], 'succeeded')
        current = self.client.post('/api/knowledge/search', json={'query': '请假'}).json()['result']
        self.assertIn('提前五天', current)
        self.assertNotIn('提前三天', current)
        self.assertEqual(self.client.get('/api/knowledge/documents/download', params={'name': '制度.txt'}).content.decode(), '请假需提前五天申请')
        self.assertEqual(self.client.delete('/api/knowledge/documents', params={'name': '制度.txt'}).status_code, 200)
        self.assertEqual(self.client.get('/api/knowledge').json()['removed_count'], 1)
        empty = self.rebuild()
        self.assertEqual(empty['job']['state'], 'succeeded', empty)
        self.assertEqual(empty['chunks'], 0)
        self.assertFalse(empty['needs_update'])
        self.assertEqual(self.client.post('/api/knowledge/search', json={'query': '请假'}).json()['result'], '知识库中未检索到相关内容')

    def test_old_index_remains_searchable_while_parsing_and_embedding_new_index(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        self.upload()
        self.assertEqual(self.rebuild()['job']['state'], 'succeeded')
        self.upload('新版.txt', '请假需提前五天申请', replace='制度.txt')
        for phase in ('parsing', 'embedding'):
            entered, release = threading.Event(), threading.Event()
            original_load = builder.load_documents
            def blocked_load(*args, **kwargs):
                entered.set()
                if not release.wait(5): raise TimeoutError('test release')
                return original_load(*args, **kwargs)
            class BlockingEmbedding(TinyEmbedding):
                def embed_documents(self, texts):
                    if any('五天' in text for text in texts):
                        entered.set()
                        if not release.wait(5): raise TimeoutError('test release')
                    return super().embed_documents(texts)
            model = BlockingEmbedding()
            target = patch.object(builder, 'load_documents', side_effect=blocked_load) if phase == 'parsing' else patch.object(kb, '_get_embedding', return_value=model)
            with target:
                response = self.client.post('/api/knowledge/rebuild')
                self.assertEqual(response.status_code, 202)
                self.assertTrue(entered.wait(5))
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(self.client.post, '/api/knowledge/search', json={'query':'请假'})
                    try:
                        result = pending.result(timeout=2)
                        self.assertEqual(result.status_code, 200, result.text)
                        self.assertIn('提前三天', result.json()['result'])
                    finally:
                        release.set()
                deadline = time.monotonic() + 5
                while self.client.get('/api/knowledge').json()['busy'] and time.monotonic() < deadline:
                    time.sleep(.01)
            self.assertEqual(self.client.get('/api/knowledge').json()['job']['state'], 'succeeded')
            # Restore the initial published version before testing the second phase.
            self.upload('新版.txt', '请假需提前三天申请', replace='制度.txt')
            self.assertEqual(self.rebuild()['job']['state'], 'succeeded')
            self.upload('新版.txt', '请假需提前五天申请', replace='制度.txt')

    def test_failed_parse_and_failed_publish_preserve_old_index(self):
        self.upload()
        self.assertEqual(self.rebuild()['job']['state'], 'succeeded')
        self.upload('坏文档.pdf', 'not a pdf')
        failed = self.rebuild()
        self.assertEqual(failed['job']['state'], 'failed')
        self.assertIn('坏文档.pdf', failed['job']['message'])
        self.assertTrue(failed['needs_update'])
        self.assertIn('提前三天', self.client.post('/api/knowledge/search', json={'query': '请假'}).json()['result'])
        self.client.delete('/api/knowledge/documents', params={'name': '坏文档.pdf'})
        self.upload('新版.txt', '请假需提前五天申请', replace='制度.txt')
        rename = Path.rename
        def fail_publication(path, target):
            if path.name.startswith('.kb-build-'):
                raise OSError('simulated publish failure')
            return rename(path, target)
        with patch.object(Path, 'rename', fail_publication):
            self.assertEqual(self.rebuild()['job']['state'], 'failed')
        self.assertIn('提前三天', self.client.post('/api/knowledge/search', json={'query': '请假'}).json()['result'])

    def test_nested_documents_and_path_safety(self):
        nested = self.source / '制度'
        nested.mkdir()
        (nested / '流程.txt').write_text('审批步骤')
        document = self.client.get('/api/knowledge').json()['documents'][0]
        self.assertEqual(document['name'], '制度/流程.txt')
        self.assertEqual(self.upload('新.txt', '新审批步骤', replace='制度/流程.txt').status_code, 200)
        self.assertEqual((nested / '流程.txt').read_text(), '新审批步骤')
        for name in ['../outside.txt', '/tmp/outside.txt', '制度/../流程.txt', 'a\\b.txt', '']:
            self.assertEqual(self.client.delete('/api/knowledge/documents', params={'name': name}).status_code, 400)
        outside = self.root / 'protected.txt'
        outside.write_text('keep')
        (self.source / 'linked.txt').symlink_to(outside)
        self.assertEqual(self.client.delete('/api/knowledge/documents', params={'name': 'linked.txt'}).status_code, 403)
        self.assertEqual(self.client.get('/api/knowledge/documents/download', params={'name': 'linked.txt'}).status_code, 403)
        self.assertEqual(outside.read_text(), 'keep')

    def test_duplicates_limits_unsupported_and_busy(self):
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(self.upload('test.exe').status_code, 400)
        self.assertEqual(self.upload('empty.txt', '').status_code, 400)
        self.assertEqual(self.upload('new.pdf', 'data', replace='制度.txt').status_code, 400)
        with patch.object(knowledge, '_MAX_BYTES', 3):
            self.assertEqual(self.upload('new.txt', 'too large', replace='制度.txt').status_code, 413)
        self.assertEqual((self.source / '制度.txt').read_text(), '请假需提前三天申请')
        knowledge._busy = True
        try:
            self.assertEqual(self.upload('next.txt').status_code, 409)
            self.assertEqual(self.client.delete('/api/knowledge/documents', params={'name': '制度.txt'}).status_code, 409)
            self.assertEqual(self.client.post('/api/knowledge/rebuild').status_code, 409)
            self.assertEqual(self.client.post('/api/knowledge/search', json={'query': '审批'}).status_code, 409)
        finally:
            knowledge._busy = False
        self.assertEqual(self.client.post('/api/knowledge/search', json={'query': '审批'}).status_code, 409)


if __name__ == '__main__':
    unittest.main()
