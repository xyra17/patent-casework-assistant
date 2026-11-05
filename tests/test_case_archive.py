"""Case folders keep fixed categories, exact counts and enabled-root boundaries."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from casework import archive, store

class CaseArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'CASEWORK_DB_PATH':str(Path(self.temp.name)/'archive.db')})
        self.env.start()
        with store.db(write=True) as conn:
            for root,enabled in [('one',1),('two',1),('disabled',0)]:
                conn.execute('INSERT INTO roots(id,label,path,classification,identity,enabled,created_at) VALUES(?,?,?,?,?,?,?)',
                             (root,root,'/'+root,'synthetic',root,enabled,'now'))
                data={'case_id':'AI-2026-001','title':'测试案件','owner_name':'甲','client_alias':'客户甲'}
                conn.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?,?)',
                             (root+':AI-2026-001',root,'AI-2026-001',json.dumps(data),'{}','[]','v',1))
            self.add(conn,'intake','one','AI-2026-001','AI-2026-001/案件信息.xlsx','intake')
            self.add(conn,'application','one','AI-2026-001','AI-2026-001/申请文件.pdf','application')
            self.add(conn,'bad','one','AI-2026-001','AI-2026-001/技术交底.docx','disclosure','error')
            self.add(conn,'drawing','one','AI-2026-001','AI-2026-001/附图.vsdx','drawing','inventory')
            self.add(conn,'old','one','AI-2026-001','AI-2026-001/旧申请.pdf','application',active=0)
            self.add(conn,'unknown','one',None,'待核对/材料.pdf','other')
            self.add(conn,'disabled-file','disabled','AI-2026-001','AI-2026-001/申请文件.pdf','application')
    def add(self,conn,ident,root,case,path,kind,state='parsed',active=1):
        conn.execute('INSERT INTO files(id,root_id,relative_path,case_id,kind,state,active,generation,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                     (ident,root,path,case,kind,state,active,1,'now'))
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_all_cases_have_identical_folders_and_zero_counts(self):
        result=archive.overview()
        self.assertEqual((result['case_count'],result['file_count'],result['unassigned_count']),(2,5,1))
        self.assertEqual([f['key'] for f in result['rows'][0]['folders']],[key for key,_ in archive.FOLDERS])
        self.assertEqual(result['rows'][0]['file_count'],4)
        self.assertEqual(result['rows'][1]['file_count'],0)
        self.assertTrue(all(f['count']==0 for f in result['rows'][1]['folders']))
        self.assertEqual(len(archive.unassigned()['documents']),1)
    def test_detail_preserves_failed_and_inventory_but_excludes_removed(self):
        detail=archive.detail('one:AI-2026-001')
        folders={f['key']:f for f in detail['folders']}
        self.assertEqual(folders['disclosure']['count'],2)
        self.assertEqual({d['state'] for d in folders['disclosure']['documents']},{'error','inventory'})
        self.assertEqual(folders['prosecution']['count'],0)
        self.assertEqual(sum(f['count'] for f in detail['folders']),4)
        with self.assertRaises(KeyError):archive.detail('disabled:AI-2026-001')
    def test_explicit_folder_and_filename_classification_do_not_use_case_title(self):
        self.assertEqual(archive.folder_key({'relative_path':'申请文件方法案/05_办理文件/scan.pdf','kind':'other'}),'prosecution')
        self.assertEqual(archive.folder_key({'relative_path':'申请文件方法案/unknown.pdf','kind':'other'}),'other')
        self.assertEqual(archive.folder_key({'relative_path':'案号/审查意见答复.docx','kind':'other'}),'prosecution')
        self.assertEqual(archive.folder_key({'relative_path':'案号/04_申请文件/附图.pdf','kind':'drawing'}),'application')
    def test_search_pagination_and_api_keep_scope(self):
        self.assertEqual(archive.overview('客户甲',1,1)['rows'][0]['case_key'],'two:AI-2026-001')
        self.assertEqual(archive.overview('不存在')['total'],0)
        from fastapi.testclient import TestClient
        from api.server import app
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/casework/archive',params={'limit':0}).status_code,422)
            self.assertEqual(client.get('/api/casework/archive/disabled:AI-2026-001').status_code,404)
            self.assertEqual(client.get('/api/casework/archive/unassigned').json()['documents'][0]['id'],'unknown')
            result=client.get('/api/casework/archive/one:AI-2026-001')
            self.assertEqual(result.status_code,200)
            self.assertEqual(sum(f['count'] for f in result.json()['folders']),4)
