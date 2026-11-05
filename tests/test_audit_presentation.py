import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.casework import router
from casework import store


class AuditPresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'CASEWORK_DB_PATH': str(Path(self.temp.name) / 'audit.db')})
        self.env.start()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        with store.db(write=True) as conn:
            for index in range(35):
                store.audit(conn, 'r:case1', 'scan_publish', {}, {'generation': index})
            store.audit(conn, 'r:case1', 'field_confirmation', {'owner_name': '甲'}, {'owner_name': '乙'})
            store.audit(conn, 'r:case2', 'field_confirmation', {}, {'owner_name': '丙'})

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_filter_applies_before_count_and_pagination(self):
        result = self.client.get('/api/casework/audit', params={'action': 'scan_publish', 'limit': 30}).json()
        self.assertEqual(result['total'], 35)
        self.assertEqual(len(result['rows']), 30)
        self.assertTrue(result['has_more'])
        self.assertTrue(all(row['action'] == 'scan_publish' for row in result['rows']))
        tail = self.client.get('/api/casework/audit', params={'action': 'scan_publish', 'offset': 30, 'limit': 30}).json()
        self.assertEqual(len(tail['rows']), 5)
        self.assertFalse(tail['has_more'])
        self.assertTrue(set(row['id'] for row in result['rows']).isdisjoint(row['id'] for row in tail['rows']))

    def test_target_and_type_filters_combine_and_empty_is_explicit(self):
        result = self.client.get('/api/casework/audit', params={'action': 'field_confirmation', 'target': 'r:case1'}).json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['rows'][0]['before'], {'owner_name': '甲'})
        self.assertEqual(result['rows'][0]['after'], {'owner_name': '乙'})
        empty = self.client.get('/api/casework/audit', params={'action': 'unknown'}).json()
        self.assertEqual(empty['rows'], [])
        self.assertEqual(empty['total'], 0)
        self.assertFalse(empty['has_more'])
        self.assertEqual(self.client.get('/api/casework/audit', params={'action': ''}).json()['total'], 37)
