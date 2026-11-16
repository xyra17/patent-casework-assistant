"""Source-bound positive/negative regressions from observed review failures."""
import json
import unittest
from unittest.mock import patch

from tools.comparison_claims import comparison_change_issues
from tools.evidence_coverage import defined_terms, acquired_content_issues
from tools.content_verification import CRITERIA, ContentReviewDecision, finalize_review

QUERY = '比较两侧原句，给出基准和修订的文字关系，说明能否证明机制替换。'


def source(sid='S1', url='https://example.org/reference'):
    return {'id':sid,'type':'web','url':url,'title':'Official reference',
            'snippet':'## Search parameters\n`beamWidth` is the number of candidates kept.\n'
                      '`buildDepth` is the depth at construction time.\n'
                      '`searchDepth` is the depth during search.\n'}


class ComparisonClaimTests(unittest.TestCase):
    def test_literal_changes_and_local_negation_are_not_mechanism_assertions(self):
        for text in [
            '第2行原句由“缓存保留七天，到期清理。”改为“撤销授权后失效。”，属原句替换。',
            '因此无法据此认定发生了机制替换，还需要确认。',
            '尚不能直接证明机制由A改为B。',
            '文本从A改为B。',
            '机制的文字描述由A改为B。',
            '触发条件的表述从A改为B。',
            '机制是否已经替换尚未确定。',
            '原文表述：“机制由甲改为乙。”[S1]，此处只摘录原句。',
            '不能确定机制是否替换。',
            '能否证明机制替换：不能。',
            '并未发生机制替换。',
            '可能机制由A改为B，但没有直接依据，仍需核实。',
            '方案由A改为B。',
        ]:
            with self.subTest(text=text):self.assertEqual(comparison_change_issues(QUERY,text),[])

    def test_disclaimer_or_unrelated_word_cannot_waive_an_affirmative_change(self):
        for text in [
            '机制由A改为B。',
            '能否证明机制替换：可以。',
            '机制是否替换：是。',
            '“机制”由A改为B。',
            '`机制`由A改为B。',
            '文字有变化，但机制由A改为B。',
            '不能确定性能，但机制由A改为B。',
            '原句显示失效规则由A改为B。',
            '仅比较文字，机制实际上由A改为B。',
            '机制由“先核对身份”改为“直接读取”。',
            '无法确定性能，然而触发条件与时机均改变。',
            '机制已经替换，尚需人工核对。',
        ]:
            with self.subTest(text=text):self.assertTrue(comparison_change_issues(QUERY,text))

    def test_real_candidate_shapes_reach_content_review_but_not_automatic_pass(self):
        from tools.reflect_check import hard_check
        for report in ['判定：第2行原句由“保留八天，到期后清理”改为“撤销权限后失效”，属原句替换；单凭这一文字变化不能证明发生了机制替换。',
                       '第2行只显示原文表述从“保留八天”改为“撤销权限后失效”，即文字层面的替换；因此无法据此认定发生了机制替换。']:
            self.assertFalse(any(i.startswith('文本比较越界') for i in hard_check({'query':QUERY,'report':report,'evidence':[]})))


class CoverageContradictionTests(unittest.TestCase):
    def test_receipt_contains_literal_definition_spans_only(self):
        text=source()['snippet'];rows=defined_terms(text)
        self.assertEqual([r['term'] for r in rows],['beamWidth','buildDepth','searchDepth'])
        for r in rows:self.assertEqual(text[r['start']:r['end']],r['quote'])
        self.assertEqual(defined_terms('We mention `beamWidth` here, no definition.'),[])

    def test_received_definitions_cannot_be_called_missing(self):
        for text in ['`beamWidth`、`buildDepth`、`searchDepth` 等参数的完整条目原文和该页其余段落均未获取。',
                     '尚未取得`beamWidth`的定义。',
                     '`beamWidth`的含义未取得。[S1]',
                     '参数`searchDepth`的说明原文没有取得。']:
            with self.subTest(text=text):self.assertTrue(acquired_content_issues(text,[source()]))

    def test_definition_does_not_prove_defaults_results_or_another_source(self):
        for text in ['`beamWidth`的默认值未取得。','`beamWidth`的性能数据未取得。',
                     '未取得`searchDepth`的上限。','未取得完整网页。',
                     '不能声称`beamWidth`的定义未取得。',
                     '不能说尚未取得`beamWidth`的定义。',
                     '> `beamWidth`的定义未取得。[S1]',
                     '`unknownDepth`的定义未取得。',
                     '`beamWidth`的定义未取得。[S2]']:
            with self.subTest(text=text):self.assertEqual(acquired_content_issues(text,[source()]),[])
        self.assertEqual(acquired_content_issues('`beamWidth`的定义未取得。',[source(),source('S2','https://another.org')]),[])
        self.assertEqual(acquired_content_issues('`beamWidth`的定义未取得。',[{**source(),'snippet':'The document mentions `beamWidth`.'}]),[])

    def test_existing_hard_gate_rejects_the_claim_without_a_model(self):
        from tools.reflect_check import hard_check
        issues=hard_check({'query':'给出两条说明','report':'`searchDepth`的定义未获取。[S1]','evidence':[source()]})
        self.assertTrue(any(i.startswith('已取得内容被误报为缺失') for i in issues))

    def test_passed_reviewer_cannot_override_the_observable_contradiction(self):
        report='`beamWidth`的定义未获取。[S1]'
        result={'query':'核对公开定义','report':report,'evidence':[source()],'required_sources':['web_search']}
        payload={'verdict':'passed','checks':[{'criterion':c,'status':'passed','detail':'已检查',
            'task_quote':result['query'] if c=='task_completion' else '',
            'evidence_ids':['S1'] if c=='evidence_support' else []} for c in CRITERIA]}
        verified=finalize_review(ContentReviewDecision(**payload),result,{'S1'})
        self.assertEqual(verified['status'],'failed')
        self.assertEqual(verified['model_status'],'passed')
        self.assertTrue(all(c['status']=='passed' for c in verified['model_checks']))
        self.assertEqual(next(c for c in verified['checks'] if c['criterion']=='consistency')['basis'],'program_source_receipt')
        self.assertTrue(verified['program_checks'])
        # A failed review is preserved, never auto-corrected into a pass.
        payload['verdict']='failed';row=payload['checks'][list(CRITERIA).index('consistency')]
        row.update(status='failed',action='revise_answer',suggestion='按S1修正缺口说明',answer_quote=report)
        self.assertEqual(finalize_review(ContentReviewDecision(**payload),result,{'S1'})['status'],'failed')

    def test_definition_and_scope_receipt_survive_reviewer_projection(self):
        from tools.review_evidence import pack_review_evidence
        item=source();item['web_excerpt']={'observed_definitions':defined_terms(item['snippet']),'truncated':True}
        packed=pack_review_evidence([item],'请核对[S1]')
        self.assertIn('observed_definitions',packed)
        self.assertIn('searchDepth',packed)


class ReviewBoundaryIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_source_contradiction_is_an_answer_failure_not_a_format_retry(self):
        from unittest.mock import MagicMock, AsyncMock
        from agent import main_agent
        from tools.content_verification import review_anchor_index
        from langchain_core.messages import AIMessage
        result={'query':'核对公开定义','report':'`beamWidth`的定义未获取。[S1]',
                'evidence':[source()],'required_sources':['web_search']}
        index=review_anchor_index(result)
        payload={'verdict':'passed','checks':[{'criterion':c,'status':'passed','detail':'内部审查通过',
                  'task_anchor_id':index['task'][0]['id'],
                  'evidence_ids':['S1'] if c=='evidence_support' else []} for c in CRITERIA]}
        model=MagicMock();reviewer=AsyncMock();model.with_structured_output.return_value=reviewer
        reviewer.ainvoke.return_value={'parsed':payload,'raw':AIMessage(content='')}
        with patch.object(main_agent,'get_llm',return_value=model):
            issue=await main_agent.soft_review(result)
        self.assertTrue(issue)
        self.assertEqual(result['verification']['status'],'failed')
        self.assertEqual(result['verification']['model_status'],'passed')
        self.assertEqual(reviewer.ainvoke.await_count,1)
        self.assertEqual(result['review_format_errors'],[])
        from tools.result_assessment import assess_result,report_hash
        from tools.product_boundary import request_contract
        run={'status':'completed','result':result['report'],'evidence':result['evidence'],'events':[
            {'event':'product_boundary_request','data':request_contract(result['query'],{'profile':'public-reference'})},
            {'event':'reflection','data':{'round':1,'candidate_report_ref':report_hash(result['report']),'verification':result['verification']}},
            {'event':'quality_summary','data':{'returned_round':1,'passed':False}}]}
        assessment=assess_result(run)
        self.assertEqual(assessment['facts']['status'],'failed')
        self.assertEqual(assessment['product_boundary']['status'],'INCOMPLETE')

    async def test_definition_receipt_is_preserved_in_followup_context(self):
        from harness.middleware import EvidenceContextMiddleware
        item=source();item['web_excerpt']={'observed_definitions':defined_terms(item['snippet']),
            'content_kind':'provider_page_text','truncated':True}
        with patch('api.evidence.snapshot',return_value=[item]):
            messages=EvidenceContextMiddleware('coverage-context').source_ledger()
        payload=json.loads(messages[0].content)
        observed=payload['context_digest']['successful_public_sources'][0]['web_excerpt']['observed_definitions']
        self.assertEqual([row['term'] for row in observed],['beamWidth','buildDepth','searchDepth'])
