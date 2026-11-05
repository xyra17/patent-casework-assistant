import unittest
import asyncio
from unittest.mock import AsyncMock, patch

from tools.content_verification import ContentReviewDecision, finalize_review
from tools.review_tracking import update_issues, pending_followups, finish_issues
from test_content_verification import review_fixture


class ReviewTrackingTests(unittest.TestCase):
    def first_round(self, action='revise_answer'):
        result, payload = review_fixture()
        payload['verdict'] = 'failed'
        payload['checks'][1].update(status='unverified', reason='missing_evidence',
            answer_quote='已扣除退货20元', action=action, suggestion='核对退货口径')
        result['verification'] = finalize_review(ContentReviewDecision(**payload), result, {'S1'})
        return result, update_issues([], result, [], 1)

    def test_reasons_distinguish_missing_evidence_from_other_unknowns(self):
        for reason in ('missing_evidence', 'ambiguous_requirement', 'verification_unavailable', 'other', 'none'):
            result, payload = review_fixture()
            payload['verdict'] = 'failed'
            payload['checks'][1].update(status='unverified', reason=reason,
                answer_quote='已扣除退货20元', action='clarify', suggestion='请明确核对条件')
            if reason == 'none':
                with self.assertRaises(ValueError):
                    finalize_review(ContentReviewDecision(**payload), result, {'S1'})
            else:
                self.assertEqual(finalize_review(ContentReviewDecision(**payload), result, {'S1'})['checks'][1]['reason'], reason)
        result, payload = review_fixture()
        payload['checks'][0]['reason'] = 'missing_evidence'
        with self.assertRaises(ValueError):
            finalize_review(ContentReviewDecision(**payload), result, {'S1'})

    def test_followup_requires_explicit_unique_matching_reference(self):
        _, ledger = self.first_round()
        for ids, criterion in (([], 'evidence_support'), (['R9-V2'], 'evidence_support'),
                                (['R1-V2', 'R1-V2'], 'evidence_support'), (['R1-V2'], 'consistency')):
            result, payload = review_fixture()
            result['review_followups'] = pending_followups(ledger)
            index = next(i for i, item in enumerate(payload['checks']) if item['criterion'] == criterion)
            payload['checks'][index]['followup_ids'] = ids
            with self.subTest(ids=ids, criterion=criterion), self.assertRaises(ValueError):
                finalize_review(ContentReviewDecision(**payload), result, {'S1'})

    def test_same_task_dimension_cannot_close_another_requirement(self):
        result, payload = review_fixture()
        result['review_followups'] = [{'issue_id':'R1-V1', 'criterion':'task_completion', 'task_quote':'统计净销售额'}]
        payload['checks'][0]['followup_ids'] = ['R1-V1']
        with self.assertRaises(ValueError):
            finalize_review(ContentReviewDecision(**payload), result, {'S1'})

    def test_repair_is_linked_and_original_snapshot_stays_immutable(self):
        _, ledger = self.first_round()
        result, payload = review_fixture()
        result['report'] = '净销售额80元，退货20元已扣除。[S1]'
        result['review_followups'] = pending_followups(ledger)
        payload['checks'][1]['followup_ids'] = ['R1-V2']
        result['verification'] = finalize_review(ContentReviewDecision(**payload), result, {'S1'})
        updated = update_issues(ledger, result, [], 2)
        self.assertEqual(updated[0]['issue_id'], 'R1-V2')
        self.assertEqual(updated[0]['status'], 'resolved')
        self.assertEqual(updated[0]['history'][-1]['outcome'], 'corrected')
        self.assertEqual(len(ledger[0]['history']), 1)
        self.assertEqual(ledger[0]['status'], 'pending')

    def test_new_evidence_and_recheck_are_distinct(self):
        _, ledger = self.first_round('retrieve_evidence')
        for new_data, expected in ((False, 'rechecked'), (True, 'evidence_added')):
            result, payload = review_fixture()
            result['evidence'][0]['round'] = 2  # metadata alone must not count
            if new_data:
                result['evidence'][0]['rows'] = [[80, 20]]
            result['review_followups'] = pending_followups(ledger)
            payload['checks'][1]['followup_ids'] = ['R1-V2']
            result['verification'] = finalize_review(ContentReviewDecision(**payload), result, {'S1'})
            updated = update_issues(ledger, result, [], 2)
            self.assertEqual(updated[0]['history'][-1]['outcome'], expected)

    def test_renumbering_or_repeating_same_evidence_is_not_supplemental_retrieval(self):
        from tools.review_tracking import _evidence_hash
        original = [{'id':'S1', 'rows':[[80]], 'columns':['net_sales']}]
        repeated = [{**original[0], 'id':'S2', 'round':2}, {**original[0], 'id':'S3', 'round':2}]
        self.assertEqual(_evidence_hash(original), _evidence_hash(repeated))

    def test_unrelated_new_source_does_not_claim_evidence_was_added_for_this_issue(self):
        _, ledger = self.first_round('retrieve_evidence')
        result, payload = review_fixture()
        result['evidence'].append({'id':'S2', 'snippet':'无关的审批制度'})
        result['review_followups'] = pending_followups(ledger)
        payload['checks'][1]['followup_ids'] = ['R1-V2']
        result['verification'] = finalize_review(ContentReviewDecision(**payload), result, {'S1', 'S2'})
        updated = update_issues(ledger, result, [], 2)
        self.assertEqual(updated[0]['history'][-1]['outcome'], 'rechecked')

    def test_missing_or_unavailable_review_never_closes_issue(self):
        result, ledger = self.first_round()
        result['verification'] = {'status':'unverified', 'checks':[], 'detail':'审查超时'}
        updated = update_issues(ledger, result, [], 2)
        self.assertEqual(updated[0]['status'], 'pending')
        self.assertEqual(updated[0]['history'][-1]['outcome'], 'unverified')
        self.assertEqual(updated[1]['kind'], 'review_service')
        final = finish_issues(updated, 'review_unavailable')
        self.assertTrue(all(item['stop_reason'] == 'review_unavailable' for item in final))
        self.assertNotIn('stop_reason', updated[0])

    def test_still_pending_keeps_identity_and_stop_reason(self):
        result, ledger = self.first_round()
        result['verification']['checks'][1]['followup_ids'] = ['R1-V2']
        updated = update_issues(ledger, result, [], 2)
        self.assertEqual(len(updated), 1)
        self.assertEqual([event['round'] for event in updated[0]['history']], [1, 2])
        self.assertEqual(finish_issues(updated, 'max_rounds')[0]['stop_reason'], 'max_rounds')

    def test_program_errors_only_close_when_program_recheck_clears_them(self):
        result, _ = review_fixture()
        result['verification'] = {'status':'passed','checks':[]}
        ledger = update_issues([], result, ['统计值与SQL不一致'], 1)
        updated = update_issues(ledger, result, ['统计值与SQL不一致'], 2)
        self.assertEqual(updated[0]['status'], 'pending')
        self.assertEqual(finish_issues(updated, 'max_rounds')[0]['status'], 'pending')
        updated = update_issues(updated, result, [], 3)
        self.assertEqual(updated[0]['status'], 'resolved')
        self.assertIn('程序重新检查', updated[0]['history'][-1]['detail'])

    def test_returning_an_earlier_draft_does_not_claim_later_repairs_apply(self):
        result, _ = review_fixture()
        ledger = update_issues([], result, ['数字不一致'], 1)
        ledger = update_issues(ledger, result, [], 2)
        self.assertTrue(finish_issues(ledger, 'max_rounds', 1)[0]['returned_report_unverified'])
        self.assertFalse(finish_issues(ledger, 'passed', 2)[0]['returned_report_unverified'])

    def test_interrupt_saves_pending_items_with_specific_stop_reason(self):
        from api import server
        _, ledger = self.first_round()
        events = [{'event':'reflection', 'data':{'round':1, 'max_rounds':3, 'issue_lifecycle':ledger}}]
        for reason in ('timeout', 'user_cancel', 'execution_error'):
            with patch.object(server.storage, 'get_run', return_value={'events':events}), \
                    patch.object(server.monitor, 'report', new_callable=AsyncMock) as report:
                asyncio.run(server._finish_interrupted_review('test', reason, 3))
            data = report.await_args.args[3]
            self.assertEqual(data['stop_reason'], reason)
            self.assertEqual(data['issue_lifecycle'][0]['stop_reason'], reason)
            self.assertEqual(data['issue_lifecycle'][0]['status'], 'pending')
            self.assertFalse(data['passed'])
        events.append({'event':'quality_summary', 'data':{}})
        with patch.object(server.storage, 'get_run', return_value={'events':events}), \
                patch.object(server.monitor, 'report', new_callable=AsyncMock) as report:
            asyncio.run(server._finish_interrupted_review('test', 'timeout', 3))
        report.assert_not_awaited()
