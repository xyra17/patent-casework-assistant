"""An omitted technical preface must survive extraction, execution and review."""
import asyncio
import json
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from casework.workflow import DepartmentIntent, compile_intent, execute
from casework.verification import task_contract_issues
from tests.test_subject_support import SubjectSearchTests

TASK='最近有个方案要做跨请求缓存隔离，我们以前办过类似的案子吗？帮我找一件，顺便把相关原文和文件位置给我。'
SHORT='我们以前办过类似的案子吗？'


def incomplete_intent():
    return DepartmentIntent(query=SHORT, result_limit=1,
        requirements=[{'kind':'archive_materials','quote':SHORT}])


class ArchiveQueryIntegrityTests(unittest.TestCase):
    def test_host_retains_context_even_when_model_drops_the_whole_preface(self):
        task=TASK+'\n【任务类型：快速问答】只做必要检索，直接回答问题；不要生成任何文件。'
        request=compile_intent(incomplete_intent(),task)
        self.assertEqual(request.retrieval_query,TASK)
        self.assertNotIn('retrieval_query',DepartmentIntent.model_fields)
        with self.assertRaises(ValidationError):
            DepartmentIntent(**incomplete_intent().model_dump(),retrieval_query='替换主题')

    def test_tool_and_candidate_judge_receive_the_same_full_user_question(self):
        request=compile_intent(incomplete_intent(),TASK)
        raw={'query':TASK,'results':[],'has_more':False,'filters':{},'query_scope':'ambiguous'}
        calls=[]
        tool=SimpleNamespace(name='search_case_archive',invoke=lambda args:(calls.append(args) or json.dumps(raw)))
        selector=AsyncMock(return_value=raw)
        with patch('tools.casework_tools.search_case_archive',tool),\
             patch('casework.store.read_snapshot',return_value=nullcontext()),\
             patch('casework.archive_selection.select_candidates',selector),\
             patch('casework.workflow.render',return_value='没有已确认匹配'),\
             patch('casework.verification.verify',return_value={'status':'passed','request':request.model_dump()}),\
             patch('casework.verification.delivery',return_value={}),\
             patch('api.evidence.snapshot',return_value=[]),\
             patch('api.monitor.monitor.report',AsyncMock()):
            asyncio.run(execute(request,'query-integrity-test',1,['knowledge']))
        self.assertEqual(calls[0]['query'],TASK)
        self.assertGreaterEqual(calls[0]['limit'],6)
        self.assertEqual(selector.call_args.args[1],TASK)

    def test_execution_drift_is_rejected_even_if_a_semantic_reviewer_would_pass(self):
        request=compile_intent(incomplete_intent(),TASK)
        delivered={'retrieval_query':SHORT,'case_ids':['AI-2026-054'],'query_scope':'ambiguous'}
        self.assertIn('实际技术检索参数与原问题不一致',task_contract_issues(TASK,request,delivered))
        wrong=request.model_copy(update={'retrieval_query':SHORT})
        self.assertTrue(any('完整原问题' in issue for issue in task_contract_issues(TASK,wrong,{})))

    def test_unverified_candidate_cannot_be_accepted_as_a_finished_answer(self):
        request=compile_intent(incomplete_intent(),TASK)
        delivered={'retrieval_query':TASK,'unverified_candidate_ids':['AI-2026-001']}
        self.assertTrue(any('仍待核实' in x for x in task_contract_issues(TASK,request,delivered)))

    def test_one_requested_answer_does_not_cut_off_later_relevant_candidates(self):
        request=compile_intent(incomplete_intent(),TASK)
        candidates=[{'case_id':str(i)}for i in range(6)]
        raw={'query':TASK,'results':candidates,'has_more':False}
        tool=SimpleNamespace(name='search_case_archive',invoke=lambda args:json.dumps(raw))
        # The first retrieval hit is irrelevant; the judge keeps later matches.
        selector=AsyncMock(return_value={**raw,'results':list(reversed(candidates[-2:]))})
        with patch('tools.casework_tools.search_case_archive',tool),\
             patch('casework.store.read_snapshot',return_value=nullcontext()),\
             patch('casework.archive_selection.select_candidates',selector),\
             patch('casework.workflow.render',return_value='相关案件')as renderer,\
             patch('casework.verification.verify',return_value={'status':'passed','request':request.model_dump()}),\
             patch('casework.verification.delivery',return_value={}),\
             patch('api.evidence.snapshot',return_value=[]),\
             patch('api.monitor.monitor.report',AsyncMock()):
            asyncio.run(execute(request,'query-pool-test',1,['knowledge']))
        self.assertEqual(len(selector.call_args.args[0]['results']),6)
        self.assertEqual([c['case_id']for c in renderer.call_args.args[0]['results']],['5'])


class BroadScopeRerankingTests(unittest.TestCase):
    setUp=SubjectSearchTests.setUp
    tearDown=SubjectSearchTests.tearDown
    add=SubjectSearchTests.add
    publish=SubjectSearchTests.publish

    def test_broad_scope_reranks_without_erasing_low_scoring_negative_mentions(self):
        from casework.search import search
        self.add('DEMO-2099-001','背景提到缓存隔离，本案并未采用缓存隔离。')
        self.add('DEMO-2099-002','本案对请求缓存按租户隔离，读取前校验租户身份。')
        self.publish()
        seen=[]
        def rank(query,records):
            seen.append(query)
            return [{**r,'rerank_logit':-100.0}for r in records]
        with patch('casework.search.get_embedding',return_value=(None,'test')),\
             patch('casework.concept_router.propose',return_value={'concepts':[],'status':'test'}),\
             patch('casework.local_reranker.rank',side_effect=rank):
            result=search('缓存隔离',query_scope='mention',limit=8)
        self.assertTrue(seen,'mention scope must also invoke the local reranker')
        self.assertEqual({c['case_id']for c in result['results']},{'DEMO-2099-001','DEMO-2099-002'})
        self.assertIn('本地交叉编码重排',result['strategy'])
