"""Bounded recovery must improve validation without changing manual limits."""
import asyncio
import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent import main_agent
from api import context, storage
from api.round_budget import select_round_budget, extend_round_budget
from tools.review_evidence import verified_statistics_context


class AdaptiveRecoveryTests(unittest.TestCase):
    def test_only_progressing_auto_budget_extends_one_round_up_to_six(self):
        budget=select_round_budget({'task_type':'analysis'},[],None)
        self.assertEqual(budget['max_rounds'],3)
        original=copy.deepcopy(budget)
        for flags in [dict(made_progress=False,stalled=False,recoverable=True),
                      dict(made_progress=True,stalled=True,recoverable=True),
                      dict(made_progress=True,stalled=False,recoverable=False)]:
            self.assertIsNone(extend_round_budget(budget,**flags))
        for expected in (4,5,6):
            budget=extend_round_budget(budget,made_progress=True,stalled=False,recoverable=True)
            self.assertEqual(budget['max_rounds'],expected)
        self.assertEqual(original['max_rounds'],3)
        self.assertIsNone(extend_round_budget(budget,made_progress=True,stalled=False,recoverable=True))
        for manual in (1,3,6):
            self.assertIsNone(extend_round_budget(select_round_budget({},[],manual),
                made_progress=True,stalled=False,recoverable=True))
        fixed=select_round_budget({},[],None,fixed_workflow=True)
        self.assertEqual(fixed['max_rounds'],2)
        self.assertEqual(extend_round_budget(fixed,made_progress=True,stalled=False,recoverable=True)['max_rounds'],3)

    def execute(self, mode, results, reviews):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root=Path(tmp);(root/'output').mkdir();(root/'upload').mkdir()
            for target,value in [('casework.store.roots',[]),('rules.engine.current',{})]:
                stack.enter_context(patch(target,return_value=value))
            stack.enter_context(patch.object(storage,'_DB_PATH',root/'app.db'))
            stack.enter_context(patch.object(main_agent.path_utils,'OUTPUT_DIR',root/'output'))
            stack.enter_context(patch.object(main_agent.path_utils,'UPLOAD_DIR',root/'upload'))
            storage.init_db();storage.create_run('adaptive','问题','问题',False,3)
            plan=main_agent.TaskPlan(task_type='analysis',require_report=False,require_pdf=False,
                                     required_sources=[],steps=['回答'],output_artifacts=[],assumptions=[])
            stack.enter_context(patch.object(main_agent,'build_task_plan',AsyncMock(return_value=plan)))
            runner=stack.enter_context(patch.object(main_agent,'main_agent_run',AsyncMock(side_effect=results)))
            stack.enter_context(patch.object(main_agent,'soft_review',AsyncMock(side_effect=reviews)))
            try:
                answer=asyncio.run(main_agent.run_deep_agent('问题','adaptive',mode))
                run=storage.get_run('adaptive')
            finally:context.reset_session()
            return answer,run,runner.await_count,runner.call_args_list

    @staticmethod
    def drafts(count, new_evidence=True):
        return [{'report':f'第{i}份回答','evidence':[{'id':'S1','snippet':f'依据{i}' if new_evidence else '同一依据'}],
                 'called_sources':[],'pdf_produced':False} for i in range(count)]

    def test_auto_extends_after_three_and_reuses_revision_evidence(self):
        answer,run,calls,args=self.execute(None,self.drafts(4),['待核对']*3+[None])
        self.assertEqual(calls,4)
        self.assertEqual(answer,'第3份回答')
        extensions=[e for e in run['events'] if e['event']=='plan_updated' and e['data'].get('budget_extension')]
        self.assertEqual(len(extensions),1)
        self.assertEqual(extensions[0]['data']['max_rounds'],4)
        self.assertEqual(extensions[0]['data']['required_sources'],[])
        self.assertIn('revision_context',args[-1].kwargs)
        self.assertTrue(any('依据2' in str(m.content) for m in args[-1].kwargs['revision_context']))

    def test_manual_three_remains_hard_limit(self):
        _,run,calls,_=self.execute(3,self.drafts(4),['待核对']*4)
        self.assertEqual(calls,3)
        self.assertFalse(any(e['event']=='plan_updated' and e['data'].get('budget_extension') for e in run['events']))

    def test_cosmetic_draft_changes_do_not_grant_extra_rounds(self):
        _,run,calls,_=self.execute(None,self.drafts(4,new_evidence=False),['同一错误']*4)
        self.assertEqual(calls,3)
        summary=next(e['data'] for e in run['events'] if e['event']=='quality_summary')
        self.assertFalse(summary['passed'])

    def test_progress_signature_ignores_renumbered_citations(self):
        first={'report':'原句[S1]','evidence':[{'id':'S1','snippet':'原句'}]}
        second={'report':'原句[S99]','evidence':[{'id':'S99','snippet':'原句'}]}
        self.assertEqual(main_agent._progress_signature(first,['引用S1不匹配']),
                         main_agent._progress_signature(second,['引用S99不匹配']))

    def test_review_receives_checked_cells_without_claiming_query_correctness(self):
        metric={'column':'unknown_stage_count','label':'状态未知案件数量','definition_verified':True}
        result={'evidence':[{'id':'S1','statistics':{'metrics':[metric]}}],
                'statistics_check':{'status':'passed','checks':[{'metric_text':'状态未知案件数量','value_text':'0',
                    'sources':[{'evidence_id':'S1','column':'unknown_stage_count','expected_value':'0'}]}]}}
        proof=verified_statistics_context(result)
        self.assertEqual(proof['checked_cells'][0]['source_metric'],metric)
        self.assertIn('不证明查询定义正确',proof['scope'])
        result['statistics_check']['status']='failed'
        self.assertEqual(verified_statistics_context(result),{})

    def test_quote_correction_does_not_instruct_relaxing_citation_check(self):
        prompt=main_agent.build_correction_prompt('原问题',['直接引文与所引片段不匹配：正文；引用=S1'],None)
        self.assertIn('逐字复制',prompt)
        self.assertIn('拼接、改写内容不得',prompt)

    def test_same_draft_review_can_correct_a_false_finding_but_not_waive_a_real_one(self):
        from tests.test_content_verification import review_fixture, review_model
        from tools.content_verification import ContentReviewDecision
        for corrected in (True,False):
            with self.subTest(corrected=corrected):
                result,good=review_fixture()
                from tools.content_verification import review_anchor_index
                for row in good['checks']:
                    row['task_anchor_id']=review_anchor_index(result)['task'][0]['id']
                result['evidence'][0]['statistics']={'metrics':[{'column':'net_sales','label':'净销售额',
                                                               'definition_verified':True}]}
                result['statistics_check']={'status':'passed','checks':[{'metric_text':'净销售额','value_text':'80元',
                    'sources':[{'evidence_id':'S1','column':'net_sales','expected_value':'80'}]}]}
                bad=copy.deepcopy(good);bad['verdict']='failed'
                bad['checks'][1].update(status='failed',detail='来源缺少净销售额，需核对',answer_quote=result['report'],
                                         action='revise_answer',suggestion='核对来源中净销售额字段')
                model,reviewer=review_model()
                reviewer.ainvoke.side_effect=[{'parsed':ContentReviewDecision(**p)}
                    for p in (bad,good if corrected else bad)]
                original=result['report']
                with patch.object(main_agent,'get_llm',return_value=model):
                    issue=asyncio.run(main_agent.soft_review(result))
                self.assertEqual(result['report'],original)
                self.assertEqual(reviewer.ainvoke.await_count,2)
                self.assertEqual(len(result['review_rechecks']),1)
                self.assertEqual(issue is None,corrected)
                self.assertEqual(result['verification']['status'],'passed' if corrected else 'failed')
