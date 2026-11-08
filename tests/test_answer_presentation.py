"""User answers keep evidence and uncertainty without exposing execution rules."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from casework.requirements import TaskRequirement
from casework.workflow import DepartmentRequest, render
from casework.verification import verify


def date_fixture():
    row = dict(case_id='AI-2026-017', client_case_id='TEST-017', owner_name='甲', stage='客户审核',
               received_date=None, draft_due_date='2026-11-07', client_review_due_date='2026-11-14',
               filing_due_date='2026-11-21', actual_filed_date=None, filing_verified='未核实',
               conflicting_candidates={})
    columns = ['所编号', '客户案号', '代理师', '案件状态', '实际收案日期', '初稿期限／所限',
               '客户送审期限', '向局提交期限', '实际交局日期', '交局已核实']
    keys = ['case_id', 'client_case_id', 'owner_name', 'stage', 'received_date', 'draft_due_date',
            'client_review_due_date', 'filing_due_date', 'actual_filed_date']
    proof = {'id': 'S1', 'type': 'database', 'columns': columns,
             'rows': [[row[k] for k in keys] + [False]]}
    return {'rows': [row], 'as_of': '2026-11-03', 'evidence_id': 'S1', 'has_more': False}, [proof]


class AnswerPresentationTests(unittest.TestCase):
    def request(self, quote='AI-2026-017实际是什么时候收案的？要用于月报。'):
        return DepartmentRequest(operation='cases', query='AI-2026-017',
            requirements=[TaskRequirement(kind='case_deadlines', quote=quote)])

    def test_missing_date_is_direct_and_remains_evidence_bound(self):
        data, evidence = date_fixture()
        report = render(data, self.request())
        self.assertEqual(report, 'AI-2026-017 的实际收案日期：未记录。[S1]')
        self.assertEqual(verify(self.request(), data, evidence, report)['status'], 'passed')
        bad = report.replace('未记录', '2026-07-15')
        self.assertEqual(verify(self.request(), data, evidence, bad)['status'], 'failed')

    def test_known_date_and_unverified_filing_are_not_replaced_by_generic_caveats(self):
        data, evidence = date_fixture()
        data['rows'][0]['received_date'] = evidence[0]['rows'][0][4] = '2026-10-02'
        data['rows'][0]['actual_filed_date'] = evidence[0]['rows'][0][8] = '2026-11-01'
        request = self.request('查实际收案日期和实际交局日期')
        report = render(data, request)
        self.assertIn('实际收案日期：2026-10-02。[S1]', report)
        self.assertIn('实际交局日期：未核实。[S1]', report)
        self.assertNotIn('实际交局日期：2026-11-01', report)
        self.assertEqual(verify(request, data, evidence, report)['status'], 'passed')
        from api.query_result import build_query_result
        for state in ['未记录', '存在冲突', '尚未确认', '暂无法确定']:
            with self.subTest(state=state):
                full='甲案日期：2026-10-02。[S1]\n乙案日期：'+state+'。[S1]'
                projected = asyncio.run(build_query_result('核对甲乙两案日期', full, evidence))
                self.assertEqual(projected['status'],'ready')
                self.assertEqual(projected['content'],full)

    def test_date_conflict_keeps_both_candidates_and_their_source(self):
        data, evidence = date_fixture()
        row = data['rows'][0]
        candidates = [{'value': '2026-10-01', 'path': '记录甲.xlsx', 'location': 'B2'},
                      {'value': '2026-10-03', 'path': '记录乙.xlsx', 'location': 'B2'}]
        row.update(conflicting_candidates={'received_date': candidates}, conflict_evidence_id='S2')
        evidence.append({'id': 'S2', 'type': 'database', 'columns': ['所编号', '字段', '候选值', '原件', '定位'],
                         'rows': [[row['case_id'], '实际收案日期', c['value'], c['path'], c['location']] for c in candidates]})
        report = render(data, self.request())
        self.assertIn('实际收案日期：存在冲突', report)
        self.assertNotIn('实际收案日期：未记录', report)
        for c in candidates:self.assertIn(c['value'], report)
        self.assertEqual(verify(self.request(), data, evidence, report)['status'], 'passed')

    def test_mixed_case_request_keeps_owner_and_deadlines(self):
        data, evidence = date_fixture()
        request = self.request()
        request.requirements.append(TaskRequirement(kind='case_owner', quote='还要承办人和期限'))
        report = render(data, request)
        self.assertIn('| 承办人 |', report)
        self.assertIn('2026-11-07', report)
        self.assertIn('实际收案日期：未记录', report)
        self.assertNotIn('需向承办人核对', report)
        self.assertNotIn('不能通过会议时间', report)
        self.assertEqual(verify(request, data, evidence, report)['status'], 'passed')
        mixed_dates = self.request('查实际收案日期和计划提交节点')
        self.assertIn('2026-11-21',render(data,mixed_dates))

    def test_fallback_diagnostics_do_not_become_user_instructions(self):
        from tools.fallback import build_fallback
        with patch('tools.fallback.evidence.snapshot', return_value=[]):
            result = build_fallback(thread_id='test', query='查日期', best_report='',
                issues=['CONTEXT_LIMIT 内部核验指令：不准用会议时间补位'],
                evidence_gaps=[{'missing': '内部调度：改查文件并增加轮次'}])
        self.assertIn('未能完成', result)
        for text in ['CONTEXT_LIMIT', '内部调度', '调整最多处理轮次', '补充更明确的指标', '会议时间补位']:
            self.assertNotIn(text, result)

    def test_api_hides_fallback_marker_but_preserves_degraded_status(self):
        from api import server
        result = '现有内容。\n\n## 兜底结果说明\n以下结果尚未核对通过。\n\n【未达标说明】'
        report = AsyncMock()
        with patch.object(server, 'run_deep_agent', AsyncMock(return_value=result)), \
             patch.object(server.storage, 'get_run', return_value={}), \
             patch.object(server.evidence, 'snapshot', return_value=[]), \
             patch.object(server, 'build_query_result', AsyncMock(return_value={})), \
             patch.object(server.monitor, 'report', report):
            asyncio.run(server._execute_task('presentation-test', '查日期'))
        data = next(c.args[3] for c in report.await_args_list if c.args[1] == 'task_result')
        self.assertTrue(data['degraded'])
        self.assertTrue(data['fallback'])
        self.assertNotIn('【未达标说明】', data['result'])
        self.assertNotIn('兜底', data['result'])
        self.assertEqual(data['result_assessment']['delivery']['status'], 'partial')
        self.assertEqual(data['result_assessment']['facts']['status'], 'unverified')
        self.assertEqual(data['result_assessment']['completeness']['status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
