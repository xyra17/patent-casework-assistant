"""结果行、基础记录、模型视图和来源行集不可混为一谈。"""
import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('APP_DB_PATH', str(Path(tempfile.mkdtemp(prefix='query-contract-'))/'app.db'))
from api import context, evidence, source_policy, storage
from api.monitor import _record_tool_evidence, MonitorCallbackHandler
from rules import engine as rule_engine
from tools import mysql_tools as db
from tools.result_contract import format_table, result_scope, query_error, sql_is_limited
from tools.semantic_layer import SemanticLayer
from tools.semantic_query import SemanticQueryEngine, _record
from harness.middleware import context_tools


class QueryCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='query-scope-test-')
        self.dbpatch = patch.object(storage, '_DB_PATH', Path(self.temp.name)/'app.db')
        self.dbpatch.start()
        storage.init_db()
        context.set_session('scope-test', self.temp.name)
        source_policy.configure(['db_query'])
        rule_engine.reset()
        evidence.reset('scope-test')
        self.layer = SemanticLayer(Path('tests/fixtures/legacy_semantic.yaml'))
        self.layer._introspect_enabled = False
        self.engine = SemanticQueryEngine(self.layer)
        self.meta_patch = patch.object(db.semantic_layer, 'annotate_columns', return_value={})
        self.meta_patch.start()

    def tearDown(self):
        evidence.clear('scope-test')
        source_policy.reset_policy()
        context.reset_session()
        rule_engine.reset()
        self.meta_patch.stop()
        self.dbpatch.stop()
        self.temp.cleanup()

    def connection(self, count, columns=('region', 'amount')):
        cursor = Mock()
        cursor.description = [(name,) for name in columns]
        cursor.fetchmany.side_effect = lambda size: [(i, i * 2) for i in range(min(count, size))]
        conn = Mock()
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_lookahead_at_limit_below_limit_and_over_limit(self):
        for count, more in [(0, False), (99, False), (100, False), (101, True), (1205, True)]:
            with self.subTest(count=count):
                conn, cursor = self.connection(count)
                with patch.object(db, '_get_connection', return_value=conn):
                    result = db.run_read_query('SELECT region, amount FROM x', limit=100)
                self.assertEqual(result['completeness']['has_more'], more)
                self.assertEqual(result['completeness']['query_rows'], None if more else count)
                self.assertIsNone(result['completeness']['matched_rows'])
                cursor.fetchmany.assert_called_once_with(101)
                cursor.fetchall.assert_not_called()
                conn.close.assert_called_once()

    def test_top_n_and_aggregate_result_rows_are_not_base_record_counts(self):
        self.assertTrue(sql_is_limited('SELECT x FROM t LIMIT 5'))
        self.assertFalse(sql_is_limited("SELECT 'LIMIT 5', (SELECT x FROM t LIMIT 1)"))
        conn, cursor = self.connection(5)
        with patch.object(db, '_get_connection', return_value=conn):
            result = db.run_read_query('SELECT region, SUM(amount) FROM x GROUP BY region LIMIT 5')
        self.assertEqual(result['completeness']['query_rows'], 5)
        self.assertFalse(result['completeness']['has_more'])
        self.assertIsNone(result['completeness']['matched_rows'])
        cursor.execute.assert_called_once_with('SELECT region, SUM(amount) FROM x GROUP BY region LIMIT 5', ())
        scalar = {'columns': ['count'], 'rows': [[1205]], 'completeness': result_scope('SELECT COUNT(*) FROM x', 1, False, 500)}
        format_table(scalar)
        self.assertEqual(scalar['completeness']['query_rows'], 1)
        self.assertEqual(scalar['rows'][0][0], 1205)

    def test_semantic_single_multi_and_drilldown_execute_actual_probe_params(self):
        for run in [lambda: self.engine.query('drug_count', ['therapeutic_area'], limit=5),
                    lambda: self.engine.query_multi(['drug_count', 'total_sales'], ['therapeutic_area'], limit=5),
                    lambda: self.engine.drilldown('product', 'therapeutic_area', '心血管', 'drug_count', limit=5)]:
            conn, cursor = self.connection(6)
            with patch.object(db, '_get_connection', return_value=conn):
                result = run()
            self.assertEqual(result['params'][-1], 6)
            self.assertEqual(cursor.execute.call_args.args[1][-1], 6)
            self.assertEqual(result['completeness']['requested_limit'], 5)
            self.assertEqual(result['completeness']['scope'], 'semantic_groups')
            self.assertTrue(result['completeness']['has_more'])
            self.assertEqual(len(result['rows']), 5)
            self.assertIsNone(result['completeness']['query_rows'])

    def test_model_only_30_evidence_keeps_100_and_can_recover_last_row(self):
        result = {'columns': ['n'], 'rows': [[i] for i in range(100)], 'rowCount': 100,
                  'sql': 'SELECT n FROM t', 'completeness': result_scope('SELECT n FROM t', 100, False, 500)}
        with patch('tools.semantic_query.engine', self.engine):
            eid = _record(result, '完整性测试')
        saved = storage.load_evidence('scope-test', eid)[0]
        self.assertEqual(saved['rows'][-1], [99])
        self.assertEqual(saved['completeness']['model_rows'], 30)
        self.assertTrue(saved['completeness']['model_truncated'])
        self.assertEqual(saved['completeness']['evidence_rows'], 100)
        reader = next(tool for tool in context_tools('scope-test') if tool.name == 'read_preserved_evidence')
        offset, pages = 0, []
        while True:
            page = json.loads(reader.invoke({'evidence_id': eid, 'offset': offset, 'limit': 4000}))
            pages.append(page['content'])
            if page['next_offset'] is None: break
            offset = page['next_offset']
        self.assertEqual(json.loads(''.join(pages))['rows'][-1], [99])

    def test_formatter_escapes_cells_bounds_output_and_exposes_truncation(self):
        result = {'columns': ['text'], 'rows': [['x|y\n' + '长'*1000] for _ in range(100)],
                  'completeness': result_scope('SELECT text FROM t', 100, True, 100)}
        text = format_table(result)
        self.assertTrue(text.startswith('结果范围：'))
        self.assertIn('取数已截断', text)
        self.assertTrue(result['completeness']['model_cells_truncated'])
        self.assertLess(len(text), 19000)
        row_lines = [line for line in text.splitlines() if line.startswith('[')]
        self.assertIn('x|y\n', json.loads(row_lines[1])[0])
        self.assertTrue(result['completeness']['model_truncated'])

    def test_raw_sql_is_bounded_and_source_stores_all_returned_rows(self):
        conn, cursor = self.connection(1205)
        with patch.object(db, '_get_connection', return_value=conn):
            text = db.execute_sql_query('SELECT region, amount FROM x')
        saved = evidence.snapshot('scope-test')[0]
        self.assertEqual(len(saved['rows']), 500)
        self.assertTrue(saved['completeness']['has_more'])
        self.assertIn('模型视图已截断', text)
        cursor.fetchall.assert_not_called()
        cursor.execute.assert_called_once_with('SELECT region, amount FROM x')

    def test_preview_and_filtered_details_probe_with_no_implicit_count(self):
        conn, cursor = self.connection(101)
        with patch.object(db, '_get_connection', return_value=conn):
            db.get_table_data('x')
        saved = evidence.snapshot('scope-test')[0]
        self.assertEqual(len(saved['rows']), 100)
        self.assertTrue(saved['completeness']['has_more'])
        self.assertEqual(saved['sql'], 'SELECT * FROM `x` LIMIT 101')
        conn, cursor = self.connection(101)
        cursor.fetchall.return_value = [('x',)]
        with patch.object(db, '_get_connection', return_value=conn):
            details = db.fetch_table_rows('x', {'region': '华东'})
        self.assertTrue(details['completeness']['has_more'])
        self.assertEqual(details['rowCount'], 100)
        self.assertEqual(cursor.execute.call_args.args[1], ['华东', 101])

    def test_scope_changes_do_not_deduplicate_as_same_evidence(self):
        item = {'type': 'database', 'title': 'same', 'columns': ['x'], 'rows': [[1]],
                'completeness': result_scope('SELECT x FROM t', 1, False, 1)}
        first = evidence.add('scope-test', item)
        changed = copy.deepcopy(item)
        changed['completeness']['has_more'] = True
        self.assertNotEqual(first, evidence.add('scope-test', changed))
        self.assertEqual(first, evidence.add('scope-test', item))

    def test_fallback_output_recorder_never_parses_scope_as_csv_or_claims_full_rows(self):
        result = {'columns': ['x'], 'rows': [[i] for i in range(100)],
                  'completeness': result_scope('SELECT x FROM t', 100, False, 500)}
        _record_tool_evidence('scope-test', 'execute_sql_query', {'sql': 'SELECT x FROM t'}, format_table(result))
        saved = evidence.snapshot('scope-test')[0]
        self.assertEqual(saved['columns'], ['x'])
        self.assertEqual(len(saved['rows']), 30)
        self.assertTrue(saved['completeness']['evidence_truncated'])

    def test_structured_query_errors_are_failures_not_successful_sources(self):
        import mysql.connector
        for errno, code in [(1054, 'unknown_schema'), (1045, 'permission_denied'), (3024, 'query_timeout'), (2003, 'connection_error')]:
            text = query_error(mysql.connector.Error('test', errno=errno))
            self.assertEqual(json.loads(text.split('：', 1)[1])['code'], code)
            _record_tool_evidence('scope-test', 'execute_sql_query', {}, text)
        self.assertEqual(evidence.snapshot('scope-test'), [])
        callback = MonitorCallbackHandler('scope-test')
        asyncio.run(callback.on_tool_start({'name': 'execute_sql_query'}, '{}', run_id='scope-error'))
        asyncio.run(callback.on_tool_end('数据库查询失败：{}', run_id='scope-error'))
        self.assertNotIn('db_query', callback.called_sources)

    def test_source_denial_still_blocks_connection_before_any_probe(self):
        source_policy.configure([], ['db_query'])
        with patch.object(db, '_get_connection', side_effect=AssertionError('must not connect')):
            self.assertIn('禁止执行', db.execute_sql_query('SELECT 1'))

    def test_empty_set_has_explicit_zero_without_silent_early_return(self):
        conn, _ = self.connection(0)
        with patch.object(db, '_get_connection', return_value=conn):
            text = db.execute_sql_query('SELECT region, amount FROM x')
        saved = evidence.snapshot('scope-test')[0]
        self.assertEqual(saved['rowCount'], 0)
        self.assertEqual(saved['completeness']['model_rows'], 0)
        self.assertEqual(saved['completeness']['query_rows'], 0)
        self.assertIn('结果没有数据', text)

    def test_review_keeps_independent_scope_index_when_big_table_consumes_text_budget(self):
        from unittest.mock import AsyncMock
        from langchain_core.messages import AIMessage
        from agent import main_agent
        llm = Mock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content='通过'))
        llm.with_structured_output=Mock(return_value=llm)
        first = {'id': 'S1', 'columns': ['x'], 'rows': [['x'*25000]], 'completeness': {'has_more': False}}
        second = {'id': 'S2', 'columns': ['n'], 'rows': [[1]], 'completeness': {'has_more': True}}
        with patch.object(main_agent, 'get_llm', return_value=llm):
            asyncio.run(main_agent.soft_review({'report': '部分结果', 'evidence': [first, second]}))
        prompt = llm.ainvoke.call_args.args[0][-1].content
        scope_index = prompt.split('独立范围索引（资料）：')[1].split('\n')[0]
        self.assertEqual(json.loads(scope_index)[1]['completeness'], {'has_more': True})
        self.assertIn('部分分组之和不等于全量总和', prompt)

    def test_correction_and_no_progress_detection_include_scope_changes(self):
        from agent.main_agent import _revision_evidence, _progress_signature
        item = {'id': 'S1', 'type': 'database', 'rows': [['x'*10000]],
                'completeness': {'has_more': True}, 'sql': 'SELECT x FROM t', 'params': [101]}
        correction = _revision_evidence([item], ['核对 S1'])
        self.assertIn('"has_more": true', correction)
        self.assertIn('"params": [101]', correction)
        old = {'report': 'same', 'evidence': [item]}
        new = copy.deepcopy(old)
        new['evidence'][0]['completeness']['has_more'] = False
        self.assertNotEqual(_progress_signature(old, []), _progress_signature(new, []))
