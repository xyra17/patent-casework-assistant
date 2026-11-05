"""Read-only business browsing: identifier whitelist, bound values, pagination and no writes."""
import unittest
import os
import tempfile
from pathlib import Path
os.environ.setdefault('APP_DB_PATH',str(Path(tempfile.mkdtemp(prefix='business-browser-tests-'))/'app.db'))
from unittest.mock import Mock,patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import business_database as db

class BusinessDatabaseTests(unittest.TestCase):
    def setUp(self):
        app=FastAPI();app.include_router(db.router);self.client=TestClient(app)
        self.cursor=Mock();self.conn=Mock();self.conn.cursor.return_value=self.cursor
        self.cursor.description=[('id',),('name',)]
        self.cursor.fetchall.side_effect=[[('items',)],[('id','int','NO','PRI',None,''),('name','varchar(80)','YES','',None,'')],[(1,'A'),(2,'B'),(3,'C')]]
        self.patch=patch.object(db,'_get_connection',return_value=self.conn);self.patch.start()
    def tearDown(self):self.patch.stop()
    def test_paginated_read_keeps_parameter_values_out_of_sql(self):
        r=self.client.post('/api/business-database/rows',json={'table':'items','page':2,'page_size':2,'filters':[{'field':'name','op':'eq','value':"x' OR 1=1"}]})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['rows'],[[1,'A'],[2,'B']]);self.assertTrue(r.json()['has_more'])
        sql,params=self.cursor.execute.call_args.args;self.assertIn('ORDER BY `id`',sql);self.assertNotIn("x'",sql);self.assertEqual(params,("x' OR 1=1",3,2));self.conn.close.assert_called_once();self.conn.commit.assert_not_called()
    def test_identifiers_unknown_fields_and_limits_are_rejected(self):
        for payload in [{'table':'items;DROP TABLE items'},{'table':'items','page_size':101},{'table':'items','page':0},{'table':'items','filters':[{'field':'name','op':'delete','value':'a'}]}]:
            self.assertEqual(self.client.post('/api/business-database/rows',json=payload).status_code,422)
        r=self.client.post('/api/business-database/rows',json={'table':'items','filters':[{'field':'secret','value':'a'}]});self.assertEqual(r.status_code,422);self.assertEqual(self.cursor.execute.call_count,2)
    def test_catalog_exposes_actual_append_only_capabilities(self):
        with patch.object(db,'configured',return_value=False),patch.object(db,'describe_schema',return_value={'items':[{'name':'id','key':'PRI'}]}):r=self.client.get('/api/business-database')
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['capabilities'],{'browse':True,'append':'import','create':False,'update':False,'delete':False,'audit':False})
        self.assertEqual(self.client.delete('/api/business-database/rows').status_code,405)
    def test_contains_escapes_wildcards_and_failure_closes_connection(self):
        self.client.post('/api/business-database/rows',json={'table':'items','filters':[{'field':'name','op':'contains','value':'10%_'}]})
        self.assertEqual(self.cursor.execute.call_args.args[1][0],'%10\\%\\_%')
        self.cursor.fetchall.side_effect=RuntimeError('connection failed')
        r=self.client.post('/api/business-database/rows',json={'table':'items'});self.assertEqual(r.status_code,503)
        self.assertNotIn('connection failed',r.text);self.assertEqual(self.conn.close.call_count,2)
