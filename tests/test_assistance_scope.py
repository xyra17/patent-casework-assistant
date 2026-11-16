"""Assistance route and positive/negative evidence checks; no paid model calls."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from api import storage, server, evidence
from casework import archive
from utils import path_utils
from tools.product_boundary import request_contract, scope, prompt
from tools.verified_delivery import partial_delivery

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT/'config/session-examples.json').read_text())
PROFILES = ('document-comparison','disclosure-search','public-reference')


class AssistanceScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_entries_start_normal_tasks_with_exact_sources_and_uploaded_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            with (patch.object(storage,'_DB_PATH',base/'app.db'),
                  patch.object(path_utils,'OUTPUT_DIR',base/'output'),
                  patch.object(path_utils,'UPLOAD_DIR',base/'uploads'),
                  patch.dict(os.environ,{'DATA_MODE':'demo','CASEWORK_DB_PATH':str(base/'cases.db')}),
                  patch.object(server.rule_engine,'resolve',AsyncMock(return_value={'applied':[],'conflicts':[],'overridden':[]})),
                  patch.object(server,'_run_task',AsyncMock()) as execute):
                storage.init_db()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
                    for profile in PROFILES:
                        template=next(t for t in CATALOG['templates'] if t['id']==profile)
                        thread='assistance-'+profile
                        names=['old.md','new.md'] if profile=='document-comparison' else ['交底.md'] if profile=='disclosure-search' else []
                        if names:
                            uploaded=await client.post('/api/upload',data={'thread_id':thread},files=[('files',(n,'独立模拟技术材料'.encode(),'text/markdown')) for n in names])
                            self.assertEqual(uploaded.status_code,200,uploaded.text)
                        response=await client.post('/api/task',json={'thread_id':thread,'query':template['value'],
                            'allowed_sources':template['allowed_sources'],'product_context':{'profile':profile,'ordered_files':names}})
                        self.assertEqual(response.status_code,200,response.text)
                        self.assertEqual(response.json()['status'],'started')
                        self.assertEqual(response.json()['allowed_sources'],template['allowed_sources'])
                        await asyncio.sleep(0)
                        run=storage.get_run(thread)
                        contract=next(e['data'] for e in run['events'] if e['event']=='product_boundary_request')
                        self.assertEqual(contract['profile'],profile)
                        self.assertEqual(contract['ordered_files'],names)
                        self.assertEqual(contract['output_policy'],'deliver_supported_parts_with_explicit_gaps')
                        with scope(contract):
                            self.assertIn('先交付',prompt())
                        evidence.clear(thread)
                self.assertEqual(execute.await_count,3)
                await asyncio.sleep(0)


class KnowledgeAvailabilityTests(unittest.TestCase):
    def status(self,root_state,company_state='empty'):
        with (patch.object(archive.catalog,'documents',return_value=[{'state':'parsed'}]),
              patch.object(archive.store,'list_cases',return_value=[{}]),
              patch.object(archive.store,'roots',return_value=[{'id':'root','label':'测试目录','state':root_state}])):
            return archive.knowledge_status.__wrapped__({'ok':company_state=='ok','state':company_state,'detail':'测试索引状态'})

    def test_empty_company_library_does_not_hide_ready_case_archive(self):
        value=self.status('ready')
        self.assertTrue(value['ok'])
        self.assertEqual(value['state'],'ok')
        self.assertIn('公司文档未接入',value['detail'])
        self.assertEqual(value['issues'],[])

    def test_missing_originals_explain_partial_availability_without_clearing_records(self):
        value=self.status('needs_confirmation')
        self.assertFalse(value['ok'])
        self.assertEqual(value['case_documents'],1)
        self.assertEqual(value['issues'][0]['root_id'],'root')
        self.assertIn('源目录大量文件缺失',value['detail'])
        self.assertIn('保留原有解析记录',value['detail'])

    def test_company_index_error_is_distinct_from_missing_case_originals(self):
        value=self.status('ready','error')
        self.assertEqual(value['state'],'partial')
        self.assertIn('公司文档索引不可用',value['detail'])
        self.assertNotIn('文件缺失',value['detail'])


class SupportedPartialAnswerTests(unittest.TestCase):
    def test_literal_result_survives_missing_professional_conclusion(self):
        items=[{'id':'S1','type':'file','title':'基准.md','revision':'a'*64,'location':'第3行','snippet':'缓存保留七天'},
               {'id':'S2','type':'file','title':'修订.md','revision':'b'*64,'location':'第3行','snippet':'权限撤销后立即失效'}]
        draft='“缓存保留七天”[S1]；“权限撤销后立即失效”[S2]。因此新机制完全替代旧机制。'
        text,audit=partial_delivery(draft,items)
        self.assertIn('缓存保留七天',text)
        self.assertIn('权限撤销后立即失效',text)
        self.assertNotIn('完全替代',text)
        self.assertEqual(len(audit['published_claim_ids']),2)

    def test_missing_or_wrong_citation_is_not_released_as_supported_text(self):
        item={'id':'S2','type':'file','title':'修订.md','location':'第3行','snippet':'权限撤销后立即失效'}
        for draft in ['“缓存保留七天”[S2]','“权限撤销后立即失效”[S9]']:
            text,audit=partial_delivery(draft,[item])
            self.assertFalse(audit['published_claim_ids'])
            self.assertIn('尚未取得',text)

    def test_short_fragment_cannot_misquote_a_larger_number(self):
        source={'id':'S1','type':'file','title':'原件.md','location':'第1行','snippet':'缓存保留17天'}
        text,audit=partial_delivery('“7”[S1]',[source])
        self.assertFalse(audit['published_claim_ids'])
        self.assertNotIn('> 7',text)


class ComparisonStatementTests(unittest.TestCase):
    def issues(self,text):
        from tools.reflect_check import hard_check
        return [item for item in hard_check({'query':'比较两侧原句，能否证明机制替换？','report':text,'evidence':[],
                'called_sources':[],'required_sources':[]}) if item.startswith('文本比较越界')]

    def test_local_uncertainty_does_not_assert_mechanism_replacement(self):
        for text in ['不足以单独认定机制被替换。','不能单独证明机制由A改为B。','不能确定机制是否替换。']:
            self.assertFalse(self.issues(text),text)

    def test_affirmative_mechanism_claim_stays_blocked_after_a_disclaimer(self):
        for text in ['机制由A改为B。','不能确定性能，但机制由A改为B。']:
            self.assertTrue(self.issues(text),text)


class AssistanceSourceBindingTests(unittest.TestCase):
    def test_source_omissions_trigger_repair_before_task_can_be_released(self):
        from tools.product_boundary import source_binding_issues
        items=[{'id':'S1','type':'file','title':'基准.md','revision':'a','location':'第1行','snippet':'基准原文'},
               {'id':'S2','type':'file','title':'修订.md','revision':'b','location':'第1行','snippet':'修订原文'}]
        contract=request_contract('比较两侧原句',{'profile':'document-comparison','ordered_files':['基准.md','修订.md']})
        self.assertTrue(source_binding_issues(contract,'基准原文与修订原文',items))
        self.assertTrue(source_binding_issues(contract,'基准原文[S1]',items))
        self.assertFalse(source_binding_issues(contract,'基准原文[S1]；修订原文[S2]',items))
        from tools.reflect_check import hard_check
        with scope(contract):
            self.assertTrue(any('双侧文字对照' in issue for issue in hard_check({'report':'基准原文[S1]','evidence':items})))

    def test_disclosure_requires_both_uploaded_and_case_bound_sources(self):
        from tools.product_boundary import source_binding_issues
        contract=request_contract('找参考',{'profile':'disclosure-search'})
        items=[{'id':'S1','type':'file','title':'交底.md','revision':'a','location':'第1行','snippet':'新交底原文'},
               {'id':'S2','type':'knowledge','title':'历史正文','revision':'b','location':'第2页','snippet':'旧案原文','case_id':'SIM-001'}]
        self.assertTrue(source_binding_issues(contract,'旧案原文[S2]',items))
        self.assertFalse(source_binding_issues(contract,'新交底原文[S1]；旧案原文[S2]',items))
