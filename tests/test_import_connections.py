"""Connection selection must bind catalog, validation, and commit to one server-owned target."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from api import import_connections as connections
from api import data_import as imports
import test_data_import as fixtures

class ConnectionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'connections.json'
        self.profile=dict(id='branch',label='分库',host='127.0.0.1',port=3307,database='branch_db',read_user_env='BRANCH_READER',read_password_env='BRANCH_READER_SECRET',write_user_env='BRANCH_WRITER',write_password_env='BRANCH_WRITER_SECRET')
        self.path.write_text(json.dumps([self.profile]));self.env=patch.dict(os.environ,{'IMPORT_CONNECTIONS_FILE':str(self.path),'BRANCH_READER':'read_test','BRANCH_READER_SECRET':'secret_a','BRANCH_WRITER':'write_test','BRANCH_WRITER_SECRET':'secret_b'})
        self.env.start()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_public_catalog_never_exposes_credentials_or_env_names(self):
        public=connections.public_profiles();self.assertEqual([p['id'] for p in public],['default','branch'])
        self.assertEqual(set(public[1]),{'id','label','host','port','database'})
        self.assertNotIn('secret',json.dumps(public));self.assertNotIn('BRANCH_',json.dumps(public))
    def test_scope_is_nested_and_connect_uses_selected_read_write_credentials(self):
        with connections.scope('branch'):
            self.assertEqual(connections.destination()['database'],'branch_db')
            with patch.object(connections.mysql.connector,'connect') as connect:
                imports._schema();self.assertEqual(connect.call_args.kwargs['user'],'read_test');self.assertTrue(connect.call_args.kwargs['autocommit'])
                imports._writer();self.assertEqual(connect.call_args.kwargs['user'],'write_test');self.assertFalse(connect.call_args.kwargs['autocommit'])
                self.assertEqual(connect.call_args.kwargs['database'],'branch_db')
            with connections.scope('default'):self.assertEqual(connections.current()['id'],'default')
            self.assertEqual(connections.current()['id'],'branch')
        self.assertIsNone(connections.current())
    def test_unknown_or_duplicate_or_inline_password_configuration_fails_closed(self):
        with self.assertRaises(HTTPException) as error:
            with connections.scope('arbitrary'):pass
        self.assertEqual(error.exception.status_code,404)
        for entries in [[self.profile,self.profile],[dict(self.profile,password='never-inline')]]:
            self.path.write_text(json.dumps(entries))
            with self.assertRaises(HTTPException) as error:connections.profiles()
            self.assertEqual(error.exception.status_code,503)

class SelectedImportTests(unittest.TestCase):
    stage=fixtures.ImportTests.stage
    rows=fixtures.ImportTests.rows
    tearDown=fixtures.ImportTests.tearDown
    def setUp(self):
        fixtures.ImportTests.setUp(self)
        self.profile=dict(id='branch',label='分库',host='localhost',port=3307,database='branch_db',read_user_env='BRANCH_READER',read_password_env='BRANCH_READER_SECRET',write_user_env='BRANCH_WRITER',write_password_env='BRANCH_WRITER_SECRET')
        path=self.root/'connections.json';path.write_text(json.dumps([self.profile]))
        p=patch.dict(os.environ,{'IMPORT_CONNECTIONS_FILE':str(path),'BRANCH_WRITER':'test','BRANCH_WRITER_SECRET':'test'});p.start();self.patches.append(p)
    def test_selected_batch_binding_rejects_wrong_connection_and_changed_destination(self):
        batch=self.stage()
        preview=self.client.post(f'/api/data-import/batches/{batch}/validate',json={'table':'demo','sheet':'CSV','mapping':fixtures.MAPPING,'connection_id':'branch'}).json()
        stored=imports._load(batch);self.assertEqual(stored['connection_id'],'branch');self.assertEqual(stored['destination']['database'],'branch_db')
        mismatch=self.client.post(f'/api/data-import/batches/{batch}/commit',json={'validation_token':preview['validation_token'],'connection_id':'default'})
        self.assertEqual(mismatch.status_code,409);self.assertEqual(self.rows(),[])
        self.profile['database']='changed_db';(self.root/'connections.json').write_text(json.dumps([self.profile]))
        changed=self.client.post(f'/api/data-import/batches/{batch}/commit',json={'validation_token':preview['validation_token'],'connection_id':'branch'})
        self.assertEqual(changed.status_code,409);self.assertEqual(self.rows(),[])
    def test_selected_catalog_and_successful_commit(self):
        with patch.object(imports,'_schema',side_effect=ConnectionError('offline')):
            registry=self.client.get('/api/data-import/connections')
            self.assertEqual(registry.status_code,200);self.assertEqual(len(registry.json()['connections']),2)
        catalog=self.client.get('/api/data-import?connection_id=branch').json()
        self.assertEqual(catalog['destination']['database'],'branch_db');self.assertEqual(catalog['connection_id'],'branch')
        batch=self.stage();preview=self.client.post(f'/api/data-import/batches/{batch}/validate',json={'table':'demo','sheet':'CSV','mapping':fixtures.MAPPING,'connection_id':'branch'}).json()
        result=self.client.post(f'/api/data-import/batches/{batch}/commit',json={'validation_token':preview['validation_token'],'connection_id':'branch'})
        self.assertEqual(result.status_code,200,result.text);self.assertEqual(len(self.rows()),1)
