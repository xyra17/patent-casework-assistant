"""Inject failures at real program boundaries; no paid calls or external DB."""
import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock,MagicMock,patch
from tests import test_regressions as orchestration
from tests import test_case_record_execution as records
from tests.test_content_verification import review_fixture,review_model,case_review_payload,passed_selection_gate
from tools.case_records import calculate,check_case_answer
from tools.reflect_check import hard_check
from agent import main_agent
from api import context,evidence,storage


class EvidenceFaultTests(unittest.TestCase):
    fixture=records.CaseRecordExecution.fixture
    report=records.CaseRecordExecution.report

    def test_missing_original_never_confirms_zero(self):
        items,params=self.fixture();items[0]['content']='[]'
        with self.assertRaisesRegex(ValueError,'没有技术原文'):calculate(items,**params)

    def test_missing_join_never_confirms_zero(self):
        items,params=self.fixture();items[1]['content']='[]'
        with self.assertRaisesRegex(ValueError,'缺少案件信息关联'):calculate(items,**params)

    def test_conflicting_actual_date_not_replaced_by_plan_or_stage(self):
        items,params=self.fixture();rows=json.loads(items[1]['content']);duplicate=copy.deepcopy(rows[0]);duplicate['actual_filed_date']='2031-03-31';duplicate['stage']='已交局';duplicate['filing_due_date']='2031-04-01';rows.append(duplicate);items[1]['content']=json.dumps(rows)
        calc=calculate(items,**params)
        self.assertEqual(calc['answer']['filed_count'],0)
        self.assertIn('actual_filed_date',calc['joined_rows'][0]['conflict_fields'])

    def test_conflicting_filter_field_requires_more_evidence(self):
        items,params=self.fixture();rows=json.loads(items[1]['content']);rows[0]['client_name']='甲';rows.append({**rows[0],'client_name':'乙'});items[1]['content']=json.dumps(rows)
        with self.assertRaisesRegex(ValueError,'筛选字段缺失或冲突'):calculate(items,**params,ledger_filters={'client_name':'甲'})

    def test_changed_source_invalidates_previously_valid_calculation(self):
        items,params=self.fixture();result=self.report(items,calculate(items,**params))
        self.assertEqual(check_case_answer(result)['status'],'passed')
        rows=json.loads(result['evidence'][1]['content']);rows[0]['received_date']='2031-02-28';result['evidence'][1]['content']=json.dumps(rows)
        self.assertEqual(check_case_answer(result)['status'],'failed')

    def test_removed_or_fabricated_citation_is_rejected(self):
        items,params=self.fixture();result=self.report(items,calculate(items,**params))
        result['evidence']=result['evidence'][1:]
        self.assertEqual(check_case_answer(result)['status'],'failed')
        self.assertTrue(any('来源' in issue for issue in hard_check(result)))


class ExecutionFaultTests(unittest.TestCase):
    setUp=orchestration.RegressionTests.setUp
    tearDown=orchestration.RegressionTests.tearDown

    def summary(self,thread):
        return next(e['data'] for e in storage.get_run(thread)['events'] if e['event']=='quality_summary')

    def test_missing_evidence_stops_without_review_or_confirmation(self):
        storage.create_run('fault-missing','只返回JSON对象，字段为history_ids、owners和evidence_ids。','fault',False)
        draft={'report':'{"history_ids":["X1"],"owners":["甲"],"evidence_ids":["S99"]}',
               'evidence':[],'called_sources':[],'require_pdf':False,'pdf_produced':False}
        with patch.object(main_agent,'main_agent_run',AsyncMock(return_value=draft)), \
             patch.object(main_agent,'soft_review',AsyncMock()) as review:
            answer=asyncio.run(main_agent.run_deep_agent('只返回JSON对象，字段为history_ids、owners和evidence_ids。','fault-missing',max_rounds=3))
        self.assertFalse(self.summary('fault-missing')['passed']);self.assertIn('未达标',answer);review.assert_not_called()

    def test_hanging_execution_is_cancelled_and_context_cleared(self):
        storage.create_run('fault-hung','仅用附件回答，不联网。','fault',False)
        cancelled=[]
        async def hung(*args,**kwargs):
            try:await asyncio.Event().wait()
            finally:cancelled.append(True)
        with patch.object(main_agent,'main_agent_run',side_effect=hung),patch.dict('os.environ',{'TASK_TIMEOUT':'0.05'}):
            with self.assertRaises(TimeoutError):asyncio.run(main_agent.run_deep_agent('仅用附件回答，不联网。','fault-hung',max_rounds=1))
        self.assertEqual(cancelled,[True]);self.assertIsNone(context.get_thread_id())
        self.assertFalse(any(e['event']=='quality_summary' and e['data'].get('passed') for e in storage.get_run('fault-hung')['events']))

    def test_review_outage_stops_then_explicit_rerun_can_pass(self):
        for thread in ('fault-review-down','fault-review-recovered'):storage.create_run(thread,'仅回答当前问题。','fault',False)
        draft={'report':'已核对本次问题。','evidence':[],'called_sources':[],'require_pdf':False,'pdf_produced':False}
        async def unavailable(result):result.update(soft_review_status='skipped',soft_review_detail='注入审查超时');return None
        async def recovered(result):result.update(soft_review_status='passed');return None
        with patch.object(main_agent,'main_agent_run',AsyncMock(return_value=copy.deepcopy(draft))),patch.object(main_agent,'soft_review',side_effect=unavailable):
            answer=asyncio.run(main_agent.run_deep_agent('仅回答当前问题。','fault-review-down',max_rounds=2))
        self.assertEqual(self.summary('fault-review-down')['stop_reason'],'review_unavailable');self.assertIn('未达标',answer)
        with patch.object(main_agent,'main_agent_run',AsyncMock(return_value=copy.deepcopy(draft))),patch.object(main_agent,'soft_review',side_effect=recovered):
            asyncio.run(main_agent.run_deep_agent('仅回答当前问题。','fault-review-recovered',max_rounds=2))
        self.assertTrue(self.summary('fault-review-recovered')['passed'])


class ReviewFaultTests(unittest.TestCase):
    def test_omitted_matching_case_cannot_hide_missing_ledger_from_full_review(self):
        from tools.case_relevance import CaseRelevanceDecision,DocumentRelevance
        from tools.content_verification import CaseContentReviewFindings
        items,params=records.CaseRecordExecution.fixture(self)
        rows=json.loads(items[1]['content']);items[1]['content']=json.dumps(rows[1:]);params['matches']=[]
        result=records.CaseRecordExecution.report(self,items,calculate(items,**params))
        docs=json.loads(items[0]['content'])
        decision=CaseRelevanceDecision(documents=[DocumentRelevance(case_id=d['case_id'],relevant=d['case_id']=='X1',
            quote=d['text'] if d['case_id']=='X1' else '',detail='逐案核对实质技术关系') for d in docs])
        model=MagicMock();semantic=AsyncMock();general=AsyncMock()
        semantic.ainvoke.return_value={'parsed':decision}
        _,good=review_fixture();good['checks'][0]['task_quote']=result['query']
        general.ainvoke.return_value={'parsed':case_review_payload(result,good)}
        model.with_structured_output.side_effect=lambda schema,**kwargs:semantic if schema is CaseRelevanceDecision else general
        with patch.object(main_agent,'get_llm',return_value=model):asyncio.run(main_agent.soft_review(result))
        self.assertEqual(semantic.ainvoke.await_count,1)
        general.ainvoke.assert_not_called()
        self.assertEqual(result['verification']['status'],'unverified')
        self.assertIn('缺少案件信息关联',result['soft_review_detail'])

    def selection_models(self,result):
        from tools.case_relevance import CaseRelevanceDecision,DocumentRelevance
        docs=json.loads(result['evidence'][0]['content'])
        decision=CaseRelevanceDecision(documents=[DocumentRelevance(case_id=d['case_id'],relevant=d['case_id']=='X1',
            quote=d['text'] if d['case_id']=='X1' else '',detail='逐案核对机制') for d in docs])
        model=MagicMock();semantic=AsyncMock();general=AsyncMock()
        semantic.ainvoke.return_value={'parsed':decision}
        _,good=review_fixture();good['checks'][0]['task_quote']=result['query']
        general.ainvoke.return_value={'parsed':case_review_payload(result,good)}
        model.with_structured_output.side_effect=lambda schema,**kwargs:semantic if schema is CaseRelevanceDecision else general
        return model,semantic,general,good

    def test_omitted_match_cannot_hide_unknown_or_conflicting_filter(self):
        for conflict in (False,True):
            with self.subTest(conflict=conflict):
                items,params=records.CaseRecordExecution.fixture(self)
                rows=json.loads(items[1]['content'])
                if conflict:rows[0]['client_name']='甲';rows.append({**rows[0],'client_name':'乙'})
                items[1]['content']=json.dumps(rows);params['matches']=[]
                result=records.CaseRecordExecution.report(self,items,calculate(items,**params,ledger_filters={'client_name':'甲'}))
                result['query']+='仅统计案件信息客户为甲的案件。'
                self.assertEqual(check_case_answer(result)['status'],'passed')
                model,semantic,general,_=self.selection_models(result)
                with patch.object(main_agent,'get_llm',return_value=model):asyncio.run(main_agent.soft_review(result))
                general.ainvoke.assert_not_called()
                self.assertEqual(result['verification']['status'],'unverified')
                self.assertIn('筛选字段缺失或冲突',result['soft_review_detail'])

    def test_irrelevant_case_without_ledger_does_not_block_full_review(self):
        items,params=records.CaseRecordExecution.fixture(self)
        items[1]['content']=json.dumps(json.loads(items[1]['content'])[:1])
        result=records.CaseRecordExecution.report(self,items,calculate(items,**params))
        model,semantic,general,_=self.selection_models(result)
        with patch.object(main_agent,'get_llm',return_value=model):asyncio.run(main_agent.soft_review(result))
        self.assertEqual(semantic.ainvoke.await_count,1);self.assertEqual(general.ainvoke.await_count,1)
        self.assertEqual(result['case_selection_verification']['status'],'passed')
        self.assertEqual(result['verification']['status'],'passed')
        self.assertEqual(result['soft_review_mode'],'structured_content')

    def test_selection_gate_never_replaces_review_of_additional_deliverable(self):
        items,params=records.CaseRecordExecution.fixture(self)
        result=records.CaseRecordExecution.report(self,items,calculate(items,**params))
        result['query']+='另需逐案承办映射表。'
        model,semantic,general,good=self.selection_models(result)
        good['checks'][0].update(status='failed',answer_quote=result['report'],reason='none',
            detail='缺少用户另要求的逐案承办映射表。',action='revise_answer',suggestion='补充逐案承办映射表。')
        general.ainvoke.return_value={'parsed':case_review_payload(result,good)}
        with patch.object(main_agent,'get_llm',return_value=model):issue=asyncio.run(main_agent.soft_review(result))
        self.assertEqual(result['case_selection_verification']['status'],'passed')
        self.assertEqual(result['verification']['status'],'failed');self.assertIn('映射表',issue)
        self.assertIn('另需逐案承办映射表',general.ainvoke.call_args.args[0][1].content)

    def test_unavailable_selection_cannot_reuse_stale_pass(self):
        items,params=records.CaseRecordExecution.fixture(self)
        result=records.CaseRecordExecution.report(self,items,calculate(items,**params))
        result['case_selection_verification']={'status':'passed'}
        model,semantic,general,_=self.selection_models(result);semantic.ainvoke.side_effect=TimeoutError('injected')
        with patch.object(main_agent,'get_llm',return_value=model):asyncio.run(main_agent.soft_review(result))
        general.ainvoke.assert_not_called();self.assertEqual(result['verification']['status'],'unverified')
        self.assertNotEqual(result.get('case_selection_verification',{}).get('status'),'passed')

    def test_review_timeout_and_cancellation_never_fabricate_pass(self):
        result,payload=review_fixture();model,reviewer=review_model(payload)
        reviewer.ainvoke.side_effect=TimeoutError('injected model timeout')
        with patch.object(main_agent,'get_llm',return_value=model):asyncio.run(main_agent.soft_review(result))
        self.assertEqual(result['verification']['status'],'unverified')
        reviewer.ainvoke.side_effect=asyncio.CancelledError()
        with patch.object(main_agent,'get_llm',return_value=model),self.assertRaises(asyncio.CancelledError):asyncio.run(main_agent.soft_review(result))

    def test_malformed_case_reviewer_stops_after_bounded_attempts(self):
        result,payload=review_fixture();model,reviewer=review_model(payload)
        reviewer.ainvoke.return_value={'parsed':{'checks':[]}}
        with patch('tools.case_relevance.review_case_answer',side_effect=passed_selection_gate),patch('tools.case_records.check_case_answer',return_value={'status':'passed','calculation':{}}),patch.object(main_agent,'get_llm',return_value=model):
            asyncio.run(main_agent.soft_review(result))
        self.assertEqual(reviewer.ainvoke.await_count,2);self.assertEqual(result['verification']['status'],'unverified')

    def test_nonexistent_review_anchor_cannot_close_real_issue(self):
        result,payload=review_fixture();data=case_review_payload(result,payload);data['checks'][0]['task_anchor_id']='T-forged'
        model,reviewer=review_model(payload);reviewer.ainvoke.return_value={'parsed':data}
        with patch('tools.case_relevance.review_case_answer',side_effect=passed_selection_gate),patch('tools.case_records.check_case_answer',return_value={'status':'passed','calculation':{}}),patch.object(main_agent,'get_llm',return_value=model):
            asyncio.run(main_agent.soft_review(result))
        self.assertEqual(result['verification']['status'],'unverified')
