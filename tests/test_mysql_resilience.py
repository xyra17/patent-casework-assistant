"""Real projection failures: rollback, stale-read denial and explicit recovery."""
import os
import socket
import unittest
from unittest.mock import patch
from tests.mysql_support import test_config


@unittest.skipUnless(os.getenv('RUN_CASEWORK_MYSQL_TESTS')=='1','isolated real MySQL opt-in')
class MySQLResilienceTests(unittest.TestCase):
    def setUp(self):
        import mysql.connector
        from casework import store
        self.cfg=test_config()
        self.admin=mysql.connector.connect(host=self.cfg['MYSQL_HOST'],port=int(self.cfg['MYSQL_PORT']),
            user='root',password=self.cfg['MYSQL_DEV_ROOT_PASSWORD'],database=self.cfg['MYSQL_DATABASE'],
            autocommit=True,use_pure=True)
        self.root=store.roots(enabled=True)[0]['id']
        self.before=self.snapshot()

    def tearDown(self):self.admin.close()

    def snapshot(self):
        cur=self.admin.cursor();out=[]
        for table,key in [('cw_cases','case_key'),('cw_documents','document_id'),('cw_sync_state','root_id')]:
            if not hasattr(self,'_columns'):self._columns={}
            if table not in self._columns:
                cur.execute('SELECT * FROM '+table+' LIMIT 0');self._columns[table]=[c[0] for c in cur.description];cur.fetchall()
            cur.execute('SELECT '+','.join(self._columns[table])+' FROM '+table+' ORDER BY '+key);out.append(cur.fetchall())
        return out

    def verify_failed_and_recover(self, failure):
        from casework import mirror,store
        self.assertEqual(failure['state'],'error')
        self.assertEqual(self.snapshot(),self.before,'A failed projection must preserve every previously committed row')
        self.assertEqual(store.get_root(self.root)['mirror_state'],'error')
        with self.assertRaises(PermissionError):mirror.check_read('SELECT * FROM cw_cases')

    def recover(self):
        from casework import mirror
        self.assertEqual(mirror.sync_mirror(self.root)['state'],'ready')
        mirror.check_read('SELECT * FROM cw_cases')
        self.assertEqual(self.snapshot(),self.before)

    def test_schema_drift_after_deletes_rolls_back_full_projection(self):
        from casework import mirror
        cur=self.admin.cursor();cur.execute('ALTER TABLE cw_documents ADD COLUMN required_new_field INT NOT NULL')
        try:self.verify_failed_and_recover(mirror.sync_mirror(self.root))
        finally:cur.execute('ALTER TABLE cw_documents DROP COLUMN required_new_field')
        self.recover()

    def test_connection_refusal_blocks_stale_projection_and_can_recover(self):
        from casework import mirror
        with socket.socket() as unused:
            unused.bind(('127.0.0.1',0))
            with patch.dict(os.environ,{'MYSQL_PORT':str(unused.getsockname()[1])}):
                self.verify_failed_and_recover(mirror.sync_mirror(self.root))
        self.recover()

    def test_revoked_insert_rolls_back_deletes_then_recovers(self):
        from casework import mirror
        cur=self.admin.cursor();database=self.cfg['MYSQL_DATABASE'];user=self.cfg['MYSQL_CASEWORK_SYNC_USER']
        cur.execute('REVOKE INSERT ON `'+database+'`.cw_cases FROM %s@%s',(user,'%'))
        try:self.verify_failed_and_recover(mirror.sync_mirror(self.root))
        finally:cur.execute('GRANT INSERT ON `'+database+'`.cw_cases TO %s@%s',(user,'%'))
        self.recover()

    def test_nontransactional_engine_cannot_enter_mutation_workflow(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api import business_edit
        from uuid import uuid4
        cur=self.admin.cursor();cur.execute('CREATE TABLE nontransactional_probe(id INT PRIMARY KEY,label VARCHAR(30)) ENGINE=MyISAM')
        app=FastAPI();app.include_router(business_edit.router)
        try:
            with patch.dict(os.environ,{'MYSQL_EDIT_ENABLED':'1'}),TestClient(app) as client:
                response=client.post('/api/business-database/create-row',json={'table':'nontransactional_probe',
                    'values':{'id':'1','label':'must not write'},'reason':'引擎边界注入','confirmed':True,'operation_id':str(uuid4())})
                self.assertEqual(response.status_code,422,response.text)
                self.assertIn('InnoDB',response.json()['detail'])
            cur.execute('SELECT COUNT(*) FROM nontransactional_probe');self.assertEqual(cur.fetchone()[0],0)
        finally:cur.execute('DROP TABLE nontransactional_probe')
