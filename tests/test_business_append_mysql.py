from tests.mysql_support import test_config
"""Opt-in append workflow tests against disposable MySQL schemas, never business rows."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import mysql.connector
_APP_TEST_DIR = tempfile.TemporaryDirectory(prefix='append-app-test-')
os.environ.setdefault('APP_DB_PATH', str(Path(_APP_TEST_DIR.name) / 'app.db'))
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import data_import as imports, business_edit as edit, business_database as browse
from scripts.setup_business_editor import AUDIT_DDL, ROOT

@unittest.skipUnless(os.getenv('RUN_MYSQL_APPEND_TESTS')=='1','真实MySQL追加验收需显式启用')
class AppendMySQLTests(unittest.TestCase):
    def setUp(self):
        cfg=test_config();self.database='append_test_'+uuid4().hex[:12];self.audit=self.database+'_audit'
        self.settings=dict(host=cfg.get('MYSQL_HOST','localhost'),port=int(cfg.get('MYSQL_PORT',3306)),user='root',password=cfg['MYSQL_DEV_ROOT_PASSWORD'],autocommit=True,use_pure=True)
        self.admin=mysql.connector.connect(**self.settings);cur=self.admin.cursor()
        cur.execute(f'CREATE DATABASE `{self.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci');cur.execute(f'CREATE DATABASE `{self.audit}`');cur.execute(f'USE `{self.audit}`');cur.execute(AUDIT_DDL);cur.execute(f'USE `{self.database}`')
        cur.execute('CREATE TABLE samples(id BIGINT AUTO_INCREMENT PRIMARY KEY,code VARCHAR(32) NOT NULL,label VARCHAR(40) NOT NULL,qty INT NOT NULL DEFAULT 0,amount DECIMAL(18,2),unique_code VARCHAR(32) UNIQUE,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB')
        cur.execute("INSERT INTO samples(code,label,qty) VALUES('A','Alpha',2),('CAFÉ','Cafe',1)")
        self.temp=tempfile.TemporaryDirectory(prefix='append-mysql-');rules=Path(self.temp.name)/'rules.json';rules.write_text(json.dumps({'samples':{'columns':['code'],'label':'业务编码'}}))
        self.env=patch.dict(os.environ,{'MYSQL_HOST':self.settings['host'],'MYSQL_PORT':str(self.settings['port']),'MYSQL_DATABASE':self.database,'MYSQL_USER':'root','MYSQL_PASSWORD':self.settings['password'],'MYSQL_IMPORT_USER':'root','MYSQL_IMPORT_PASSWORD':self.settings['password'],'MYSQL_EDIT_ENABLED':'1','MYSQL_EDIT_USER':'root','MYSQL_EDIT_PASSWORD':self.settings['password'],'MYSQL_EDIT_AUDIT_DATABASE':self.audit,'IMPORT_STORAGE_DIR':self.temp.name,'BUSINESS_IMPORT_RULES_FILE':str(rules)})
        self.env.start();app=FastAPI();app.include_router(browse.router);app.include_router(edit.router);app.include_router(imports.router);self.client=TestClient(app)
    def tearDown(self):
        self.client.close();self.env.stop();self.admin.cursor().execute(f'DROP DATABASE `{self.database}`');self.admin.cursor().execute(f'DROP DATABASE `{self.audit}`');self.admin.close();self.temp.cleanup()
    def stage(self,text):
        r=self.client.post('/api/data-import/upload',files={'file':('test.csv',text.encode())});self.assertEqual(r.status_code,200,r.text);return r.json()['id']
    def validate(self,ident,policy='block',mapping=None):
        r=self.client.post(f'/api/data-import/batches/{ident}/validate',json={'table':'samples','sheet':'CSV','mapping':mapping or {'code':'code','label':'label','qty':'qty'},'duplicate_policy':policy});self.assertEqual(r.status_code,200,r.text);return r.json()
    def commit(self,ident,preview):return self.client.post(f'/api/data-import/batches/{ident}/commit',json={'validation_token':preview['validation_token']})
    def create(self,code,**extra):
        return {'table':'samples','values':{'code':code,'label':code,**extra},'reason':'隔离库验收','confirmed':True,'operation_id':str(uuid4())}
    def count(self,code=None):
        cur=self.admin.cursor();cur.execute('SELECT COUNT(*) FROM samples'+(' WHERE code=%s' if code else ''),([code] if code else []));return cur.fetchone()[0]
    def test_preview_block_skip_file_duplicates_collation_and_replay(self):
        ident=self.stage('code,label,qty\nA,Alpha,2\nB,Bravo,3\nB,Bravo,3\nC,Charlie,4\ncafe,Cafe,1\n')
        blocked=self.validate(ident);self.assertFalse(blocked['valid']);self.assertEqual([blocked['dedupe'][k] for k in ['new','duplicate','conflict']],[2,3,0]);self.assertEqual(self.count(),2)
        preview=self.validate(ident,'skip');self.assertTrue(preview['valid']);self.assertEqual(len(preview['preview']),2)
        result=self.commit(ident,preview);self.assertEqual(result.status_code,200,result.text);self.assertEqual((result.json()['inserted_rows'],result.json()['skipped_rows']),(2,3));self.assertTrue(self.commit(ident,preview).json()['replayed']);self.assertEqual(self.count(),4)
    def test_conflicts_missing_identity_zero_new_and_changed_plan_never_write(self):
        ident=self.stage('code,label,qty\nA,Wrong,2\nQ,First,1\nq,Second,2\n');p=self.validate(ident,'skip');self.assertFalse(p['valid']);self.assertEqual(p['dedupe']['conflict'],2);self.assertEqual(self.commit(ident,p).status_code,409)
        ident=self.stage('code,label,qty\n,Missing,1\n');p=self.validate(ident,'skip');self.assertFalse(p['valid'])
        ident=self.stage('code,label,qty\nA,Alpha,2\n');p=self.validate(ident,'skip');self.assertFalse(p['valid']);self.assertEqual(p['insert_count'],0)
        ident=self.stage('code,label,qty\nR,R,0\nS,S,0\n');p=self.validate(ident,'skip');self.assertTrue(p['valid'])
        result=self.client.post('/api/business-database/create-row',json=self.create('R'));self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.commit(ident,p).status_code,409);self.assertEqual(self.count('S'),0)
        p=self.validate(ident,'skip');self.assertEqual(p['insert_count'],1);self.assertEqual(self.commit(ident,p).status_code,200)
    def test_creation_defaults_precision_audit_replay_rollback_and_route_separation(self):
        template=self.client.post('/api/business-database/row-template',json={'table':'samples'});self.assertEqual(template.status_code,200,template.text)
        columns={c['name']:c for c in template.json()['columns']};self.assertFalse(columns['id']['insertable']);self.assertTrue(columns['created_at']['has_default'])
        body=self.create('D',amount='900719925474099.01');r=self.client.post('/api/business-database/create-row',json=body);self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['values']['qty'],0);self.assertEqual(r.json()['values']['amount'],'900719925474099.01')
        self.assertTrue(self.client.post('/api/business-database/create-row',json=body).json()['replayed']);self.assertEqual(self.count('D'),1)
        self.assertEqual(self.client.post('/api/business-database/create-row',json={**body,'values':{'code':'other','label':'other'}}).status_code,409)
        self.assertEqual(self.client.post('/api/business-database/rows',json={'table':'samples'}).status_code,200)
        cur=self.admin.cursor();cur.execute(f'SELECT COUNT(*) FROM `{self.audit}`.row_audit WHERE operation=%s',('insert',));self.assertEqual(cur.fetchone()[0],1)
        fail=self.create('ROLLBACK')
        with patch.object(edit,'audit_table',return_value=f'`{self.audit}`.`missing_audit`'):
            self.assertEqual(self.client.post('/api/business-database/create-row',json=fail).status_code,503)
        self.assertEqual(self.count('ROLLBACK'),0)
        self.assertEqual(self.client.post('/api/business-database/create-row',json=fail).status_code,200)
        for changes in [{'confirmed':False},{'values':{'id':'10','code':'X','label':'X'}},{'values':{'code':'X'}},{'values':{'code':'X','label':'X','qty':'1.2'}}]:
            self.assertIn(self.client.post('/api/business-database/create-row',json={**self.create('X'),**changes}).status_code,[400,422])
        self.assertEqual(self.count('X'),0)
    def test_concurrent_creation_and_import_share_lock_without_unique_business_index(self):
        body=self.create('CONCURRENT')
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:self.client.post('/api/business-database/create-row',json=body),range(2)))
        self.assertEqual([r.status_code for r in results],[200,200]);self.assertEqual(self.count('CONCURRENT'),1)
        ident=self.stage('code,label,qty\nRACE,RACE,0\n');preview=self.validate(ident)
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(self.commit,ident,preview);b=pool.submit(self.client.post,'/api/business-database/create-row',json=self.create('RACE'));codes=sorted([a.result().status_code,b.result().status_code])
        self.assertEqual(codes,[200,409]);self.assertEqual(self.count('RACE'),1)
