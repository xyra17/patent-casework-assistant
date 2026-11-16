"""Product entry scope does not revise historical evaluation scores."""
import json
import unittest
from pathlib import Path
from api import server

ROOT=Path(__file__).resolve().parents[1]
CATALOG=json.loads((ROOT/'config/session-examples.json').read_text())


class ScopeTrimTests(unittest.IsolatedAsyncioTestCase):

    def test_remaining_profiles_and_permissions_still_validate(self):
        for profile in ['case-materials','uploaded-table-analysis']:
            body=server.TaskRequest(query='查询现有资料',product_context={'profile':profile})
            self.assertEqual(body.product_context.profile,profile)
        from api import task_sources
        from agent.main_agent import TaskPlan,_normalize_plan
        query=next(t['value'] for t in CATALOG['templates'] if t['id']=='case-materials')
        with task_sources.scope(['db_query']):
            plan=_normalize_plan(TaskPlan(task_type='qa',require_report=False,require_pdf=False,
                required_sources=['db_query','knowledge']),query,[])
        self.assertEqual(plan.required_sources,['db_query'])
        self.assertIn('knowledge',plan.forbidden_sources)


    def test_catalog_has_all_22_assistance_entries(self):
        self.assertEqual(len(CATALOG['templates']),22)
        self.assertFalse(CATALOG.get('manual_workflows'))
        self.assertTrue({'document-comparison','disclosure-search','public-reference'} <= {t['id'] for t in CATALOG['templates']})
