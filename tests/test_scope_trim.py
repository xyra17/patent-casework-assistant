"""Scope removal is not a revised evaluation result; no model is invoked."""
import json
import hashlib
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from api import server
from tools.result_assessment import assess_result

ROOT=Path(__file__).resolve().parents[1]
CATALOG=json.loads((ROOT/'config/session-examples.json').read_text())


class ScopeTrimTests(unittest.IsolatedAsyncioTestCase):
    async def test_retired_entries_stop_before_rules_model_or_task_creation(self):
        with (patch.object(server,'PROJECT_ROOT',ROOT/'runtime-without-source-catalog'),
              patch.object(server,'run_deep_agent',new_callable=AsyncMock) as agent,
              patch.object(server.rule_engine,'resolve',new_callable=AsyncMock) as rules,
              patch.object(server.storage,'create_run') as create):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
                for item in CATALOG['manual_workflows']:
                    response=await client.post('/api/task',json={'query':'请求执行'+item['label'],
                        'thread_id':'scope-retired-'+item['scenario_id'],
                        'product_context':{'profile':item['template_id']}})
                    self.assertEqual(response.status_code,410)
                    detail=response.json()['detail']
                    self.assertEqual(detail['code'],'manual_workflow')
                    self.assertEqual(detail['scenario_id'],item['scenario_id'])
                    self.assertTrue(detail['manual_steps'])
            agent.assert_not_awaited();rules.assert_not_awaited();create.assert_not_called()



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


    def test_public_catalog_has_only_active_entries_and_named_manual_workflows(self):
        self.assertEqual(len(CATALOG['templates']),19)
        self.assertEqual({m['scenario_id'] for m in CATALOG['manual_workflows']},{'S12','S14','S16'})
        self.assertFalse({m['template_id'] for m in CATALOG['manual_workflows']} & {t['id'] for t in CATALOG['templates']})
