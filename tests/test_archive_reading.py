"""Citation focus must preserve version, original text, and all result cases."""
import copy
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.casework import router
from casework import catalog, store
from casework.document_view import document_page
from casework.search import matched_terms, search
from tests import test_department_casework as fixtures


class CitationPageTests(unittest.TestCase):
    def item(self):
        return dict(id='f', revision='original', parser_key='p', payload={'chunks':[
            dict(text=f'未经改写的原文 {i}', location=f'正文块 {i}') for i in range(25)]})

    def test_focus_beyond_first_page_and_neighbors_cross_page_boundary(self):
        item = self.item()
        before = copy.deepcopy(item)
        key = store.digest(['f', 'original', 'p', 10])
        result = document_page(item, focus_chunk_id=key)
        self.assertEqual(result['offset'], 10)
        self.assertEqual(result['focus_index'], 10)
        self.assertEqual([c['ordinal'] for c in result['focus_context']], [9, 10, 11])
        self.assertEqual([c['cited'] for c in result['focus_context']], [False, True, False])
        self.assertEqual(result['focus_context'][1]['text'], before['payload']['chunks'][10]['text'])
        self.assertEqual(item, before)

    def test_stale_chunk_does_not_silently_focus_a_different_revision(self):
        result = document_page(self.item(), focus_chunk_id=store.digest(['f', 'old', 'p', 10]),
                               focus_location='正文块 10')
        self.assertEqual(result['focus_status'], 'not_found')
        self.assertIsNone(result['focus_index'])
        self.assertEqual(result['focus_context'], [])

    def test_location_excerpt_and_field_claims_focus_exact_originals(self):
        item = self.item()
        self.assertEqual(document_page(item, focus_location='正文块 24')['focus_index'], 24)
        self.assertEqual(document_page(item, focus_excerpt='原文 18')['focus_index'], 18)
        item['payload']['chunks'][4]['location'] = '工作表 案件信息 · 第2行'
        self.assertEqual(document_page(item, focus_location='工作表 案件信息 · 第2行 · 第3列')['focus_index'], 4)
        self.assertEqual(document_page(item, focus_location='工作表 案件信息 · 第20行 · 第3列')['focus_status'], 'not_found')

    def test_regular_pagination_and_empty_document(self):
        result = document_page(self.item(), offset=20)
        self.assertEqual(len(result['chunks']), 5)
        self.assertFalse(result['has_more'])
        self.assertEqual(result['focus_status'], 'not_requested')
        item = self.item(); item['payload']['chunks'] = []
        self.assertEqual(document_page(item, focus_location='missing')['chunks'], [])


class ArchiveReadingIntegrationTests(unittest.TestCase):
    setUp = fixtures.DepartmentCaseworkTests.setUp
    tearDown = fixtures.DepartmentCaseworkTests.tearDown

    def test_api_locates_original_and_retains_pagination_and_disk_hash_check(self):
        app = FastAPI(); app.include_router(router)
        result = search('向量数据库近邻检索', limit=30)
        self.assertTrue(result['results'])
        proof = result['results'][0]['sources'][0]
        with TestClient(app) as client:
            url = '/api/casework/documents/' + proof['file_id']
            data = client.get(url, params={k: proof[k] for k in ('revision', 'parser_key')} | {
                'focus_chunk_id': proof['chunk_id']}).json()
            self.assertEqual(data['focus_status'], 'located')
            self.assertEqual(data['source_status'], 'verified')
            self.assertEqual(next(c['text'] for c in data['focus_context'] if c['cited']), proof['excerpt'])
            self.assertEqual(data['revision'], proof['revision'])
            self.assertEqual(data['parser_key'], proof['parser_key'])
            plain = client.get(url, params={'offset': 0, 'limit': 1}).json()
            self.assertEqual(plain['focus_status'], 'not_requested')
            self.assertEqual(len(plain['chunks']), 1)
            bad = client.get(url, params={'focus_chunk_id': 'wrong'}).json()
            self.assertEqual(bad['focus_status'], 'not_found')

    def test_preview_metadata_preserves_all_cases_and_literal_source_words(self):
        result = search('向量数据库近邻检索', limit=30)
        self.assertGreater(len(result['results']), 3)
        self.assertEqual(result['matched_cases_in_retrieval_pool'], len(result['results']))
        for case in result['results']:
            for proof in case['sources']:
                for term in proof['matched_terms']:
                    self.assertIn(term.casefold(), proof['excerpt'].casefold())
        self.assertEqual(matched_terms('教师模型输出软标签', {'模型蒸馏'}), ['教师模型', '软标签'])
        self.assertNotIn('模型蒸馏', matched_terms('教师模型输出软标签', {'模型蒸馏'}))


if __name__ == '__main__':
    unittest.main()
