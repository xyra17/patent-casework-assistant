"""Task intent governs charts on completion, reload and PDF export."""
import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from api import server
from tools.chart_utils import build_chart_items, build_chart_spec, task_needs_charts
from tools.chart_report import build_chart_report
from tools.answer_contracts import fact_block, render_answer


RECOMMEND = ('新接了一个向量数据库近邻检索的AI案件，结合类似旧案的技术经历、目前在办量和'
             '初稿、客户送审、向局提交三个期限，推荐合适的代理师。只给建议，由Boss确认分案。')


class ChartIntentTests(unittest.TestCase):
    def evidence(self):
        columns, rows = ['承办人', '在办案件数量'], [['甲', 3], ['乙', 5]]
        return [{'id': 'S1', 'sql': 'select owner, count(*) from cases group by owner',
                 'columns': columns, 'rows': rows, 'rowCount': 2,
                 'chart': build_chart_spec(columns, rows)}]

    def test_business_decision_and_document_comparison_do_not_imply_charts(self):
        for query in (RECOMMEND, RECOMMEND + '\n【任务类型：数据分析】基于数据做分析',
                      '结合历史经验与在办量统计，推荐接案候选',
                      '核对两份案件表中期限和阶段的变化', '分析两版技术文档的差异', ''):
            with self.subTest(query=query):
                self.assertFalse(task_needs_charts(query))
                self.assertEqual(build_chart_items(self.evidence(), query), [])

    def test_explicit_charts_and_statistical_analysis_remain_available(self):
        for query in (RECOMMEND + '另附在办量柱状图。', '按承办人统计在办量',
                      '查看每月收案趋势', '统计向量检索案件的月度数量趋势',
                      '分析各领域案件占比', '对比两个月的收案量，生成图表'):
            with self.subTest(query=query):
                self.assertTrue(build_chart_items(self.evidence(), query))

    def test_complete_ocr_table_needs_visual_corroboration_for_chart(self):
        source={'id':'S1','type':'file','title':'清单.png','revision':'r1',
                'extraction_method':'mineru','content_complete':True,
                'content':'[第 1 页 · 正文块 4 · 表格]\n| 部件 | 编号 | 数量 |\n| --- | --- | --- |\n| 传感器 | A | 3 |\n| 电机 | B | 2 |'}
        witness={'id':'S2','type':'file','title':'清单.png','revision':'r1',
                 'visual_observation':True,'content':'传感器 A 3；电机 B 2'}
        self.assertEqual(build_chart_items([source],'按部件画柱状图'),[])
        charts=build_chart_items([source,witness],'按部件画柱状图')
        self.assertEqual(len(charts),1)
        self.assertEqual(charts[0]['chart']['series'][0]['data'],[3,2])
        self.assertEqual(charts[0]['chart']['sourceIds'],['S1','S2'])

    def test_complete_deadline_rows_are_added_to_delivered_answer(self):
        item={'id':'S3','type':'database','columns':['所编号','代理师','初稿期限／所限'],
              'rows':[['AI-2026-001','甲','2026-10-01'],['AI-2026-002','乙','2026-10-02']],
              'answer_contract':{'kind':'due_case_list','version':1,'as_of':'2026-11-05',
                                 'due_field':'draft_due_date','due_status':'overdue',
                                 'total':2,'complete_snapshot':True}}
        rendered,_=render_answer('前一条：AI-2026-001', [item])
        self.assertIn('AI-2026-002 | 乙 | 2026-10-02 | [S3]',rendered)
        item['answer_contract']['total']=3
        with self.assertRaisesRegex(ValueError,'完整来源行'):
            fact_block(item)

    def test_prohibition_overrides_statistical_and_chart_requests(self):
        for query in ('统计各代理师在办量，不要图表', '分析月度趋势，无需生成任何图表',
                      '用表格展示占比，不需要可视化', '统计在办量，不出图',
                      '统计在办量，不要生成数据图表', RECOMMEND + '不要生成柱状图。'):
            with self.subTest(query=query):
                self.assertEqual(build_chart_items(self.evidence(), query), [])

    def test_reload_hides_cached_charts_and_preserves_source_rows(self):
        original = {'status': 'completed', 'query': RECOMMEND, 'evidence': self.evidence()}
        with patch.object(server.storage, 'get_run', return_value=deepcopy(original)):
            view = server.get_run('chart-intent-test')
        self.assertEqual(view['charts'], [])
        self.assertNotIn('chart', view['evidence'][0])
        self.assertEqual(view['evidence'][0]['rows'], original['evidence'][0]['rows'])
        self.assertIn('chart', original['evidence'][0])

    def test_completion_does_not_publish_automatic_evidence_charts(self):
        report = AsyncMock()
        with patch.object(server, 'run_deep_agent', new=AsyncMock(return_value='推荐甲，由Boss确认。')), \
             patch.object(server.storage, 'get_run', return_value={}), \
             patch.object(server.evidence, 'snapshot', return_value=self.evidence()), \
             patch.object(server, 'build_query_result', new=AsyncMock(return_value=None)), \
             patch.object(server.monitor, 'report', new=report):
            asyncio.run(server._execute_task('chart-intent-test', RECOMMEND))
        args = report.call_args.args
        self.assertEqual(args[1], 'task_result')
        self.assertEqual(args[3]['charts'], [])
        self.assertNotIn('chart', args[3]['evidence'][0])

    def test_export_cannot_restore_irrelevant_cached_charts(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, '没有可导出的图表'):
                build_chart_report({'query': RECOMMEND, 'evidence': self.evidence()},
                                   Path(directory) / 'charts.pdf')
