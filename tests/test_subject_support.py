"""Original-object admission, independently of catalog expansion and reranking."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from casework import store,sync
from casework.search import search
from casework.subject_support import assess


class SubjectSupportTests(unittest.TestCase):
    def test_generic_similarity_does_not_replace_an_unknown_object(self):
        for query in ['陶瓷坯体烧结升温曲线','CRISPR基因编辑脱靶检测','蛋白质结晶缓冲液筛选','钙钛矿薄膜上的向量索引']:
            self.assertFalse(assess(query,'使用向量索引检索编辑记录，测量曲线及表面纹理。')['supported'],query)
        # A generic trailing term alone cannot establish the leading subject.
        result=assess('陶瓷升温曲线','测量温度曲线')
        self.assertEqual(result['coverage'],.5)
        self.assertFalse(result['supported'])

    def test_familiar_mechanisms_cannot_hide_an_absent_application_target(self):
        query='检索索引缓存工具用于钙钛矿薄膜'
        self.assertFalse(assess(query,'检索索引缓存工具整理历史资料。')['supported'])
        self.assertTrue(assess(query,'检索索引缓存工具用于钙钛矿薄膜实验记录。')['supported'])

    def test_prose_is_not_assigned_a_false_leading_subject(self):
        result=assess('希望先生成不同策略的少量候选，再保留未获支持的争议，查相关历史方案。','生成候选策略并保留未获支持的争议。')
        self.assertTrue(result['supported']);self.assertIsNone(result['anchor'])
        self.assertTrue(assess('参数在表里、解释在正文里，检查单位是不是同一个。','核对单位')['supported'])
        self.assertFalse(assess('CRISPR的检索与索引','检索与索引')['supported'])
        self.assertFalse(assess('CAT','concatenate records')['supported'])
        self.assertTrue(assess('HNSW近邻索引','HNSW graph 近邻索引')['supported'])


class SubjectSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.source=self.base/'source';self.source.mkdir()
        (self.source/'.casework-demo.json').write_text(json.dumps({'kind':'independently_authored_synthetic'}))
        self.env=patch.dict(os.environ,{'CASEWORK_DB_PATH':str(self.base/'catalog.db'),
            'CASEWORK_ALLOWED_ROOTS':json.dumps([str(self.base)]),'CASEWORK_EMBEDDING':'0',
            'CASEWORK_MYSQL_MIRROR':'0','CASEWORK_SYNC_INTERVAL':'0'})
        self.env.start()
        self.root=store.save_root('对象核对',str(self.source),'synthetic')
        self.add('DEMO-2099-001','使用向量索引检索历史段落并记录词频。')
        self.publish()

    def tearDown(self):self.env.stop();self.temp.cleanup()

    def add(self,case_id,text,source=None):
        folder=(source or self.source)/case_id;folder.mkdir(exist_ok=True)
        (folder/(case_id+'.txt')).write_text(text)

    def publish(self,root=None):
        job=sync.run((root or self.root)['id']);self.assertEqual(job['state'],'ready',job)

    def test_default_web_scope_and_every_fallback_obey_original_subject(self):
        from api.casework import search_archive
        for scope in ['ambiguous','mention','topic','mechanism']:
            with self.subTest(scope=scope):
                result=search('CRISPR基因编辑的向量索引',query_scope=scope)
                self.assertEqual(result['results'],[])
                self.assertEqual(result['subject_check']['rejected_cases'],1)
        result=search_archive(query='CRISPR基因编辑的向量索引',limit=8,root_id=None,domain=None,owner=None,kind=None)
        self.assertEqual(result['results'],[])
        self.assertEqual(result['query_scope'],'ambiguous')

    def test_new_domain_is_supported_after_sync_and_stale_revision_is_not(self):
        query='钙钛矿薄膜的光致发光量子产率'
        self.assertEqual(search(query)['results'],[])
        self.add('DEMO-2099-002','钙钛矿薄膜的光致发光量子产率由积分球测量，完成响应校准。');self.publish()
        result=search(query,query_scope='ambiguous')
        self.assertIn('DEMO-2099-002',[r['case_id'] for r in result['results']])
        self.add('DEMO-2099-002','仅记录项目进度，本版不含实验正文。');self.publish()
        self.assertEqual(search(query,query_scope='ambiguous')['results'],[])

    def test_case_number_lookup_keeps_negative_text_and_does_not_subject_filter(self):
        self.add('DEMO-2099-001','本方案未采用CRISPR基因编辑。使用向量索引检索历史段落。');self.publish()
        result=search('DEMO-2099-001 CRISPR基因编辑',query_scope='mention')
        self.assertTrue(result['results']);self.assertEqual(result['subject_check']['status'],'not_required')
        self.assertIn('未采用CRISPR',str(result['results']))

    def test_same_case_id_in_two_roots_cannot_borrow_subject_support(self):
        other=self.base/'other';other.mkdir()
        (other/'.casework-demo.json').write_text(json.dumps({'kind':'independently_authored_synthetic'}))
        root2=store.save_root('另一目录',str(other),'synthetic')
        self.add('DEMO-2099-001','钙钛矿薄膜采用积分球测量光致发光量子产率。',other);self.publish(root2)
        result=search('钙钛矿薄膜的光致发光量子产率',query_scope='ambiguous')
        self.assertEqual([r['case_key'] for r in result['results']],[root2['id']+':DEMO-2099-001'])
        store.save_root(root2['label'],root2['path'],'synthetic',ident=root2['id'],expected_version=root2['version'],enabled=False)
        self.assertEqual(search('钙钛矿薄膜的光致发光量子产率',query_scope='ambiguous')['results'],[])

    def test_subject_check_fault_is_not_disguised_as_a_correct_empty_answer(self):
        with patch('casework.subject_support.assess',side_effect=RuntimeError('tokenizer unavailable')):
            with self.assertRaisesRegex(RuntimeError,'tokenizer unavailable'):search('向量索引')

    def test_offline_native_channel_is_not_changed_by_production_gate(self):
        with patch('casework.subject_support.assess',side_effect=AssertionError('production only')):
            result=search('向量索引',_profile='bm25')
        self.assertTrue(result['results']);self.assertEqual(result['subject_check']['status'],'not_required')
