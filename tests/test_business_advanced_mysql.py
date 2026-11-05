from tests.mysql_support import test_config
"""Real DB acceptance, strictly disposable random schemas; never alter original data."""
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
import mysql.connector
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import business_edit as edit,business_advanced as adv
from scripts.setup_business_editor import AUDIT_DDL,ROOT
from scripts.setup_business_console import SQL_AUDIT_DDL

@unittest.skipUnless(os.getenv('RUN_MYSQL_EDIT_TESTS')=='1','真实MySQL高级维护验收需显式启用')
class AdvancedMySQLTests(unittest.TestCase):
    def test_rename_associations_batch_atomicity_sql_and_idempotency(self):
        cfg=test_config();database='advanced_test_'+uuid4().hex[:10];audit=database+'_audit'
        admin=mysql.connector.connect(host=cfg.get('MYSQL_HOST','localhost'),port=int(cfg.get('MYSQL_PORT',3306)),user='root',password=cfg['MYSQL_DEV_ROOT_PASSWORD'],autocommit=True,use_pure=True)
        cur=admin.cursor();editor=cfg['MYSQL_EDIT_USER'];console=cfg['MYSQL_CONSOLE_USER']
        try:
            cur.execute(f'CREATE DATABASE `{database}`');cur.execute(f'CREATE DATABASE `{audit}`');cur.execute(f'USE `{audit}`');cur.execute(AUDIT_DDL);cur.execute(SQL_AUDIT_DDL)
            cur.execute(f'USE `{database}`')
            cur.execute('CREATE TABLE parents(id BIGINT PRIMARY KEY,label VARCHAR(40) NOT NULL,amount DECIMAL(18,2)) ENGINE=InnoDB')
            cur.execute('CREATE TABLE children(id INT PRIMARY KEY,parent_id BIGINT NOT NULL,FOREIGN KEY(parent_id) REFERENCES parents(id) ON DELETE CASCADE) ENGINE=InnoDB')
            cur.execute("INSERT INTO parents VALUES(1,'one',10.00),(2,'two',20.00),(3,'three',30.00)");cur.execute('INSERT INTO children VALUES(1,1),(2,1)')
            cur.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON `{database}`.* TO %s@%s',(editor,'%'));cur.execute(f'GRANT SELECT,INSERT ON `{audit}`.`row_audit` TO %s@%s',(editor,'%'))
            cur.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,DROP,INDEX,REFERENCES ON `{database}`.* TO %s@%s',(console,'%'));cur.execute(f'GRANT SELECT,INSERT,UPDATE ON `{audit}`.`sql_operations` TO %s@%s',(console,'%'))
            env={k:cfg[k] for k in ['MYSQL_HOST','MYSQL_PORT','MYSQL_EDIT_USER','MYSQL_EDIT_PASSWORD','MYSQL_CONSOLE_USER','MYSQL_CONSOLE_PASSWORD']};env.update(MYSQL_DATABASE=database,MYSQL_EDIT_ENABLED='1',MYSQL_CONSOLE_ENABLED='1',MYSQL_EDIT_AUDIT_DATABASE=audit)
            app=FastAPI();app.include_router(edit.router);app.include_router(adv.router)
            with patch.dict(os.environ,env):
                client=TestClient(app)
                def preview(body):
                    r=client.post('/api/business-database/advanced/preview',json=body);self.assertEqual(r.status_code,200,r.text);return r.json()
                def apply(body,p=None,ident=None):
                    p=p or preview(body);return client.post('/api/business-database/advanced/apply',json={**body,'token':p['token'],'operation_id':ident or str(uuid4()),'confirmed':True})
                rename={'kind':'rename_key','table':'parents','keys':[{'id':'1'}],'changes':{'id':'9007199254740993'},'reason':'主键关联迁移验收'}
                p=preview(rename);self.assertEqual(p['related_count'],2);ident=str(uuid4());r=apply(rename,p,ident);self.assertEqual(r.status_code,200,r.text)
                cur.execute('SELECT parent_id FROM children ORDER BY id');self.assertEqual(cur.fetchall(),[(9007199254740993,),(9007199254740993,)])
                self.assertTrue(apply(rename,p,ident).json()['replayed'])
                batch={'kind':'bulk_update','table':'parents','keys':[{'id':'2'},{'id':'3'}],'changes':{'amount':'123.45'},'reason':'批量验收'}
                p=preview(batch);cur.execute("UPDATE parents SET label='changed' WHERE id=3")
                stale=apply(batch,p);self.assertEqual(stale.status_code,409,stale.text)
                cur.execute('SELECT amount FROM parents WHERE id=2');self.assertEqual(str(cur.fetchone()[0]),'20.00')
                good=apply(batch);self.assertEqual(good.status_code,200,good.text)
                # Failed audit insert must roll back the entire batch.
                p=preview({**batch,'changes':{'amount':'999.99'}});cur.execute(f'REVOKE INSERT ON `{audit}`.`row_audit` FROM %s@%s',(editor,'%'))
                r=apply({**batch,'changes':{'amount':'999.99'}},p);self.assertEqual(r.status_code,503,r.text)
                cur.execute('SELECT amount FROM parents WHERE id IN (2,3)');self.assertEqual([str(row[0]) for row in cur.fetchall()],['123.45','123.45'])
                cur.execute(f'GRANT INSERT ON `{audit}`.`row_audit` TO %s@%s',(editor,'%'))
                denied=client.post('/api/business-database/advanced/preview',json={'kind':'bulk_delete','table':'parents','keys':[{'id':'2'},{'id':'9007199254740993'}],'reason':'关联阻止整批删除'});self.assertEqual(denied.status_code,409,denied.text)
                r=apply({**batch,'kind':'bulk_delete','changes':{}});self.assertEqual(r.status_code,200,r.text)
                def sql(statement):
                    b={'sql':statement,'reason':'SQL控制台验收'};p=client.post('/api/business-database/sql/preview',json=b);self.assertEqual(p.status_code,200,p.text)
                    body={**b,'token':p.json()['token'],'operation_id':str(uuid4()),'confirmed':True};r=client.post('/api/business-database/sql/execute',json=body);self.assertEqual(r.status_code,200,r.text);return r.json(),body
                sql('SHOW TABLES');sql('SELECT 1')
                for statement in ['CREATE TABLE sql_sample(id INT PRIMARY KEY,label VARCHAR(40)) ENGINE=InnoDB','INSERT INTO sql_sample VALUES(1,\'created\')','UPDATE sql_sample SET label=\'updated\' WHERE id=1','ALTER TABLE sql_sample ADD COLUMN quantity INT DEFAULT 0']:
                    result,body=sql(statement);replay=client.post('/api/business-database/sql/execute',json=body);self.assertTrue(replay.json()['replayed'],replay.text)
                result,_=sql('SELECT * FROM sql_sample');self.assertEqual(result['rows'],[[1,'updated',0]])
                # Duplicate result labels must remain distinct in the browser table.
                result,_=sql('SELECT id AS value,label AS value FROM sql_sample');self.assertEqual(result['columns'],['value','value [2]'])
                # Audit finalization failure cannot leave a successful DML write behind.
                body={'sql':"UPDATE sql_sample SET label='must rollback' WHERE id=1",'reason':'审计失败回滚'}
                p=client.post('/api/business-database/sql/preview',json=body).json();body.update(token=p['token'],operation_id=str(uuid4()),confirmed=True)
                cur.execute(f'REVOKE UPDATE ON `{audit}`.`sql_operations` FROM %s@%s',(console,'%'))
                bad=client.post('/api/business-database/sql/execute',json=body);self.assertEqual(bad.status_code,503,bad.text)
                cur.execute('SELECT label FROM sql_sample WHERE id=1');self.assertEqual(cur.fetchone()[0],'updated')
                cur.execute(f'GRANT UPDATE ON `{audit}`.`sql_operations` TO %s@%s',(console,'%'))
                self.assertEqual(client.post('/api/business-database/sql/execute',json=body).status_code,409)
                # An unsuccessful DDL stays uncertain and is never automatically executed again.
                body={'sql':'CREATE TABLE sql_sample(id INT)','reason':'重复建表失败状态'}
                p=client.post('/api/business-database/sql/preview',json=body).json();body.update(token=p['token'],operation_id=str(uuid4()),confirmed=True)
                bad=client.post('/api/business-database/sql/execute',json=body);self.assertEqual(bad.status_code,422,bad.text)
                self.assertEqual(client.post('/api/business-database/sql/execute',json=body).status_code,409)
                sql('DELETE FROM sql_sample WHERE id=1')
                sql('INSERT INTO sql_sample(id,label) VALUES '+','.join(f"({n},'row')" for n in range(1,602)))
                result,_=sql('SELECT * FROM sql_sample');self.assertEqual(len(result['rows']),500);self.assertTrue(result['truncated'])
                sql('TRUNCATE TABLE sql_sample');sql('DROP TABLE sql_sample')
                history=client.get('/api/business-database/sql/history');self.assertEqual(history.status_code,200,history.text);self.assertEqual(len(history.json()['records']),15);self.assertTrue(any(r['status']=='uncertain' for r in history.json()['records']));self.assertTrue(any(r['status']=='executing' for r in history.json()['records']))
                # Reader still cannot write, console cannot read audit credentials or modify server accounts.
                conn=adv.console_connect()
                try:
                    with self.assertRaises(mysql.connector.Error):conn.cursor().execute('SELECT * FROM mysql.user')
                finally:conn.close()
        finally:
            for user,priv,target in [(editor,'SELECT,INSERT,UPDATE,DELETE',f'`{database}`.*'),(editor,'SELECT,INSERT',f'`{audit}`.`row_audit`'),(console,'SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,DROP,INDEX,REFERENCES',f'`{database}`.*'),(console,'SELECT,INSERT,UPDATE',f'`{audit}`.`sql_operations`')]:
                try:cur.execute(f'REVOKE {priv} ON {target} FROM %s@%s',(user,'%'))
                except mysql.connector.Error:pass
            cur.execute(f'DROP DATABASE IF EXISTS `{database}`');cur.execute(f'DROP DATABASE IF EXISTS `{audit}`');admin.close()
