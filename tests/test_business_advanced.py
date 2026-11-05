"""SQL scope, parser fallbacks and signed preview contracts."""
import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from api.business_advanced import classify,token_for,verify

class AdvancedValidationTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'MYSQL_DATABASE':'business_test','MYSQL_EDIT_PASSWORD':'test-only'});self.env.start();self.addCleanup(self.env.stop)
    def test_supported_sql_and_capped_select(self):
        self.assertIn('LIMIT 501',classify('SELECT * FROM drugs')[0])
        self.assertIn('LIMIT 2',classify('SELECT * FROM drugs LIMIT 2')[0])
        for sql in ['SELECT 1','SHOW TABLES','SHOW COLUMNS FROM drugs','DESC drugs','EXPLAIN SELECT * FROM drugs','UPDATE drugs SET drug_id=10','DELETE FROM drugs','INSERT INTO drugs(drug_id) VALUES(1)','CREATE TABLE t(id INT PRIMARY KEY)','ALTER TABLE t ADD COLUMN label VARCHAR(30)','ALTER TABLE t MODIFY COLUMN label VARCHAR(50)','ALTER TABLE t ADD INDEX idx(label)','ALTER TABLE t RENAME TO t2','DROP TABLE t','TRUNCATE TABLE t','WITH x AS (SELECT * FROM drugs) SELECT * FROM x']:
            with self.subTest(sql=sql):self.assertTrue(classify(sql)[0])
    def test_cross_schema_server_file_and_multiple_statements_rejected(self):
        for sql in ['SHOW TABLES FROM mysql','SHOW CREATE TABLE mysql.user','SHOW GRANTS','EXPLAIN DELETE FROM drugs','SELECT * FROM mysql.user','SELECT * FROM business_test.drugs JOIN mysql.user ON 1=1','UPDATE drugs SET drug_id=(SELECT COUNT(*) FROM mysql.user)','ALTER TABLE drugs RENAME TO mysql.drugs','CREATE TABLE t AS SELECT * FROM mysql.user','CREATE DATABASE other','DROP DATABASE business_test','GRANT ALL ON *.* TO x','SET FOREIGN_KEY_CHECKS=0','USE mysql','SELECT SLEEP(10) FROM drugs','SELECT LOAD_FILE(\'/etc/passwd\') FROM drugs','SELECT * FROM drugs FOR UPDATE','SELECT * FROM drugs; DELETE FROM drugs','CALL evil()','LOAD DATA INFILE \'/tmp/a\' INTO TABLE drugs']:
            with self.subTest(sql=sql),self.assertRaises(ValueError):classify(sql)
    def test_signed_preview_binds_request_data_schema_and_time(self):
        payload={'kind':'bulk_delete','keys':[{'id':'1'}]};token=token_for(payload,'state')
        verify(token,payload,'state')
        for p,state in [(payload,'changed'),({'keys':[{'id':'2'}]},'state')]:
            with self.assertRaises(HTTPException):verify(token,p,state)
        with patch('api.business_advanced.time.time',return_value=10**12),self.assertRaises(HTTPException):verify(token,payload,'state')
