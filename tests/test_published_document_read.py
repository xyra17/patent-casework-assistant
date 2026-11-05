import asyncio,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from api import context,evidence,source_policy
from tools.knowledge_base_tool import read_knowledge_document
from tools.knowledge_versions import VersionInfo,SNAPSHOT

class PublishedRead(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        context.set_session('published-read-test',str(self.root));context.set_round(1);context.reset_tool_budget()
        source_policy.configure(['knowledge']);evidence.reset('published-read-test')
        snap={'doc.md':{'revision':'a'*64,'info':VersionInfo().model_dump(mode='json')}}
        (self.root/SNAPSHOT).write_text(json.dumps(snap))
        chunks=[{'text':'正文'+str(n),'metadata':{'source':'doc.md','revision':'a'*64,'chunk_id':str(n),'line':n+1}} for n in range(2)]
        (self.root/'tanyuan-chunks.json').write_text(json.dumps(chunks))
        self.env=patch.dict(os.environ,{'VECTOR_STORE_DIR':str(self.root)});self.env.start()
    def tearDown(self):
        self.env.stop();context.reset_session();source_policy.reset_policy();evidence.clear('published-read-test');self.tmp.cleanup()
    def read(self,**kwargs):return asyncio.run(read_knowledge_document.ainvoke({'source':'doc.md',**kwargs}))
    def test_published_pages_have_explicit_coverage_and_keep_version_uncertain(self):
        text=self.read(limit=1);self.assertIn('"has_more": true',text);self.assertNotIn('正文1',text)
        text=self.read(offset=1);self.assertIn('正文1',text);self.assertIn('"complete_published_text": false',text)
        text=self.read();self.assertIn('"complete_published_text": true',text);self.assertIn('"applicability_verified": false',text)
        self.assertTrue(all(e['revision']=='a'*64 for e in evidence.snapshot('published-read-test')))
    def test_unknown_paths_and_revision_mismatch_cannot_read_disk(self):
        self.assertIn('未发布',self.read(source='../secret.md'))
        p=self.root/'tanyuan-chunks.json';chunks=json.loads(p.read_text());chunks[0]['metadata']['revision']='b'*64;p.write_text(json.dumps(chunks))
        self.assertIn('不一致',self.read());self.assertEqual(evidence.snapshot('published-read-test'),[])
    def test_permission_and_shared_budget_are_enforced(self):
        source_policy.configure([],['knowledge']);self.assertIn('禁止执行',self.read())
        source_policy.configure(['knowledge'])
        for _ in range(6):self.read()
        self.assertIn('预算已耗尽',self.read())
