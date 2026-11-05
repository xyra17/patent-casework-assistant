"""可选真实 MySQL 集成测试：只在独立随机测试库创建记录，结束后移除测试库。"""
import os
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch

import mysql.connector
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import data_import as imports


@unittest.skipUnless(os.getenv('RUN_MYSQL_IMPORT_TESTS') == '1', '真实 MySQL 测试需显式启用')
class MySQLImportTests(unittest.TestCase):
    def test_mysql_transactions_constraints_schema_and_restart(self):
        root_password = os.getenv('MYSQL_DEV_ROOT_PASSWORD')
        self.assertTrue(root_password, '需本机开发库管理凭据')
        database = 'tanyuan_import_test_' + uuid4().hex[:10]
        settings = {'host': os.getenv('MYSQL_HOST', 'localhost'), 'port': int(os.getenv('MYSQL_PORT', '3306')),
                    'user': 'root', 'password': root_password, 'use_pure': True, 'autocommit': True}
        admin = mysql.connector.connect(**settings)
        cursor = admin.cursor()
        cursor.execute(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4')
        try:
            cursor.execute(f'USE `{database}`')
            cursor.execute('CREATE TABLE parents(id INT PRIMARY KEY) ENGINE=InnoDB')
            cursor.execute('INSERT INTO parents VALUES(1)')
            cursor.execute('CREATE TABLE samples(id INT PRIMARY KEY AUTO_INCREMENT,code VARCHAR(12) NOT NULL UNIQUE,parent_id INT NOT NULL,amount DECIMAL(10,2),FOREIGN KEY(parent_id) REFERENCES parents(id)) ENGINE=InnoDB')
            with tempfile.TemporaryDirectory(prefix='real-mysql-import-') as root, patch.dict(os.environ, {
                'MYSQL_DATABASE': database, 'MYSQL_USER':'root', 'MYSQL_PASSWORD':root_password,
                'MYSQL_IMPORT_USER':'root', 'MYSQL_IMPORT_PASSWORD':root_password, 'IMPORT_STORAGE_DIR':root,
            }):
                app = FastAPI(); app.include_router(imports.router)
                with TestClient(app) as client:
                    tables = client.get('/api/data-import').json()['tables']
                    self.assertEqual({t['name'] for t in tables}, {'parents','samples'})
                    mapping = {'code':'编码','parent_id':'关联','amount':'金额'}
                    def stage(text,valid=True):
                        response = client.post('/api/data-import/upload',files={'file':('sample.csv',text.encode())})
                        self.assertEqual(response.status_code,200,response.text)
                        batch_id=response.json()['id']
                        preview=client.post(f'/api/data-import/batches/{batch_id}/validate',json={'table':'samples','sheet':'CSV','mapping':mapping})
                        self.assertEqual(preview.status_code,200,preview.text)
                        self.assertEqual(preview.json()['valid'],valid,preview.text)
                        return batch_id,preview.json()['validation_token']
                    def commit(batch_id,token):
                        return client.post(f'/api/data-import/batches/{batch_id}/commit',json={'validation_token':token})
                    good,token=stage('编码,关联,金额\nNEW-A,1,12.50\nNEW-B,1,25.00\n')
                    success=commit(good,token)
                    self.assertEqual(success.status_code,200,success.text)
                    self.assertEqual(success.json()['inserted_rows'],2)
                    self.assertTrue(commit(good,token).json()['replayed'])
                    cursor.execute('SELECT COUNT(*) FROM samples'); self.assertEqual(cursor.fetchone()[0],2)
                    bad,token=stage('编码,关联,金额\nROLLBACK,1,1.00\nBAD-FK,999,2.00\n')
                    failure=commit(bad,token)
                    self.assertEqual(failure.status_code,409,failure.text)
                    self.assertIn('外键',failure.json()['detail'])
                    cursor.execute('SELECT COUNT(*) FROM samples'); self.assertEqual(cursor.fetchone()[0],2)
                    duplicate,token=stage('编码,关联,金额\nROLLBACK,1,1.00\nNEW-A,1,2.00\n',valid=False)
                    self.assertEqual(commit(duplicate,token).status_code,409)
                    cursor.execute('SELECT COUNT(*) FROM samples'); self.assertEqual(cursor.fetchone()[0],2)
                    changed,token=stage('编码,关联,金额\nCHANGED,1,1.00\n')
                    cursor.execute('ALTER TABLE samples MODIFY code VARCHAR(13) NOT NULL')
                    self.assertEqual(commit(changed,token).status_code,409)
                    self.assertEqual(client.get(f'/api/data-import/batches/{good}').json()['status'],'completed')
        finally:
            cursor.execute(f'DROP DATABASE `{database}`')
            admin.close()


if __name__ == '__main__': unittest.main()
