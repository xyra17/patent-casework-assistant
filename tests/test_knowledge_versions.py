"""Version selection and real dual-channel integration; no production model/database required."""
import asyncio
import json
import unittest
from datetime import date
from unittest.mock import patch

from pydantic import ValidationError
import test_knowledge_management as fixtures
from tools import knowledge_versions as kv, knowledge_base_tool as kb
from scripts import build_knowledge_base as builder


class SelectionTests(unittest.TestCase):
    def entry(self, version='A', start='2020-01-01', **kwargs):
        return {'revision':'a'*64, 'info':kv.VersionInfo(document_id='采购', version_label=version,
                kind='regulation', confirmed=True, effective_from=start, **kwargs).model_dump(mode='json')}

    def scope(self, **kwargs):
        return kv.RetrievalScope(as_of='2025-01-01', **kwargs).resolved()

    def test_dates_scopes_future_and_history(self):
        snapshot={'旧':self.entry(effective_until='2024-01-01'),
                  '新':self.entry('B','2024-01-01',organizations=['甲']),
                  '未来':self.entry('C','2026-01-01')}
        selected,_,notices=kv.select_versions(snapshot,self.scope())
        self.assertEqual(selected,set())
        self.assertTrue(any('organization' in n for n in notices))
        self.assertEqual(kv.select_versions(snapshot,self.scope(organization='甲'))[0],{'新'})
        history=kv.RetrievalScope(purpose='history',as_of='2023-01-01').resolved()
        self.assertEqual(kv.select_versions(snapshot,history)[0],{'旧'})

    def test_replacement_partial_and_cycle(self):
        relation={'kind':'replaces','target':'旧','target_revision':'a'*64,'basis':'依据'}
        snapshot={'旧':self.entry(), '新':self.entry('B',relations=[relation])}
        selected,checks,_=kv.select_versions(snapshot,self.scope())
        self.assertEqual(selected,{'新'})
        self.assertTrue(checks['新']['verified'])
        self.assertEqual(checks['新']['relations'][0]['target'],'旧')
        snapshot['新']['info']['relations'][0].update(kind='amends',source_clause='第2条',target_clause='第8条')
        selected,checks,_=kv.select_versions(snapshot,self.scope())
        self.assertEqual(selected,{'新','旧'})
        self.assertFalse(checks['新']['verified'])
        self.assertTrue(any('局部修订' in w for w in checks['旧']['warnings']))
        snapshot['新']['info']['relations'][0]['kind']='replaces'
        snapshot['旧']['info']['relations']=[{**relation,'target':'新'}]
        selected,checks,_=kv.select_versions(snapshot,self.scope())
        self.assertEqual(selected,{'新','旧'})
        self.assertTrue(all(not checks[n]['verified'] for n in selected))

    def test_source_filter_does_not_bypass_replacement_or_resurrect_old_rule(self):
        rel={'kind':'replaces','target':'旧','target_revision':'a'*64,'basis':'替代'}
        snapshot={'旧':self.entry(), '新':self.entry('B','2024-01-01',relations=[rel])}
        self.assertEqual(kv.select_versions(snapshot,self.scope(sources=['旧']))[0],set())
        snapshot['新']['info']['effective_until']='2024-12-31'
        self.assertEqual(kv.select_versions(snapshot,self.scope())[0],set())

    def test_parallel_and_explicit_comparison(self):
        snapshot={'A':self.entry(), 'B':self.entry('B','2026-01-01')}
        comparison=kv.RetrievalScope(purpose='compare',sources=['A','B']).resolved()
        self.assertEqual(kv.select_versions(snapshot,comparison)[0],{'A','B'})
        snapshot['B']['info']['effective_from']='2024-01-01'
        _,checks,_=kv.select_versions(snapshot,self.scope())
        self.assertFalse(checks['A']['verified'])
        for payload in ({'purpose':'history'},{'purpose':'compare','sources':['A','A']}):
            with self.assertRaises(ValidationError): kv.RetrievalScope(**payload)
        with self.assertRaises(ValidationError):
            kv.VersionInfo(kind='regulation',confirmed=True)


class CitationTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_answer_keeps_version_uncertainty_separate_from_quote(self):
        from api.query_result import checked_projection, payload
        item={'id':'S1','type':'knowledge','applicability':{'verified':False,'purpose':'compare',
              'warnings':['存在局部修订'],'version':{'version_label':'A'}}}
        result=await checked_projection(payload('审批额度五万元',['S1'],'verified_quote'),[item])
        self.assertEqual(result['content'],'审批额度五万元')
        self.assertEqual(result['knowledge_applicability'][0]['purpose'],'compare')
        self.assertFalse(result['knowledge_applicability'][0]['verified'])


class VersionIntegrationTests(unittest.TestCase):
    def setUp(self):
        fixtures.KnowledgeTests.setUp(self)
        self.model.embed_documents = lambda texts: [[1.0, 0.0, 0.0] for text in texts]
    tearDown=fixtures.KnowledgeTests.tearDown
    upload=fixtures.KnowledgeTests.upload
    rebuild=fixtures.KnowledgeTests.rebuild

    def save_info(self, name, **kwargs):
        row=next(d for d in self.client.get('/api/knowledge').json()['documents'] if d['name']==name)
        info={**row['version_info'],**kwargs}
        return self.client.put('/api/knowledge/documents/version',params={'name':name},json={
            'expected_revision':row['revision'],'metadata_token':row['metadata_token'],'info':info})

    def policy(self, label, start, **kwargs):
        return dict(document_id='采购',version_label=label,kind='regulation',confirmed=True,
                    effective_from=start,**kwargs)

    def setup_versions(self):
        self.upload('旧规.txt','采购审批旧规金额五万元。')
        self.upload('新规.txt','采购审批新规金额八万元。本规定整体替代旧规。')
        self.assertEqual(self.save_info('旧规.txt',**self.policy('旧','2020-01-01',effective_until='2024-01-01')).status_code,200)
        self.assertEqual(self.save_info('新规.txt',**self.policy('新','2024-01-01')).status_code,200)
        self.assertEqual(self.rebuild()['job']['state'],'succeeded')

    def test_real_filtered_channels_history_comparison_and_pending_publication(self):
        self.setup_versions()
        scope=kv.RetrievalScope(as_of='2025-01-01')
        current=kb._retrieve_sync('采购审批',4,self.index,scope)
        self.assertEqual({r['metadata']['source'] for r in current},{'新规.txt'})
        self.assertIn('bm25',current[0]['channels'])
        self.assertIn('vector',current[0]['channels'])
        history=kb._retrieve_sync('采购审批',4,self.index,kv.RetrievalScope(purpose='history',as_of='2023-01-01'))
        self.assertEqual({r['metadata']['source'] for r in history},{'旧规.txt'})
        comparison=kb._retrieve_sync('采购审批',4,self.index,kv.RetrievalScope(purpose='compare',sources=['旧规.txt','新规.txt']))
        self.assertEqual({r['metadata']['source'] for r in comparison},{'旧规.txt','新规.txt'})
        self.assertEqual(self.save_info('新规.txt',confirmed=False).status_code,200)
        self.assertTrue(self.client.get('/api/knowledge').json()['needs_update'])
        self.assertTrue(kb._retrieve_sync('采购审批',4,self.index,scope)[0]['applicability']['verified'])
        self.assertEqual(self.rebuild()['job']['state'],'succeeded')
        self.assertEqual(kb._retrieve_sync('采购审批',4,self.index,scope),[])

    def test_comparison_candidate_pools_share_the_tool_budget(self):
        self.setup_versions()
        audit=[]
        records=kb._retrieve_sync('采购审批',4,self.index,
            kv.RetrievalScope(purpose='compare',sources=['旧规.txt','新规.txt']),audit,lambda:False)
        self.assertEqual({r['metadata']['source'] for r in records},{'旧规.txt'})
        self.assertTrue(any('预算耗尽' in issue for issue in audit))

    def test_confirmation_hash_binding_and_optimistic_concurrency(self):
        self.setup_versions()
        self.assertEqual(self.upload('替换.txt','覆盖',replace='旧规.txt').status_code,409)
        row=self.client.get('/api/knowledge').json()['documents'][0]
        body={'expected_revision':row['revision'],'metadata_token':row['metadata_token'],'info':row['version_info']}
        self.assertEqual(self.save_info(row['name'],scope_note='更新').status_code,200)
        self.assertEqual(self.client.put('/api/knowledge/documents/version',params={'name':row['name']},json=body).status_code,409)
        (self.source / '新规.txt').write_text('采购审批内容被外部改写')
        new=next(d for d in self.client.get('/api/knowledge').json()['documents'] if d['name']=='新规.txt')
        self.assertFalse(new['version_info']['confirmed'])
        self.assertEqual(self.rebuild()['job']['state'],'succeeded')
        self.assertEqual(kb._retrieve_sync('采购审批',4,self.index,kv.RetrievalScope(as_of='2025-01-01')),[])

    def test_relation_basis_binding_partial_warnings_and_snapshot_rollback(self):
        self.setup_versions()
        old_revision=kv.file_revision(self.source/'旧规.txt')
        relation={'kind':'amends','target':'旧规.txt','target_revision':old_revision,
                  'basis':'不存在的原文','source_clause':'新规金额','target_clause':'旧规金额'}
        self.assertEqual(self.save_info('新规.txt',relations=[relation]).status_code,400)
        relation['basis']='本规定整体替代旧规'
        # Keep both in date range to exercise conflict/partial amendment handling.
        self.assertEqual(self.save_info('旧规.txt',effective_until=None).status_code,200)
        self.assertEqual(self.save_info('新规.txt',relations=[relation]).status_code,200)
        self.assertEqual(self.rebuild()['job']['state'],'succeeded')
        records=kb._retrieve_sync('采购审批',4,self.index,kv.RetrievalScope(as_of='2025-01-01'))
        self.assertTrue(all(not r['applicability']['verified'] for r in records))
        self.assertTrue(any('局部修订' in w for r in records for w in r['applicability']['warnings']))
        published=(self.index/kv.SNAPSHOT).read_bytes()
        self.save_info('新规.txt',confirmed=False)
        self.upload('坏.pdf','not pdf')
        self.assertEqual(self.rebuild()['job']['state'],'failed')
        self.assertEqual((self.index/kv.SNAPSHOT).read_bytes(),published)

    def test_tool_preserves_unverified_applicability_and_validates_context(self):
        self.setup_versions()
        self.save_info('旧规.txt',effective_until=None)
        self.rebuild()
        async def review(*args):
            records=args[1]
            return {'sufficient':True,'supporting_ids':[r['evidence_id'] for r in records], 'missing':[]}
        # Use the real controller with a deterministic coverage model; scope checks must override it.
        from tools.adaptive_retrieval import adaptive_search
        async def controlled(*args,**kwargs):
            return await adaptive_search(*args,review=review,**kwargs)
        with patch.object(kb,'adaptive_search',side_effect=controlled):
            result=asyncio.run(kb.search_knowledge_base.ainvoke({'query':'采购审批','mode':'multi','as_of':'2025-01-01'}))
        summary=json.loads(result.split('\n\n')[0].removeprefix('检索状态：'))
        self.assertFalse(summary['coverage_verified'])
        self.assertEqual(summary['stop_reason'],'applicability_unverified')
        invalid=asyncio.run(kb.search_knowledge_base.ainvoke({'query':'采购审批','purpose':'history'}))
        self.assertIn('待补充',invalid)
        self.assertEqual(self.client.post('/api/knowledge/search',json={'query':'采购','purpose':'compare','sources':['旧规.txt']}).status_code,422)
