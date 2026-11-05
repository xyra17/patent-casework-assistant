"""Regression coverage for distinct chart views and historic evidence."""
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
from pypdf import PdfReader

from tools.chart_utils import build_chart_items, build_chart_spec
from tools.chart_report import _chart_drawing, _styles, build_chart_report
from reportlab.graphics.shapes import Rect


class ChartViewsTests(unittest.TestCase):
    def evidence(self, ident, rows, columns=None):
        columns = columns or ['therapeutic_area', 'dosage_form', 'drug_count']
        meta = {'therapeutic_area': {'label': '治疗领域'}, 'dosage_form': {'label': '剂型'},
                'drug_count': {'label': '药品数量'}}
        return {'id': ident, 'columns': columns, 'rows': rows, 'rowCount': len(rows),
                'chart': build_chart_spec(columns, rows, field_meta=meta)}

    def test_cross_table_defaults_to_named_heatmap_and_pdf_cells(self):
        item = self.evidence('S1', [['A', '片剂', 1], ['B', '针剂', 2]])
        spec = build_chart_items([item], "交叉统计")[0]['chart']
        self.assertEqual(spec['type'], 'heatmap')
        self.assertEqual(spec['title'], '治疗领域 × 剂型：药品数量')
        self.assertEqual(spec['pointFilters'][1], [
            {'field': 'therapeutic_area', 'value': 'B'}, {'field': 'dosage_form', 'value': '针剂'}])
        _styles()
        drawing = _chart_drawing(spec)
        self.assertEqual(sum(isinstance(obj, Rect) for obj in drawing.contents), 2)

    def test_identical_views_merge_sources_but_values_and_metrics_do_not(self):
        first = self.evidence('S1', [['A', '片剂', 1], ['B', '针剂', 2]])
        same = self.evidence('S2', list(reversed(first['rows'])))
        changed = self.evidence('S3', [['A', '片剂', 5], ['B', '针剂', 2]])
        multi = self.evidence('S4', [['A', '片剂', 1, 100], ['B', '针剂', 2, 100]],
                              ['therapeutic_area', 'dosage_form', 'drug_count', 'pct_in_area'])
        charts = build_chart_items([first, same, changed, multi], "交叉统计")
        self.assertEqual(len(charts), 3)
        self.assertEqual(charts[0]['chart']['sourceIds'], ['S1', 'S2', 'S4'])
        percent = charts[-1]['chart']
        self.assertEqual(percent['metricLabel'], '治疗领域内占比')
        self.assertEqual(percent['series'][0]['unit'], '%')
        self.assertEqual([cell[2] for cell in percent['heatmap']['data']], [100, 100])

    def test_no_chart_count_cap_and_singleton_is_excluded(self):
        entries = [self.evidence(f'S{i}', [['A', '片剂', i], ['B', '针剂', 2]]) for i in range(8)]
        entries.append(self.evidence('total', [[10]], ['total_drug_count']))
        self.assertEqual(len(build_chart_items(entries, "交叉统计")), 8)

    def test_old_evidence_does_not_shrink_original_forty_points(self):
        original = self.evidence('S1', [[str(i), i] for i in range(50)], ['category', 'amount'])
        original['rows'] = original['rows'][:20]
        del original['chart']['sourceRows']
        charts = build_chart_items([original], "交叉统计")
        self.assertEqual(len(charts[0]['chart']['series'][0]['data']), 40)
        self.assertEqual(charts[0]['chart']['rowCount'], 50)

    def test_scatter_keeps_both_numeric_coordinates(self):
        spec = build_chart_spec(['x', 'y'], [[1, 3], [2, 5]], query='散点关系')
        charts = build_chart_items([{'id': 'S1', 'chart': spec, 'columns': ['x', 'y'], 'rows': [[1, 3], [2, 5]]}], '散点关系')
        self.assertEqual(len(charts), 1)
        self.assertEqual(charts[0]['chart']['xAxis'], [1, 2])
        self.assertEqual(charts[0]['chart']['series'][0]['data'], [3, 5])

    def test_pdf_exports_all_chart_views_without_six_chart_cap(self):
        entries = [self.evidence(f'S{i}', [['A', '片剂', i], ['B', '针剂', 2]]) for i in range(7)]
        with tempfile.TemporaryDirectory() as directory:
            output = build_chart_report({'query': '交叉统计', 'evidence': entries}, Path(directory) / 'charts.pdf')
            self.assertEqual(len(PdfReader(output).pages), 7)

    def test_export_uses_current_execution_task_in_title_and_chart_selection(self):
        run = {'query': '旧问题：不能出现在本次报告', 'task_query': '旧问题带模式',
               'execution_query': '修订后的任务：按当前范围统计\n【任务类型：数据分析】内部模式提示',
               'applied_intent_id': 1, 'intent_revisions': [{'id': 1, 'revised_query': '修订后的任务：按当前范围统计'}],
               'evidence': [self.evidence('S1', [['A', '片剂', 1], ['B', '针剂', 2]])]}
        with tempfile.TemporaryDirectory() as directory, patch('tools.chart_report.build_chart_items', wraps=build_chart_items) as selection:
            output = build_chart_report(run, Path(directory) / 'current.pdf')
            selection.assert_called_once_with(run['evidence'], run['execution_query'])
            text = ''.join(page.extract_text() for page in PdfReader(output).pages)
            self.assertIn('修订后的任务', text)
            self.assertNotIn('旧问题', text)
            self.assertNotIn('内部模式提示', text)
