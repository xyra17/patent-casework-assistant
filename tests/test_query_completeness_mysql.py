from tests.mysql_support import test_config
"""Opt-in real query test; creates and removes its own schema and SELECT-only user."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

@unittest.skipUnless(os.getenv('RUN_MYSQL_QUERY_TESTS') == '1', '真实MySQL结果范围验收需显式启用')
class QueryCompletenessMySQLTests(unittest.TestCase):
    def test_1205_rows_top_n_aggregates_and_exact_boundary(self):
        import mysql.connector
        from dotenv import dotenv_values
        from api import context, evidence, source_policy, storage
        from rules import engine as rule_engine
        from tools import mysql_tools as db
        from tools.semantic_layer import SemanticLayer
        from tools.semantic_query import SemanticQueryEngine
        from utils.path_utils import PROJECT_ROOT
        config = test_config()
        suffix = uuid4().hex[:12]
        database, username, password = 'query_test_'+suffix, 'query_'+suffix, uuid4().hex
        admin = mysql.connector.connect(host=config.get('MYSQL_HOST','localhost'),
            port=int(config.get('MYSQL_PORT',3306)), user='root', password=config['MYSQL_DEV_ROOT_PASSWORD'],
            autocommit=True, use_pure=True)
        cursor = admin.cursor()
        try:
            cursor.execute(f'CREATE DATABASE `{database}`')
            cursor.execute(f'CREATE USER %s@%s IDENTIFIED BY %s', (username, '%', password))
            cursor.execute(f'GRANT SELECT ON `{database}`.* TO %s@%s', (username, '%'))
            cursor.execute(f'CREATE TABLE `{database}`.items(id INT PRIMARY KEY, region VARCHAR(20), amount DECIMAL(18,2))')
            cursor.executemany(f'INSERT INTO `{database}`.items VALUES(%s,%s,%s)', [(i, 'region-'+str(i % 13), '2.50') for i in range(1205)])
            env = {'MYSQL_HOST':config.get('MYSQL_HOST','localhost'),'MYSQL_PORT':config.get('MYSQL_PORT','3306'),
                   'MYSQL_DATABASE':database,'MYSQL_USER':username,'MYSQL_PASSWORD':password}
            with tempfile.TemporaryDirectory(prefix='mysql-query-scope-') as temporary, patch.dict(os.environ,env), patch.object(storage,'_DB_PATH',Path(temporary)/'app.db'):
                storage.init_db()
                context.set_session('mysql-scope', temporary)
                evidence.reset('mysql-scope')
                source_policy.configure(['db_query'])
                rule_engine.reset()
                model = Path(temporary)/'model.yaml'
                model.write_text('''version: 1
name: isolated query test
tables:
  items:
    primary_key: id
    columns:
      id: {role: id}
      region: {role: dimension}
      amount: {role: metric}
metrics:
  amount_sum: {label: amount sum, table: items, expression: SUM(amount)}
relationships: []
hierarchies: {}
''')
                layer = SemanticLayer(model)
                layer._introspect_enabled = False
                engine = SemanticQueryEngine(layer)
                raw = db.run_read_query('SELECT * FROM items ORDER BY id',limit=100)
                self.assertEqual(raw['rowCount'],100)
                self.assertTrue(raw['completeness']['has_more'])
                count = db.run_read_query('SELECT COUNT(*) FROM items')
                self.assertEqual(count['rows'],[[1205]])
                self.assertEqual(count['completeness']['query_rows'],1)
                self.assertIsNone(count['completeness']['matched_rows'])
                top = db.run_read_query('SELECT region, SUM(amount) FROM items GROUP BY region ORDER BY SUM(amount) DESC LIMIT 5')
                self.assertEqual(top['completeness']['query_rows'],5)
                self.assertTrue(top['completeness']['sql_limited'])
                self.assertFalse(top['completeness']['has_more'])
                truncated = engine.query('amount_sum',['region'],limit=5)
                self.assertTrue(truncated['completeness']['has_more'])
                self.assertEqual(truncated['params'][-1],6)
                exact = engine.query('amount_sum',['region'],limit=13)
                self.assertFalse(exact['completeness']['has_more'])
                self.assertEqual(exact['completeness']['query_rows'],13)
                full_sum = engine.query('amount_sum',[])
                self.assertEqual(str(full_sum['rows'][0][0]),'3012.5')
                from tools.statistics_check import validate_claims
                checked_source = {**full_sum, 'id': 'S1', 'type': 'database'}
                # Delimit the metric and value: sum3012 is otherwise an ID-like token.
                self.assertEqual(validate_claims('amount sum：3012.50[S1]', [checked_source], [{'unit_id': 0, 'metric_text': 'amount sum', 'value_text': '3012.50'}])['status'], 'passed')
                self.assertEqual(validate_claims('amount sum：3013.50[S1]', [checked_source], [{'unit_id': 0, 'metric_text': 'amount sum', 'value_text': '3013.50'}])['status'], 'failed')
                with patch.object(db.semantic_layer,'annotate_columns',return_value={}):
                    text = db.execute_sql_query('SELECT * FROM items ORDER BY id')
                saved = evidence.snapshot('mysql-scope')[0]
                self.assertEqual(len(saved['rows']),500)
                self.assertEqual(saved['rows'][-1][0],499)
                self.assertTrue(saved['completeness']['has_more'])
                self.assertIn('模型视图已截断',text)
                conn=db._get_connection()
                try:
                    with self.assertRaises(mysql.connector.Error):
                        conn.cursor().execute('DELETE FROM items WHERE id=1')
                finally: conn.close()
        finally:
            evidence.clear('mysql-scope')
            context.reset_session()
            source_policy.reset_policy()
            rule_engine.reset()
            cursor.execute(f'DROP DATABASE IF EXISTS `{database}`')
            cursor.execute('DROP USER IF EXISTS %s@%s',(username,'%'))
            admin.close()
