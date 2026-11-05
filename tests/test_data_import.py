"""导入预校验与事务状态测试；MySQL 适配器使用隔离 SQLite 验证回滚和防重。"""
import copy
from contextlib import closing
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mysql.connector
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import data_import as imports


def column(name, kind, nullable=False, generated=False):
    return {'name': name, 'type': kind, 'nullable': nullable, 'generated': generated,
            'required': not nullable and not generated, 'default': None, 'comment': ''}


SCHEMA = {'demo': {'name': 'demo', 'engine': 'InnoDB', 'columns': [
    column('id','int', generated=True), column('code','varchar(8)'), column('qty','int'),
    column('amount','decimal(10,2)', True), column('day','date', True)]}}
MAPPING = {c: c for c in ('code','qty','amount','day')}
CSV = 'code,qty,amount,day\nA1,2,12.50,2026-10-23\n'


class Writer:
    def __init__(self, path, uncertain=False):
        self.connection = sqlite3.connect(path)
        self.uncertain = uncertain
    def cursor(self): return self
    def execute(self, sql, params=()):
        if sql.startswith('SET SESSION'): return
        try: self.connection.execute(sql.replace('%s','?'), params)
        except sqlite3.IntegrityError as exc:
            raise mysql.connector.IntegrityError(str(exc), errno=1062) from exc
    def start_transaction(self): self.connection.execute('BEGIN')
    def commit(self):
        self.connection.commit()
        if self.uncertain: raise ConnectionError('lost acknowledgement after commit')
    def rollback(self): self.connection.rollback()
    def close(self): self.connection.close()


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='data-import-test-')
        self.root = Path(self.temp.name)
        self.business = self.root / 'business.sqlite3'
        with closing(sqlite3.connect(self.business)) as connection:
            connection.execute('CREATE TABLE demo(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT UNIQUE NOT NULL, qty INTEGER NOT NULL,amount TEXT,day TEXT)')
        self.schema = copy.deepcopy(SCHEMA)
        self.patches = [patch.dict('os.environ', {'IMPORT_STORAGE_DIR': str(self.root / 'imports'), 'MYSQL_IMPORT_USER': 'test', 'MYSQL_IMPORT_PASSWORD':'test'}),
                        patch.object(imports, '_schema', side_effect=lambda: self.schema),
                        patch.object(imports, '_writer', side_effect=lambda: Writer(self.business))]
        for p in self.patches: p.start()
        app = FastAPI(); app.include_router(imports.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def stage(self, text=CSV, name='sales.csv'):
        response = self.client.post('/api/data-import/upload', files={'file': (name, text.encode())})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def validate(self, batch_id, mapping=None):
        return self.client.post(f'/api/data-import/batches/{batch_id}/validate',
            json={'table':'demo','sheet':'CSV','mapping': MAPPING if mapping is None else mapping})

    def commit(self, batch_id, token):
        return self.client.post(f'/api/data-import/batches/{batch_id}/commit', json={'validation_token':token})

    def rows(self):
        with closing(sqlite3.connect(self.business)) as connection:
            return connection.execute('SELECT code,qty,amount,day FROM demo').fetchall()

    def test_preview_commit_idempotency_and_identical_file_block(self):
        batch_id = self.stage()
        self.assertTrue((self.root / 'imports' / batch_id / 'source.csv').is_file())
        self.assertEqual(self.rows(), [])
        preview = self.validate(batch_id).json()
        self.assertTrue(preview['valid'])
        self.assertEqual(preview['preview'][0], ['A1',2,'12.50','2026-10-23'])
        self.assertEqual(self.rows(), [])
        token = preview['validation_token']
        self.assertEqual(self.commit(batch_id, '0'*32).status_code, 409)
        self.assertEqual(self.commit(batch_id, token).json()['inserted_rows'], 1)
        self.assertTrue(self.commit(batch_id, token).json()['replayed'])
        self.assertEqual(len(self.rows()), 1)
        other = self.stage(name='same-content.csv')
        self.assertEqual(self.validate(other).status_code, 409)
        self.assertEqual(self.client.delete(f'/api/data-import/batches/{batch_id}').status_code, 409)

    def test_rollback_all_rows_on_database_constraint_failure(self):
        batch_id = self.stage('code,qty,amount,day\nA1,2,,\nA1,4,,\n')
        validation = self.validate(batch_id).json()
        self.assertTrue(validation['valid'])
        result = self.commit(batch_id, validation['validation_token'])
        self.assertEqual(result.status_code, 409)
        self.assertIn('第 3 行', result.json()['detail'])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.client.get(f'/api/data-import/batches/{batch_id}').json()['status'], 'failed')

    def test_validation_errors_missing_mapping_and_schema_change(self):
        batch_id = self.stage('code,qty,amount,day\nTOO-LONG-CODE,2.5,12.345,2026-02-30\n')
        result = self.validate(batch_id).json()
        self.assertFalse(result['valid'])
        self.assertEqual(result['error_count'], 4)
        self.assertEqual({e['row'] for e in result['errors']}, {2})
        self.assertEqual(self.commit(batch_id,result['validation_token']).status_code,409)
        self.assertEqual(self.validate(batch_id, {'code':'code'}).status_code,400)
        self.assertEqual(self.validate(batch_id, {'id':'qty', **MAPPING}).status_code,400)
        good = self.stage()
        token = self.validate(good).json()['validation_token']
        self.schema['demo']['columns'][1]['type'] = 'varchar(7)'
        self.assertEqual(self.commit(good,token).status_code,409)
        self.assertEqual(self.rows(),[])

    def test_parameter_binding_and_discard_staging(self):
        batch_id = self.stage("code,qty,amount,day\nx');--,1,,\n")
        result = self.validate(batch_id).json()
        self.assertTrue(result['valid'])
        self.assertEqual(self.commit(batch_id,result['validation_token']).status_code,200)
        self.assertEqual(self.rows()[0][0], "x');--")
        draft = self.stage('code,qty,amount,day\nDRAFT,1,,\n')
        self.assertEqual(self.client.delete(f'/api/data-import/batches/{draft}').status_code,200)
        self.assertFalse((self.root/'imports'/draft).exists())
        self.assertEqual(self.client.get(f'/api/data-import/batches/{draft}').status_code,404)
        self.assertEqual(self.client.get('/api/data-import/batches/not-valid').status_code,400)

    def test_uncertain_commit_and_restart_prevent_retry(self):
        batch_id = self.stage()
        token = self.validate(batch_id).json()['validation_token']
        with patch.object(imports,'_writer',side_effect=lambda: Writer(self.business, uncertain=True)):
            self.assertEqual(self.commit(batch_id,token).status_code,503)
        self.assertEqual(len(self.rows()),1)
        self.assertEqual(self.commit(batch_id,token).status_code,409)
        self.assertEqual(self.client.get(f'/api/data-import/batches/{batch_id}').json()['status'],'uncertain')
        other = self.stage('code,qty,amount,day\nB2,1,,\n')
        with imports._lock:
            batch = imports._load(other)
            batch['status']='committing'
            imports._save(batch)
        imports._initialized.discard(str((self.root/'imports'/'imports.sqlite3').resolve()))
        self.assertEqual(self.client.get(f'/api/data-import/batches/{other}').json()['status'],'uncertain')

    def test_xlsx_dates_formulas_invalid_headers_and_limits(self):
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['code','qty','amount','day'])
        sheet.append(['A1',3,12.5,imports.date(2026,10,23)])
        output=io.BytesIO(); workbook.save(output)
        response=self.client.post('/api/data-import/upload',files={'file':('a.xlsx',output.getvalue())})
        self.assertEqual(response.status_code,200,response.text)
        batch=response.json()
        validation=self.client.post(f'/api/data-import/batches/{batch["id"]}/validate',json={'table':'demo','sheet':sheet.title,'mapping':MAPPING})
        self.assertTrue(validation.json()['valid'], validation.text)
        sheet['B2']='=1+2'
        output=io.BytesIO(); workbook.save(output)
        self.assertEqual(self.client.post('/api/data-import/upload',files={'file':('formula.xlsx',output.getvalue())}).status_code,400)
        for text in ['code,code\na,b\n','code,\na,b\n','code,qty\n', 'code\na,b\n']:
            self.assertEqual(self.client.post('/api/data-import/upload',files={'file':('bad.csv',text.encode())}).status_code,400)
        with patch.object(imports,'_MAX_BYTES',3):
            self.assertEqual(self.client.post('/api/data-import/upload',files={'file':('large.csv',b'1234')}).status_code,413)
        with patch.object(imports,'_MAX_ROWS',1):
            self.assertEqual(self.client.post('/api/data-import/upload',files={'file':('rows.csv',b'code\na\nb\n')}).status_code,400)
        self.assertEqual(self.client.post('/api/data-import/upload',files={'file':('../bad.csv',b'code\na')}).status_code,400)

    def test_failed_revalidation_revokes_previous_token(self):
        batch_id = self.stage()
        previous = self.validate(batch_id).json()['validation_token']
        self.assertEqual(self.validate(batch_id, {'code': 'code'}).status_code, 400)
        self.assertEqual(self.commit(batch_id, previous).status_code, 409)
        self.assertEqual(self.rows(), [])

    def test_database_target_change_revokes_confirmation(self):
        batch_id = self.stage()
        token = self.validate(batch_id).json()['validation_token']
        with patch.dict('os.environ', {'MYSQL_DATABASE': 'different_database'}):
            self.assertEqual(self.commit(batch_id, token).status_code, 409)
        self.assertEqual(self.rows(), [])

    def test_conversion_boundaries_and_nullable(self):
        for value in ['NaN','Infinity','2147483648','-2147483649','2.1']:
            with self.assertRaises(ValueError): imports._convert(value,column('x','int'))
        self.assertEqual(imports._convert('0012',column('x','varchar(8)')),'0012')
        self.assertIsNone(imports._convert('',column('x','int',True)))
        with self.assertRaises(ValueError): imports._convert('-1',column('x','int unsigned'))
        with self.assertRaises(ValueError): imports._convert('100000000',column('x','decimal(10,2)'))


if __name__ == '__main__': unittest.main()
