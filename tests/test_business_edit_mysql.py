from tests.mysql_support import test_config
"""Opt-in real MySQL editor test, granting access only to disposable random schemas."""
import os
import json
import unittest
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import mysql.connector
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import business_edit as edit
from scripts.setup_business_editor import AUDIT_DDL,ROOT

@unittest.skipUnless(os.getenv('RUN_MYSQL_EDIT_TESTS')=='1','真实MySQL维护验收需显式启用')
class MySQLEditTests(unittest.TestCase):
    def test_transactions_associations_stale_versions_idempotency_and_audit(self):
        cfg=test_config();database='edit_test_'+uuid4().hex[:12];audit=database+'_audit'
        admin=mysql.connector.connect(host=cfg.get('MYSQL_HOST','localhost'),port=int(cfg.get('MYSQL_PORT',3306)),user='root',password=cfg['MYSQL_DEV_ROOT_PASSWORD'],autocommit=True,use_pure=True)
        cur=admin.cursor();user=cfg['MYSQL_EDIT_USER']
        try:
            cur.execute(f'CREATE DATABASE `{database}`');cur.execute(f'CREATE DATABASE `{audit}`');cur.execute(f'USE `{audit}`');cur.execute(AUDIT_DDL)
            cur.execute(f'USE `{database}`')
            cur.execute('CREATE TABLE parents(id BIGINT PRIMARY KEY,label VARCHAR(40) NOT NULL,amount DECIMAL(18,2),note VARCHAR(40),modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB')
            cur.execute('CREATE TABLE children(id INT PRIMARY KEY,parent_id BIGINT NOT NULL,quantity INT,FOREIGN KEY(parent_id) REFERENCES parents(id) ON DELETE CASCADE) ENGINE=InnoDB')
            cur.execute('CREATE TABLE no_key(label VARCHAR(40)) ENGINE=InnoDB')
            cur.execute("INSERT INTO parents(id,label,amount) VALUES(9007199254740993,'exact',10.00),(2,'race',1.00)")
            cur.execute('INSERT INTO children VALUES(1,9007199254740993,5)')
            cur.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON `{database}`.* TO %s@%s',(user,'%'));cur.execute(f'GRANT SELECT,INSERT ON `{audit}`.`row_audit` TO %s@%s',(user,'%'))
            env={'MYSQL_HOST':cfg.get('MYSQL_HOST','localhost'),'MYSQL_PORT':cfg.get('MYSQL_PORT','3306'),'MYSQL_DATABASE':database,'MYSQL_EDIT_ENABLED':'1','MYSQL_EDIT_USER':user,'MYSQL_EDIT_PASSWORD':cfg['MYSQL_EDIT_PASSWORD'],'MYSQL_EDIT_AUDIT_DATABASE':audit}
            app=FastAPI();app.include_router(edit.router)
            with patch.dict(os.environ,env):
                client=TestClient(app)
                def detail(table,key):
                    r=client.post('/api/business-database/row-detail',json={'table':table,'key':key});self.assertEqual(r.status_code,200,r.text);return r.json()
                record=detail('parents',{'id':'9007199254740993'});self.assertEqual(record['key']['id'],'9007199254740993');self.assertTrue(record['referenced_by'])
                body={'table':'parents','key':record['key'],'expected_version':record['version'],'changes':{'amount':'900719925474099.01','note':None},'reason':'精度验收','operation_id':str(uuid4()),'confirmed':True}
                good=client.patch('/api/business-database/row',json=body);self.assertEqual(good.status_code,200,good.text)
                replay=client.patch('/api/business-database/row',json=body);self.assertTrue(replay.json()['replayed'])
                conflict=client.patch('/api/business-database/row',json={**body,'changes':{'amount':'11.00'}});self.assertEqual(conflict.status_code,409)
                stale=client.patch('/api/business-database/row',json={**body,'operation_id':str(uuid4()),'changes':{'amount':'11.00'}});self.assertEqual(stale.status_code,409)
                fresh=detail('parents',record['key']);self.assertEqual(fresh['values']['amount'],'900719925474099.01')
                denied=client.post('/api/business-database/delete-row',json={**body,'expected_version':fresh['version'],'changes':{},'operation_id':str(uuid4())});self.assertEqual(denied.status_code,409,denied.text)
                child=detail('children',{'id':1});childbody={**body,'table':'children','key':{'id':1},'expected_version':child['version'],'changes':{'parent_id':'999'},'operation_id':str(uuid4())}
                self.assertEqual(client.patch('/api/business-database/row',json=childbody).status_code,422)
                cur.execute(f'REVOKE INSERT ON `{audit}`.`row_audit` FROM %s@%s',(user,'%'))
                failure=client.patch('/api/business-database/row',json={**body,'expected_version':fresh['version'],'changes':{'label':'must rollback'},'operation_id':str(uuid4())});self.assertEqual(failure.status_code,503)
                cur.execute('SELECT label FROM parents WHERE id=9007199254740993');self.assertEqual(cur.fetchone()[0],'exact')
                cur.execute(f'GRANT INSERT ON `{audit}`.`row_audit` TO %s@%s',(user,'%'))
                # Only one writer with a stale shared snapshot may commit.
                race=detail('parents',{'id':2});requests=[{**body,'key':{'id':2},'expected_version':race['version'],'changes':{'amount':str(v)},'operation_id':str(uuid4())} for v in (2,3)]
                with ThreadPoolExecutor(2) as pool:codes=list(pool.map(lambda b:client.patch('/api/business-database/row',json=b).status_code,requests))
                self.assertEqual(sorted(codes),[200,409])
                for table,key in [('children',{'id':1}),('parents',record['key'])]:
                    d=detail(table,key);deleted=client.post('/api/business-database/delete-row',json={**body,'table':table,'key':key,'expected_version':d['version'],'changes':{},'operation_id':str(uuid4())});self.assertEqual(deleted.status_code,200,deleted.text)
                history=client.get('/api/business-database/audit');self.assertEqual(history.status_code,200,history.text)
                records=history.json()['records'];self.assertEqual(len(records),4);self.assertTrue(all(r['actor']=='local_shared' for r in records));self.assertTrue(any(r['operation']=='delete' and r['after'] is None for r in records))
                no_key=client.post('/api/business-database/row-detail',json={'table':'no_key','key':{'label':'x'}});self.assertEqual(no_key.status_code,422)
        finally:
            for privilege,target in [('SELECT,INSERT,UPDATE,DELETE',f'`{database}`.*'),('SELECT,INSERT',f'`{audit}`.`row_audit`')]:
                try:cur.execute(f'REVOKE {privilege} ON {target} FROM %s@%s',(user,'%'))
                except mysql.connector.Error:pass
            cur.execute(f'DROP DATABASE IF EXISTS `{database}`');cur.execute(f'DROP DATABASE IF EXISTS `{audit}`');admin.close()
