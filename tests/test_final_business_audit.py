"""Cross-entry recommendation behavior and atomic human assignment."""
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.casework import router
from casework import analytics, catalog, store
from tests import test_department_casework as fixtures


class FinalBusinessAuditTests(unittest.TestCase):
    setUp = fixtures.DepartmentCaseworkTests.setUp
    tearDown = fixtures.DepartmentCaseworkTests.tearDown
    case = fixtures.DepartmentCaseworkTests.case

    def client(self):
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def add_people(self):
        for person in json.loads((fixtures.ROOT/'examples/department_shared_drive/expected.json').read_text())['people']:
            analytics.save_person(**person)

    def test_workbench_gap_searches_original_query_and_does_not_return_ranked_candidates(self):
        from casework.search import search
        self.add_people()
        with self.client() as client, patch('casework.search.search', wraps=search) as lookup:
            response = client.post('/api/casework/recommendations', json={'query':'量子纠错逻辑量子比特'})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result['requires_user_confirmation'])
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['technical_lookup']['query'], '量子纠错逻辑量子比特')
        lookup.assert_called_once_with('量子纠错逻辑量子比特', limit=6)

    def test_workbench_known_mechanism_without_history_is_also_held(self):
        self.add_people()
        original = analytics.recommend

        def no_history(*args, **kwargs):
            result = original(*args, **kwargs)
            for person in result['candidates']:
                person['history_matches'] = []
                person['history_match_count'] = 0
            return result

        with self.client() as client, patch.object(analytics, 'recommend', side_effect=no_history):
            response = client.post('/api/casework/recommendations', json={'query':'模型蒸馏'})
        self.assertTrue(response.json()['requires_user_confirmation'])
        self.assertEqual(response.json()['candidates'], [])

    def test_workbench_explicit_workload_choice_has_names_without_ranks_or_assignment(self):
        self.add_people()
        before = store.digest(store.list_cases())
        with self.client() as client, patch('casework.search.search') as lookup:
            response = client.post('/api/casework/recommendations', json={
                'query':'模型蒸馏', 'recommendation_mode':'workload_only'})
        result = response.json()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(result['requires_user_confirmation'])
        self.assertTrue(result['candidates'])
        self.assertEqual([p['name'] for p in result['candidates']], sorted(p['name'] for p in result['candidates']))
        for person in result['candidates']:
            self.assertTrue({'score','domain_match','history_matches','history_match_count'}.isdisjoint(person))
        self.assertEqual(store.digest(store.list_cases()), before)
        lookup.assert_not_called()

    def test_registered_history_remains_available_and_invalid_target_is_rejected(self):
        self.add_people()
        with self.client() as client:
            response = client.post('/api/casework/recommendations', json={'query':'模型蒸馏'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['requires_user_confirmation'])
            self.assertTrue(any(p['history_matches'] for p in response.json()['candidates']))
            response = client.post('/api/casework/recommendations', json={'query':'模型蒸馏','case_key':'missing:case'})
            self.assertEqual(response.status_code, 400, response.text)

    def test_assignment_and_both_audit_records_roll_back_together(self):
        case = self.case('DEMO-2026-021')
        person = analytics.save_person('事务测试代理师', ['人工智能'])
        generation = store.get_root(self.ident)['generation']
        audit = store.audit

        def fail_assignment_audit(conn, target, action, *args, **kwargs):
            if action == 'assignment_confirmed':
                raise OSError('simulated audit write failure')
            return audit(conn, target, action, *args, **kwargs)

        with patch.object(store, 'audit', side_effect=fail_assignment_audit), self.assertRaises(OSError):
            analytics.assign(case['case_key'], person['id'], case['version'], '核对后分案', confirmed=True)
        self.assertEqual(self.case('DEMO-2026-021')['version'], case['version'])
        self.assertEqual(store.get_root(self.ident)['generation'], generation)
        with store.db() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM overrides WHERE case_key=?', (case['case_key'],)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit WHERE target=? AND action IN ('field_confirmation','assignment_confirmed')", (case['case_key'],)).fetchone()[0], 0)
        result = analytics.assign(case['case_key'], person['id'], case['version'], '核对后分案', confirmed=True)
        self.assertEqual(result['case']['owner_name'], person['name'])
        with store.db() as conn:
            rows = conn.execute("SELECT action FROM audit WHERE target=? AND action IN ('field_confirmation','assignment_confirmed')", (case['case_key'],)).fetchall()
        self.assertEqual({r['action'] for r in rows}, {'field_confirmation','assignment_confirmed'})

    def test_disabled_roots_reject_field_confirmation_and_assignment(self):
        case = self.case('DEMO-2026-021')
        person = analytics.save_person('事务测试代理师', ['人工智能'])
        with store.db(write=True) as conn:
            conn.execute('UPDATE roots SET enabled=0 WHERE id=?', (self.ident,))
        with self.assertRaises(ValueError):
            catalog.confirm(case['case_key'], {'owner_name':person['name']}, case['version'], '已停用目录')
        with self.assertRaises(ValueError):
            analytics.assign(case['case_key'], person['id'], case['version'], '已停用目录', confirmed=True)
