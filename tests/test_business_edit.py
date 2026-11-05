"""Maintenance unit boundaries; transactions are also exercised against isolated real MySQL."""
import os
import tempfile
from pathlib import Path
os.environ.setdefault('APP_DB_PATH',str(Path(tempfile.mkdtemp(prefix='edit-tests-'))/'app.db'))
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4
from decimal import Decimal
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import business_edit as edit

COLS=[{'name':'id','type':'bigint','nullable':False,'key':'PRI','editable':False},
      {'name':'amount','type':'decimal(10,2)','nullable':False,'key':'','editable':True}]

class EditBoundaryTests(unittest.TestCase):
    def setUp(self):
        app=FastAPI();app.include_router(edit.router);self.client=TestClient(app)
        self.row={'id':1,'amount':Decimal('10.00')};self.conn=Mock();self.cur=self.conn.cursor.return_value
        self.cur.rowcount=1
        self.patches=[patch.object(edit,'connect',return_value=self.conn),patch.object(edit,'schema',return_value=COLS),patch.object(edit,'read_row',return_value=(self.row,'`id`=%s',[1])),patch.object(edit,'previous',return_value=None),patch.object(edit,'validate_foreign_keys'),patch.object(edit,'references',return_value=[])]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
    def body(self,**changes):return {'table':'samples','key':{'id':1},'expected_version':edit.version(self.row),'changes':{'amount':'12.00'},'reason':'更正金额','operation_id':str(uuid4()),'confirmed':True,**changes}
    def test_update_and_audit_commit_once_and_preserve_exact_amount(self):
        response=self.client.patch('/api/business-database/row',json=self.body())
        self.assertEqual(response.status_code,200,response.text);self.conn.commit.assert_called_once()
        calls=self.cur.execute.call_args_list;self.assertTrue(calls[0].args[0].startswith('UPDATE'))
        self.assertEqual(calls[0].args[1],('12.00',1));self.assertIn('INSERT INTO',calls[1].args[0])
        self.assertIn('10.00',calls[1].args[1][-3]);self.conn.close.assert_called_once()
    def test_stale_revision_confirmation_and_primary_key_mutation_are_rejected(self):
        for change,status in [({'confirmed':False},400),({'expected_version':'0'*64},409),({'changes':{'id':'2'}},422),({'changes':{'unknown':'1'}},422),({'changes':{'amount':'1.234'}},422)]:
            r=self.client.patch('/api/business-database/row',json=self.body(**change));self.assertEqual(r.status_code,status,r.text)
        self.conn.commit.assert_not_called()
    def test_audit_failure_rolls_back_business_update(self):
        self.cur.execute.side_effect=[None,RuntimeError('audit unavailable')]
        r=self.client.patch('/api/business-database/row',json=self.body());self.assertEqual(r.status_code,503)
        self.conn.rollback.assert_called_once();self.conn.commit.assert_not_called()
    def test_delete_references_block_even_when_database_has_cascade(self):
        with patch.object(edit,'references',return_value=[{'table':'children','fields':['sample_id']}]):
            r=self.client.post('/api/business-database/delete-row',json=self.body(changes={}))
        self.assertEqual(r.status_code,409);self.assertIn('级联',r.text);self.cur.execute.assert_not_called();self.conn.rollback.assert_called_once()
    def test_referenced_unique_field_cannot_cascade_update_silently(self):
        with patch.object(edit,'references',return_value=[{'table':'children','referenced_fields':['amount']}]):
            r=self.client.patch('/api/business-database/row',json=self.body())
        self.assertEqual(r.status_code,409);self.cur.execute.assert_not_called();self.conn.commit.assert_not_called()

    def test_safe_delete_and_request_replay_do_not_repeat_mutation(self):
        with patch.object(edit,'references',return_value=[]):
            r=self.client.post('/api/business-database/delete-row',json=self.body(changes={}))
        self.assertEqual(r.status_code,200,r.text);self.assertIn('DELETE FROM `samples` WHERE `id`=%s',self.cur.execute.call_args_list[0].args[0])
        self.cur.reset_mock();self.conn.reset_mock()
        with patch.object(edit,'previous',return_value={'status':'completed','operation_id':'saved'}):
            r=self.client.patch('/api/business-database/row',json=self.body())
        self.assertEqual(r.status_code,200);self.assertTrue(r.json()['replayed']);self.cur.execute.assert_not_called();self.conn.commit.assert_not_called()
    def test_exact_precision_hash_numeric_validation_and_date_rules(self):
        self.assertNotEqual(edit.version({'amount':Decimal('900719925474099.01')}),edit.version({'amount':Decimal('900719925474099.02')}))
        self.assertEqual(edit.serial(9007199254740993),'9007199254740993')
        with self.assertRaises(ValueError):edit.coerce(None,COLS[1])
        with self.assertRaises(ValueError):edit.coerce('nan',COLS[1])
        with self.assertRaises(ValueError):edit.validate_business('inventory',{'quantity_on_hand':-1})
        with self.assertRaises(ValueError):edit.validate_business('inventory',{'production_date':'2027-01-01','expiry_date':'2026-01-01'})
