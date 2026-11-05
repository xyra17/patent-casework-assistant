"""Physical structure and grouping compatibility, without business writes."""
import os,tempfile,unittest
from pathlib import Path
os.environ.setdefault('APP_DB_PATH',str(Path(tempfile.mkdtemp())/'app.db'))
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import business_database as db
from tools.semantic_layer import SemanticLayer
from tools.semantic_query import SemanticQueryEngine

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.model=SemanticLayer(Path('tests/fixtures/legacy_semantic.yaml'));self.model._introspect_enabled=False
        self.engine=SemanticQueryEngine(self.model)
        self.patches=[patch.object(db,'semantic_layer',self.model),patch('tools.semantic_query.engine',self.engine),patch('rules.engine.business_rules',return_value=[])]
        for p in self.patches:p.start()
        app=FastAPI();app.include_router(db.router);self.client=TestClient(app)
        self.schema={t:[{'name':n,'type':'varchar(32)','nullable':True,'key':'PRI' if n==c.get('primary_key') else '','default':None,'extra':''} for n in c['columns']] for t,c in self.model._tables.items()}
    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):p.stop()
    def test_safe_parent_dimensions_and_no_fact_fanout(self):
        with patch.object(db,'describe_schema',return_value=self.schema):r=self.client.post('/api/business-database/metric-options',json={'metrics':['total_sales','inventory_quantity']})
        self.assertEqual(r.status_code,200,r.text)
        data=r.json();options={v['value']:v for v in data['dimensions']}
        self.assertTrue(options['drugs.dosage_form']['compatible'])
        self.assertFalse(options['sales_records.region']['compatible'])
        self.assertIn('重复累计',options['sales_records.region']['reason'])
        self.assertEqual(data['tables'],['sales_records','inventory'])
        with self.assertRaises(ValueError):db.validate_metric_dimensions(['drug_count'],['sales_records.region'])
    def test_unknown_and_missing_fields_fail_closed(self):
        schema={**self.schema,'drugs':[]}
        with patch.object(db,'describe_schema',return_value=schema):r=self.client.post('/api/business-database/metric-options',json={'metrics':['total_sales']})
        option=next(v for v in r.json()['dimensions'] if v['value']=='drugs.dosage_form')
        self.assertFalse(option['compatible']);self.assertIn('不存在',option['reason'])
        with patch.object(db,'describe_schema',side_effect=RuntimeError('offline')):self.assertEqual(self.client.post('/api/business-database/metric-options',json={'metrics':['total_sales']}).status_code,503)
        with self.assertRaises(ValueError):db.validate_metric_dimensions(['unknown'],[])
    def test_catalog_marks_only_explicit_semantics(self):
        self.schema['drugs'].append({'name':'new_field','type':'int','nullable':False,'default':'0','key':'','extra':''})
        with patch.object(db,'describe_schema',return_value=self.schema):r=self.client.get('/api/business-database')
        columns=next(t for t in r.json()['tables'] if t['name']=='drugs')['columns'];cols={c['name']:c for c in columns}
        self.assertTrue(cols['drug_id']['semantic_configured']);self.assertFalse(cols['new_field']['semantic_configured']);self.assertEqual(cols['new_field']['default'],'0')

    def test_stale_parent_key_and_deleted_metric_column_rejected(self):
        self.schema['drugs'][0]['key']=''
        with self.assertRaisesRegex(ValueError,'实际主键'):
            db.validate_metric_dimensions(['total_sales'],['drugs.dosage_form'],self.schema)
        self.schema['sales_records']=[c for c in self.schema['sales_records'] if c['name']!='total_amount']
        with self.assertRaisesRegex(ValueError,'指标字段'):
            db.validate_metric_dimensions(['total_sales'],[],self.schema)
