import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage
from agent import main_agent
from tools.content_verification import (CRITERIA, ContentReviewDecision, finalize_review,
                                        review_issues, targeted_correction, visible_evidence_ids, review_anchor_index,
                                        CaseContentReviewFindings)
from tools.reflect_check import hard_check


def review_fixture():
    result = {'query': '统计净销售额，说明退货口径', 'report': '净销售额80元，已扣除退货20元。[S1]',
              'required_sources': ['db_query'], 'require_report': False,
              'evidence': [{'id': 'S1', 'type': 'database', 'columns': ['net_sales'], 'rows': [[80]]}]}
    checks = [{'criterion': key, 'status': 'passed', 'detail': '按本次要求和已提供资料核对。',
               'task_quote': result['query'] if key == 'task_completion' else '',
               'evidence_ids': ['S1'] if key == 'evidence_support' else []}
              for key in CRITERIA]
    return result, {'verdict': 'passed', 'checks': checks}


def review_model(payload=None):
    if payload:
        payload=copy.deepcopy(payload)
        query=next(row['task_quote'] for row in payload['checks'] if row.get('task_quote'))
        task_id=review_anchor_index({'query':query,'report':''})['task'][0]['id']
        for row in payload['checks']:
            row.setdefault('task_anchor_id',task_id)
    model=MagicMock()
    reviewer=AsyncMock()
    model.with_structured_output.return_value=reviewer
    reviewer.ainvoke.return_value={'parsed':ContentReviewDecision(**payload) if payload else None,
                                  'parsing_error':None,'raw':AIMessage(content='')}
    return model,reviewer


async def passed_selection_gate(result,*args,**kwargs):
    """Isolate content-format unit tests whose program proof is a stub."""
    result['case_selection_verification']={'status':'passed'}


def case_review_payload(result, payload):
    anchors=review_anchor_index(result)
    rows=[]
    for row in payload['checks']:
        item={k:row[k] for k in ('criterion','detail','evidence_ids','followup_ids') if k in row}
        item['task_anchor_id']=anchors['task'][0]['id']
        item['answer_anchor_id']=anchors['answer'][0]['id'] if row.get('answer_quote') else ''
        item['issue']=({k:row.get(k,'none' if k=='reason' else '') for k in ('action','suggestion','reason')}
                       | {'state':row['status']}) if row['status'] in ('failed','unverified') else None
        rows.append(item)
    return {'checks':rows}


class ContentVerificationTests(unittest.TestCase):
    def test_case_review_projection_keeps_all_candidates_and_filter_fields(self):
        from tools.review_evidence import project_case_review_evidence, pack_review_evidence
        docs={'id':'S1','content_complete':True,'content':json.dumps([
            {'case_id':'A','text':'采用目标机制'},{'case_id':'B','text':'另一份待审技术原文'}])}
        ledger={'id':'S2','content_complete':True,'content':json.dumps([
            {'case_id':'A','owner_name':'甲','client_name':'客户一'},
            {'case_id':'B','owner_name':'乙','client_name':'客户二'},
            {'case_id':'UNSUPPORTED','owner_name':'丙','filing_verified':True}]),
            'rows':[['UNSUPPORTED']], 'columns':['case_id']}
        result={'evidence':[docs,ledger],'report':'{"evidence_ids":["S1","S2"]}'}
        original=copy.deepcopy(result)
        checked={'status':'passed','calculation':{'documents_evidence_id':'S1','ledger_evidence_id':'S2',
                  'matches':[{'case_id':'A'}]}}
        projected=project_case_review_evidence(result,checked)
        rows=json.loads(projected[1]['content'])
        self.assertEqual({r['case_id'] for r in rows},{'A','B'})
        self.assertEqual(rows[1]['client_name'],'客户二')
        self.assertEqual(projected[1]['review_projection']['original_ledger_rows'],3)
        self.assertFalse(projected[1]['content_complete'])
        self.assertNotIn('rows',projected[1])
        self.assertIn('technical_candidate_ledger_join',pack_review_evidence(projected,result['report']))
        self.assertEqual(result,original)
        self.assertIs(project_case_review_evidence(result,{'status':'failed'}),result['evidence'])

    def test_anchor_ids_restore_exact_json_whitespace_without_model_retyping(self):
        result,payload=review_fixture()
        result['report']='{"amount":80,"note":"核实"}'
        anchors=review_anchor_index(result)
        payload['checks'][0].update(task_quote='',task_anchor_id=anchors['task'][0]['id'])
        payload['checks'][1].update(answer_anchor_id=anchors['answer'][0]['id'])
        checks=finalize_review(ContentReviewDecision(**payload),result,{'S1'})['checks']
        self.assertEqual(checks[0]['task_quote'],result['query'])
        self.assertEqual(checks[1]['answer_quote'],result['report'])

    def test_stale_cross_type_and_conflicting_anchors_cannot_pass(self):
        for kind in ('stale','cross_type','conflict'):
            result,payload=review_fixture();anchors=review_anchor_index(result)
            row=payload['checks'][0];row['task_quote']='';row['task_anchor_id']=anchors['task'][0]['id']
            if kind=='stale':result['query']+='新要求'
            elif kind=='cross_type':row['task_anchor_id']=anchors['answer'][0]['id']
            else:row['task_quote']='用户没有说过'
            with self.assertRaises(ValueError):finalize_review(ContentReviewDecision(**payload),result,{'S1'})

    def test_valid_review_retains_exact_anchors_and_snapshot(self):
        result, payload = review_fixture()
        verified = finalize_review(ContentReviewDecision(**payload), result, {'S1'})
        self.assertEqual(verified['status'], 'passed')
        self.assertEqual(len(verified['checks']), 5)
        self.assertEqual(verified['checks'][1]['evidence_ids'], ['S1'])
        self.assertEqual(len(verified['report_sha256']), 64)
        self.assertEqual(review_issues(verified), [])

    def test_one_unverified_item_blocks_pass_and_drives_only_targeted_repair(self):
        result, payload = review_fixture()
        payload['verdict'] = 'failed'
        payload['checks'][1].update(status='unverified', reason='missing_evidence', task_quote='退货口径',
            answer_quote='已扣除退货20元', action='retrieve_evidence', suggestion='补查退货汇总的原始查询结果，无法核实则说明。')
        verified = finalize_review(ContentReviewDecision(**payload), result, {'S1'})
        self.assertEqual(verified['status'], 'failed')
        self.assertEqual(len(review_issues(verified)), 1)
        prompt = targeted_correction(verified)
        self.assertIn('V2', prompt)
        self.assertIn('已扣除退货20元', prompt)
        self.assertNotIn('"id": "V1"', prompt)
        self.assertIn('不得重复导入', prompt)
        self.assertIn('仅限本轮开放来源', prompt)

    def test_forged_anchors_and_verdicts_are_rejected(self):
        for mutation in ('task', 'answer', 'evidence', 'invisible', 'verdict', 'dimension', 'required'):
            with self.subTest(mutation=mutation):
                result, payload = review_fixture()
                visible = {'S1'}
                if mutation == 'task': payload['checks'][0]['task_quote'] = '用户未要求的额外报表'
                if mutation == 'answer': payload['checks'][1]['answer_quote'] = '不存在的正文'
                if mutation == 'evidence': payload['checks'][1]['evidence_ids'] = ['S999']
                if mutation == 'invisible': visible = set()
                if mutation == 'verdict': payload['verdict'] = 'failed'
                if mutation == 'dimension': payload['checks'][-1]['criterion'] = 'consistency'
                if mutation == 'required': payload['checks'][1]['status'] = 'not_required'
                with self.assertRaises(ValueError):
                    finalize_review(ContentReviewDecision(**payload), result, visible)

    def test_no_unsupported_correction_on_passing_item(self):
        result, payload = review_fixture()
        payload['checks'][0].update(action='revise_answer', suggestion='再写一份未要求的报告')
        with self.assertRaises(ValueError):
            finalize_review(ContentReviewDecision(**payload), result, {'S1'})

    def test_failure_requires_action_and_location(self):
        for missing in ('action', 'location'):
            result, payload = review_fixture()
            payload['verdict'] = 'failed'
            item = payload['checks'][1]
            item.update(status='failed', detail='数字不一致')
            if missing == 'action': item['answer_quote'] = '净销售额80元'
            else: item.update(action='revise_answer', suggestion='以实际查询结果改正数字')
            with self.assertRaises(ValueError):
                finalize_review(ContentReviewDecision(**payload), result, {'S1'})

    def test_simple_no_source_task_can_mark_evidence_not_required(self):
        result, payload = review_fixture()
        result.update(required_sources=[], evidence=[])
        payload['checks'][1].update(status='not_required', evidence_ids=[])
        self.assertEqual(finalize_review(ContentReviewDecision(**payload), result, set())['status'], 'passed')

    def test_packed_index_alone_does_not_prove_visible_evidence(self):
        self.assertEqual(visible_evidence_ids('{"available_ids":["S1","S2"]}\n{"id":"S1"}'), {'S1'})
        self.assertEqual(visible_evidence_ids('[证据 S2] 摘录（已截断）'), {'S2'})

    def test_program_rejects_fabricated_citations(self):
        result, _ = review_fixture()
        self.assertIn('引用了不存在的来源编号：S99', hard_check({**result, 'report': '答案[S99]'}))
        self.assertFalse(any('来源编号' in issue for issue in hard_check(result)))

    def test_structured_review_call_once_and_stores_real_checks(self):
        result, payload = review_fixture()
        model, reviewer = review_model(payload)
        with patch.object(main_agent, 'get_llm', return_value=model):
            issue = asyncio.run(main_agent.soft_review(result))
        self.assertIsNone(issue)
        self.assertEqual(reviewer.ainvoke.await_count, 1)
        model.with_structured_output.assert_called_once()
        self.assertTrue(issubclass(model.with_structured_output.call_args.args[0],ContentReviewDecision))
        self.assertEqual(model.with_structured_output.call_args.kwargs,{'method':'function_calling','include_raw':True})
        self.assertEqual(result['verification']['status'], 'passed')
        self.assertEqual(result['soft_review_mode'], 'structured_content')

    def test_general_review_receives_replayed_set_semantics_and_extra_constraints(self):
        result, payload = review_fixture()
        result['query'] += '，仅限甲客户；另需逐案对应表'
        payload['checks'][0]['task_quote'] = result['query']
        model, reviewer = review_model(payload)
        proof = {'status':'passed', 'calculation':{'mode':'history',
                 'answer':{'history_ids':['A','B'],'owners':['甲']},
                 'joined_rows':[{'case_id':'A','owner_name':'甲'},{'case_id':'B','owner_name':'甲'}]}}
        reviewer.ainvoke.return_value={'parsed':case_review_payload(result,payload)}
        with patch('tools.case_relevance.review_case_answer',side_effect=passed_selection_gate), \
             patch('tools.case_records.check_case_answer', return_value=proof), \
             patch.object(main_agent, 'get_llm', return_value=model):
            asyncio.run(main_agent.soft_review(result))
        prompt = reviewer.ainvoke.call_args.args[0][1].content
        self.assertIn('owners 是所列案件的承办人集合', prompt)
        self.assertIn('不是按数组下标一一对应', prompt)
        self.assertIn('附加筛选', prompt)
        self.assertIn(result['query'], prompt)
        self.assertIn('joined_rows', prompt)
        self.assertEqual(result['soft_review_mode'], 'structured_content')

    def test_invalid_review_is_regenerated_from_evidence_without_stale_ai_verdict(self):
        result, good = review_fixture()
        for row in good['checks']:
            row['task_anchor_id']=review_anchor_index(result)['task'][0]['id']
        bad = copy.deepcopy(good)
        bad['verdict'] = 'failed'
        bad['checks'][0].update(status='failed', detail='重新核对后，原回答数值正确。', action='none', suggestion='')
        model, reviewer = review_model(good)
        reviewer.ainvoke.side_effect = [
            {'parsed':ContentReviewDecision(**bad), 'raw':AIMessage(content=json.dumps(bad,ensure_ascii=False))},
            {'parsed':ContentReviewDecision(**good), 'raw':AIMessage(content='')}]
        with patch.object(main_agent, 'get_llm', return_value=model):
            issue = asyncio.run(main_agent.soft_review(result))
        self.assertIsNone(issue)
        self.assertEqual(reviewer.ainvoke.await_count, 2)
        retry = reviewer.ainvoke.call_args.args[0]
        self.assertFalse(any(isinstance(message, AIMessage) for message in retry))
        self.assertIn('前次审查无效', retry[-1].content)
        self.assertIn(result['query'], retry[-1].content)
        self.assertEqual(result['soft_review_status'], 'passed')

    def test_case_review_cannot_require_prose_outside_the_requested_json(self):
        result, good = review_fixture()
        result['query'] = '核对附件，只返回JSON对象，字段为history_ids、owners和evidence_ids。'
        result['report'] = '{"history_ids":["A"],"owners":["甲"],"evidence_ids":["S1"]}'
        good['checks'][0]['task_quote'] = result['query']
        bad = copy.deepcopy(good)
        bad['verdict'] = 'failed'
        bad['checks'][0].update(status='failed', detail='结果正确，但没有解释排除B的理由。',
            action='revise_answer', suggestion='在JSON外或字段内补充说明B不满足技术条件。')
        model, reviewer = review_model(good)
        reviewer.ainvoke.side_effect = [
            {'parsed':case_review_payload(result,bad)}, {'parsed':case_review_payload(result,good)}]
        with patch('tools.case_relevance.review_case_answer',side_effect=passed_selection_gate), \
             patch('tools.case_records.check_case_answer', return_value={'status':'passed','calculation':{}}), \
             patch.object(main_agent, 'get_llm', return_value=model):
            issue = asyncio.run(main_agent.soft_review(result))
        self.assertIsNone(issue)
        self.assertEqual(reviewer.ainvoke.await_count, 2)
        self.assertIn('输出契约', result['review_format_errors'][0]['detail'])
        self.assertEqual(result['soft_review_status'], 'passed')

    def test_invalid_case_review_is_never_automatically_accepted(self):
        result, payload = review_fixture()
        result['query'] = '只返回JSON对象，字段为history_ids、owners和evidence_ids。'
        payload['checks'][0].update(task_quote=result['query'], status='failed',
            detail='需要说明排除依据。', action='revise_answer', suggestion='请在JSON外补充排除理由。')
        payload['verdict'] = 'failed'
        model, reviewer = review_model(payload)
        reviewer.ainvoke.return_value={'parsed':case_review_payload(result,payload)}
        with patch('tools.case_relevance.review_case_answer',side_effect=passed_selection_gate), \
             patch('tools.case_records.check_case_answer', return_value={'status':'passed','calculation':{}}), \
             patch.object(main_agent, 'get_llm', return_value=model):
            asyncio.run(main_agent.soft_review(result))
        self.assertEqual(reviewer.ainvoke.await_count, 2)
        self.assertEqual(result['soft_review_status'], 'skipped')
        self.assertEqual(result['verification']['status'], 'unverified')

    def test_scope_guard_preserves_real_omissions_and_noncase_output_requests(self):
        from tools.content_verification import validate_case_review_scope
        result, payload = review_fixture()
        payload['verdict'] = 'failed'
        payload['checks'][0].update(status='failed', detail='原文证明B也满足条件且已交局，结果漏案。',
            action='revise_answer', suggestion='按原文补入遗漏案号B；不要在JSON外增加解释。')
        decision=ContentReviewDecision(**payload)
        validate_case_review_scope(decision, {'status':'passed'})
        self.assertEqual(finalize_review(decision,result,{'S1'})['status'], 'failed')
        payload['checks'][0]['suggestion']='请在JSON外补充排除理由。'
        validate_case_review_scope(ContentReviewDecision(**payload), {'status':'not_applicable'})

    def test_selection_proof_does_not_allow_invented_technical_output_fields(self):
        from tools.content_verification import validate_case_review_scope
        for suggestion in ('在JSON中补充技术条件核验结论，如新增字段说明哪些案件满足机制。',
                           '在保持指定JSON字段的前提下体现技术条件核验结论，按案号说明关系。'):
            result,payload=review_fixture();payload['verdict']='failed'
            payload['checks'][0].update(status='failed',detail='缺少输出的核验过程。',
                action='revise_answer',suggestion=suggestion)
            with self.subTest(suggestion=suggestion),self.assertRaisesRegex(ValueError,'输出契约'):
                validate_case_review_scope(ContentReviewDecision(**payload),{'status':'passed'},selection_verified=True)
        payload['checks'][0]['suggestion']='根据逐案原文修正错误字段，补入漏案B。'
        validate_case_review_scope(ContentReviewDecision(**payload),{'status':'passed'},selection_verified=True)

    def test_case_findings_derive_status_and_reject_contradictory_legacy_flags(self):
        result,payload=review_fixture()
        data=case_review_payload(result,payload)
        self.assertEqual(CaseContentReviewFindings(**data).as_decision().verdict,'passed')
        bad=copy.deepcopy(data)
        bad['checks'][0]['status']='failed'
        with self.assertRaises(ValueError):CaseContentReviewFindings(**bad)
        bad=copy.deepcopy(data);del bad['checks'][0]['issue']
        with self.assertRaises(ValueError):CaseContentReviewFindings(**bad)
        data['checks'][0]['issue']={'state':'failed','action':'revise_answer','suggestion':'补入确有原文及已核实交局的B案。'}
        decision=CaseContentReviewFindings(**data).as_decision()
        self.assertEqual(finalize_review(decision,result,{'S1'})['status'],'failed')
        data['checks'][0]['issue']['action']='none'
        with self.assertRaises(ValueError):CaseContentReviewFindings(**data)

    def test_unstructured_pass_is_not_a_verified_pass(self):
        result, _ = review_fixture()
        model, reviewer = review_model()
        reviewer.ainvoke.return_value={'raw':AIMessage(content='通过。'),'parsed':None,'parsing_error':ValueError('missing schema')}
        with patch.object(main_agent, 'get_llm', return_value=model):
            self.assertIsNone(asyncio.run(main_agent.soft_review(result)))
        self.assertEqual(result['soft_review_status'], 'skipped')
        self.assertEqual(result['verification']['status'], 'unverified')

    def test_review_timeout_and_task_cancellation(self):
        async def wait_forever(*args):
            await asyncio.Event().wait()
        result, _ = review_fixture()
        model, reviewer = review_model()
        reviewer.ainvoke.side_effect = wait_forever
        with patch.object(main_agent, 'get_llm', return_value=model), patch.dict('os.environ', {'CONTENT_REVIEW_TIMEOUT': '1'}):
            self.assertIsNone(asyncio.run(main_agent.soft_review(result)))
        self.assertEqual(result['verification']['status'], 'unverified')
        reviewer.ainvoke.side_effect = asyncio.CancelledError()
        with patch.object(main_agent, 'get_llm', return_value=model), self.assertRaises(asyncio.CancelledError):
            asyncio.run(main_agent.soft_review(result))

    def test_business_contract_keeps_specialized_review(self):
        result, _ = review_fixture()
        result['business_check'] = {'status': 'passed'}
        async def business_review(value):
            value.update(soft_review_status='passed', soft_review_mode='program_contract')
            return None
        with patch('casework.review.review', side_effect=business_review), patch.object(main_agent, 'get_llm') as model:
            self.assertIsNone(asyncio.run(main_agent.soft_review(result)))
        model.assert_not_called()
        self.assertEqual(result['verification']['mode'], 'program_contract')
        self.assertEqual(result['verification']['checks'], [])


class MeetingAbsenceTests(unittest.TestCase):
    """Absence from a meeting register needs the actual evidence id."""

    def setUp(self):
        self.sources=[{'casework':True,'type':'knowledge','id':'S3','snippet':'会议记录：围绕端点检测核对'}]

    def test_absence_without_source_is_rejected(self):
        from tools.content_verification import archive_absence_issues
        self.assertTrue(archive_absence_issues('“关键反例”在两案会议资料中均无登记，属于资料缺口。',self.sources))
        self.assertTrue(archive_absence_issues('申请文件中没有该步骤。',self.sources))

    def test_absence_with_source_or_explicit_scope_passes(self):
        from tools.content_verification import archive_absence_issues
        for text in ('“关键反例”在两案会议资料中均无登记。[S3]',
                     '会议记录中未登记待补材料。[S3]\n\n其余无。',
                     '在本次已取得的会议记录中未见该反例。'):
            with self.subTest(text=text):
                self.assertFalse(archive_absence_issues(text,self.sources))


class QuotedAndCoverageGapTests(unittest.TestCase):
    """A quotation or a stated coverage gap is not an unsupported absence claim."""

    def setUp(self):
        self.sources=[{'casework':True,'type':'knowledge','id':'S3','snippet':'会议记录：围绕端点检测核对'}]

    def test_quoted_source_text_is_not_rejected(self):
        from tools.content_verification import archive_absence_issues
        for text in ('该案档案注明为 AI 生成材料—"不是公开申请文件，没有真实申请号、公开号。"',
                     '该申请文件首页自述为 AI 生成材料：“不是公开申请文件，没有真实申请号。'):
            with self.subTest(text=text):
                self.assertFalse(archive_absence_issues(text,self.sources))

    def test_stated_coverage_gap_is_not_rejected(self):
        from tools.content_verification import archive_absence_issues
        text=('会议资料“待补示例／边界样本”尚未取得原文：三方会议表已定位，'
              '但正文行未成功读取，故无法核对，也不能判定为“缺失”。')
        self.assertFalse(archive_absence_issues(text,self.sources))
