"""检索控制与结构解析回归：不调用真实 LLM，不依赖业务语料。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from docx import Document as WordDocument
from reportlab.pdfgen.canvas import Canvas
from tools.document_parser import parse_document
from tools.hybrid_retrieval import LexicalIndex, fuse_results, record_id
from tools.adaptive_retrieval import HopDecision, adaptive_search


def hit(text, source='制度.md', **extra):
    result = {'text': text, 'metadata': {'source': source}, 'score': .03, 'channels': ['bm25'], **extra}
    result['chunk_id'] = record_id(result)
    return result


class AdaptiveRetrievalTests(unittest.TestCase):
    def search(self, query, retrieve, review=None, **kwargs):
        serial = []
        def register(item, query, hop):
            serial.append(item)
            return f'S{len(serial)}'
        reviewer = review or AsyncMock(return_value=HopDecision(sufficient=True, supporting_ids=['S1']))
        return asyncio.run(adaptive_search(query, retrieve, register, lambda: True, review=reviewer, **kwargs))

    def test_simple_question_has_one_retrieval_and_no_review_cost(self):
        retrieve = AsyncMock(return_value=[hit('请假需提前三天申请。')])
        review = AsyncMock()
        result = self.search('请假提前几天申请？', retrieve, review)
        self.assertEqual(result['stop_reason'], 'single_retrieval')
        self.assertFalse(result['coverage_verified'])
        self.assertEqual(retrieve.await_count, 1)
        review.assert_not_awaited()

    def test_cross_document_query_follows_newly_found_title(self):
        retrieve = AsyncMock(side_effect=[[hit('采购审批额度参见《授权额度表》。')],
                                          [hit('超过十万元由总经理审批。', '授权额度表.md')]])
        review = AsyncMock(side_effect=[HopDecision(missing=['审批人'], next_query='授权额度表 十万元 审批人'),
                                       HopDecision(sufficient=True, supporting_ids=['S1', 'S2'])])
        result = self.search('采购应由谁审批？', retrieve, review)
        self.assertEqual(result['stop_reason'], 'evidence_sufficient')
        self.assertEqual(len(result['hops']), 2)
        self.assertEqual({r['metadata']['source'] for r in result['evidence']}, {'制度.md', '授权额度表.md'})
        self.assertIn('授权额度表', retrieve.await_args_list[1].args[0])

    def test_reference_in_simple_question_can_escalate_to_multihop(self):
        retrieve = AsyncMock(side_effect=[[hit('标准参考《假期规定》。')], [hit('共五天。', '假期规定.md')]])
        review = AsyncMock(side_effect=[HopDecision(missing=['天数'], next_query='假期规定 天数'),
                                       HopDecision(sufficient=True, supporting_ids=['S2'])])
        self.assertEqual(self.search('假期有几天？', retrieve, review)['strategy'], 'multi')

    def test_no_hit_does_not_call_review_or_claim_coverage(self):
        review = AsyncMock()
        result = self.search('跨文档比较制度', AsyncMock(return_value=[]), review)
        self.assertEqual(result['stop_reason'], 'no_hits')
        self.assertFalse(result['coverage_verified'])
        review.assert_not_awaited()

    def test_repeated_queries_and_identical_evidence_stop(self):
        for next_query, expected in [('比较制度', 'repeated_query'), ('换一个词', 'no_new_evidence')]:
            with self.subTest(expected=expected):
                review = AsyncMock(return_value=HopDecision(missing=['例外条件'], next_query=next_query))
                result = self.search('比较制度', AsyncMock(return_value=[hit('同一原文')]), review)
                self.assertEqual(result['stop_reason'], expected)
                self.assertFalse(result['coverage_verified'])

    def test_max_hops_and_invalid_supporting_ids_do_not_pass(self):
        review = AsyncMock(return_value=HopDecision(sufficient=True, supporting_ids=['S999'], next_query='继续追查'))
        result = self.search('比较制度', AsyncMock(return_value=[hit('原文')]), review, max_hops=1)
        self.assertEqual(result['stop_reason'], 'max_hops')
        self.assertFalse(result['coverage_verified'])
        self.assertTrue(result['missing'])

    def test_controller_failure_retains_evidence_and_uncertainty(self):
        result = self.search('比较制度', AsyncMock(return_value=[hit('有效原文')]), AsyncMock(side_effect=TimeoutError()))
        self.assertEqual(result['stop_reason'], 'controller_unavailable')
        self.assertEqual(len(result['evidence']), 1)
        self.assertFalse(result['coverage_verified'])

    def test_controller_timeout_is_bounded(self):
        async def slow(*args):
            await asyncio.sleep(1)
        result = self.search('比较制度', AsyncMock(return_value=[hit('原文')]), slow, timeout=.001)
        self.assertEqual(result['stop_reason'], 'controller_unavailable')

    def test_budget_counts_each_hop_not_each_tool_wrapper(self):
        budget = iter([True, False])
        retrieve = AsyncMock(return_value=[hit('原文')])
        result = asyncio.run(adaptive_search('比较制度', retrieve, lambda *args: 'S1', lambda: next(budget),
            review=AsyncMock(return_value=HopDecision(missing=['关联资料'], next_query='新线索'))))
        self.assertEqual(result['stop_reason'], 'budget_exhausted')
        self.assertEqual(retrieve.await_count, 1)

    def test_index_versions_cannot_be_mixed_across_hops(self):
        retrieve = AsyncMock(side_effect=[[hit('A', index_version='one')], [hit('B', index_version='two')]])
        review = AsyncMock(return_value=HopDecision(missing=['后续'], next_query='新查询'))
        result = self.search('比较制度', retrieve, review)
        self.assertEqual(result['stop_reason'], 'index_changed')
        self.assertEqual(len(result['evidence']), 1)

    def test_context_budget_prevents_unbounded_accumulation(self):
        result = self.search('比较制度', AsyncMock(return_value=[hit('正文' * 100)]), char_budget=10)
        self.assertEqual(result['stop_reason'], 'context_budget')
        self.assertFalse(result['coverage_verified'])

    def test_single_mode_never_escalates_for_document_references(self):
        review = AsyncMock()
        result = self.search('比较制度', AsyncMock(return_value=[hit('参见《额度表》。')]), review, mode='single')
        self.assertEqual(result['strategy'], 'single')
        review.assert_not_awaited()

    def test_next_hop_skips_old_top_candidate_and_selects_new_evidence(self):
        first, second = hit('第一份资料'), hit('第二份资料', '关联规定.md')
        retrieve = AsyncMock(side_effect=[[first], [first, second]])
        review = AsyncMock(side_effect=[HopDecision(missing=['后续资料'], next_query='关联规定'),
                                       HopDecision(sufficient=True, supporting_ids=['S1', 'S2'])])
        result = self.search('比较制度', retrieve, review, k=1)
        self.assertEqual(result['stop_reason'], 'evidence_sufficient')
        self.assertEqual(len(result['evidence']), 2)

    def test_question_named_document_cannot_be_ignored_by_controller(self):
        first, named = hit('相关回答', '其他规定.md'), hit('必要原文', '采购制度.md')
        retrieve = AsyncMock(side_effect=[[first, named], [named]])
        review = AsyncMock(side_effect=[HopDecision(sufficient=True, supporting_ids=['S1']),
                                       HopDecision(sufficient=True, supporting_ids=['S1', 'S2'])])
        result = self.search('根据采购制度判断适用条件', retrieve, review, k=1)
        self.assertEqual(len(result['hops']), 2)
        self.assertIn('采购制度', retrieve.await_args_list[1].args[0])


class ParsingAndRankingTests(unittest.TestCase):
    def test_word_tables_and_heading_locations_are_not_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '制度.docx'
            doc = WordDocument()
            doc.add_heading('审批权限', level=1)
            doc.add_paragraph('采购金额按下表执行。')
            table = doc.add_table(rows=2, cols=2)
            table.cell(0, 0).text, table.cell(0, 1).text = '金额', '审批人'
            table.cell(1, 0).text, table.cell(1, 1).text = '十万元以上', '总经理'
            doc.save(path)
            units = parse_document(path)
            tables = [unit for unit in units if unit.metadata.get('content_type') == 'table']
            self.assertIn('总经理', tables[0].page_content)
            self.assertEqual(tables[0].metadata['section'], '审批权限')
            self.assertEqual(len(tables[0].metadata['revision']), 64)

    def test_pdf_keeps_page_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'policy.pdf'
            canvas = Canvas(str(path))
            canvas.drawString(50, 500, 'page one'); canvas.showPage()
            canvas.drawString(50, 500, 'page two'); canvas.save()
            self.assertEqual([unit.metadata['page'] for unit in parse_document(path)], [1, 2])

    def test_lexical_recall_finds_exact_rule_and_filters_unrelated(self):
        records = [hit('采购关联交易必须额外审核。', '关联交易制度.md'), hit('公园花卉养护指南。', '园艺.md')]
        lexical = LexicalIndex(records).search('关联交易 审核')
        result = fuse_results('关联交易 审核', [], lexical)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['metadata']['source'], '关联交易制度.md')
        self.assertEqual(LexicalIndex(records).search('火箭发射'), [])

    def test_fusion_deduplicates_and_keeps_semantic_only_matches(self):
        item = hit('每位员工享有五天年休假。')
        result = fuse_results('annual leave', [{**item, 'vector_distance': .2}], [], k=4)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['channels'], ['vector'])
        result = fuse_results('年休假', [{**item, 'vector_distance': .2}], [item])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['channels'], ['vector', 'bm25'])
        self.assertEqual(fuse_results('火箭', [{**item, 'vector_distance': 1.9}], []), [])
