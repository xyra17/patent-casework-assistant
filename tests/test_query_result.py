"""Final answers retain their complete body and validation without excerpt generation."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from fastapi.testclient import TestClient
from api import query_result as projection, storage, server

QUOTE='控制活动包括一套全面的政策和相关的执行程序，从而确保管理层的指令得到正确执行。'
QUERY='只查知识库，不联网，不查数据库。依据《企业内部控制管理手册》6.3节，引用一句原文说明控制活动的作用。'
REPORT='已检索到原文：\n\n> “'+QUOTE+'”\n\n这说明控制活动确保指令执行 [S1]。\n出处：管理手册第65页。'
EVIDENCE=[{'id':'S1','title':'企业内部控制管理手册','snippet':QUOTE.replace('指令','指\n令'),'location':'第65页'}]

def business_fixture(number=10):
    from tests.unit_fixtures import business_run
    return business_run(number)

class QueryResultTests(unittest.TestCase):
    def test_compatible_fixed_business_examples_display_without_second_model_call(self):
        with patch('agent.llm.get_llm', side_effect=AssertionError('verified business must not call a second model')):
            for number in range(1, 11):
                run = business_fixture(number)
                result = asyncio.run(projection.build_query_result(run['execution_query'], run['result'], run['evidence'],
                    business_review=projection.business_review_from_run(run)))
                if number == 7:
                    # The frozen request predates explicit mechanism scope. A
                    # historical pass cannot authorize it under today's contract.
                    self.assertEqual(result['status'],'unavailable')
                    self.assertTrue(result['requires_rerun'])
                    self.assertIn('用户明确的案件查询口径未保留',result['validation_issues'])
                    continue
                self.assertEqual(result['status'], 'ready', number)
                self.assertEqual(result['mode'], 'verified_business')
                self.assertEqual(result['content'], run['result'])
                self.assertTrue(set(result['evidence_ids']) <= {e['id'] for e in run['evidence']})

    def test_fixed_projection_rejects_changed_body_sources_plan_and_failed_review(self):
        run = business_fixture()
        review = projection.business_review_from_run(run)
        cases = [(run['result'] + '\n新增无据结论', run['evidence'], review, False),
                 (run['result'], [], review, False),
                 (run['result'], run['evidence'], review, True)]
        for changed in [{'soft_review_status': 'failed'}, {'hard_issues': ['未完成']}, {'soft_issue': '遗漏条件'},
                        {'business_check': {}}, {'planned_request': {'operation': 'cases', 'query': 'DEMO-2026-024'}}]:
            cases.append((run['result'], run['evidence'], {**copy.deepcopy(review), **changed}, False))
        with patch('agent.llm.get_llm', side_effect=AssertionError('a model cannot waive a failed seal')):
            for report, evidence, current, degraded in cases:
                result = asyncio.run(projection.build_query_result(run['execution_query'], report, evidence, degraded,
                    business_review=current))
                self.assertEqual(result['status'], 'unavailable')

    def test_only_latest_review_can_authorize_fixed_projection(self):
        run = business_fixture()
        run['events'].append({'event': 'reflection', 'data': {'soft_review_status': 'failed'}})
        result = projection.verified_business_result(run['execution_query'], run['result'], run['evidence'],
            projection.business_review_from_run(run))
        self.assertEqual(result['status'], 'unavailable')

    def test_requested_quote_is_verified_against_source_without_model(self):
        with patch('agent.llm.get_llm',side_effect=AssertionError('no model')):
            result=asyncio.run(projection.build_query_result(QUERY,REPORT,EVIDENCE))
        self.assertEqual(result['content'],REPORT);self.assertEqual(result['evidence_ids'],['S1']);self.assertEqual(result['mode'],'final_result')
        result=asyncio.run(projection.build_query_result(QUERY,REPORT,[{'id':'S1','snippet':'其他原文'}]))
        self.assertEqual(result['status'],'unavailable')
    def test_unregistered_citation_is_not_marked_as_valid(self):
        result=asyncio.run(projection.build_query_result('问题','结论未核实[S9]',EVIDENCE))
        self.assertEqual(result['status'],'unavailable')

    def test_long_final_answer_is_kept_without_calling_an_extraction_model(self):
        report='# 分析结果\n'+('背景资料。'*4000)+'\n当前数量未核实。'
        with patch('agent.llm.get_llm',side_effect=AssertionError('no extraction model')):
            result=asyncio.run(projection.build_query_result('数量是多少',report,[]))
            self.assertEqual(result['content'],report)
            self.assertEqual(result['mode'],'final_result')
            self.assertEqual(asyncio.run(projection.build_query_result('数量',report,[],True))['status'],'unavailable')


class QueryResultStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(storage,'_DB_PATH',Path(self.tmp.name)/'app.db');self.patch.start();storage.init_db()
        storage.create_run('answer-test',QUERY,QUERY,False)
        storage.append_event('answer-test',{'event':'task_result','data':{'result':REPORT,'evidence':EVIDENCE}})
        self.client=TestClient(server.app)
    def tearDown(self):self.client.close();self.patch.stop();self.tmp.cleanup()
    def test_legacy_projection_is_cached_without_changing_report_or_history_order(self):
        before=storage.get_run('answer-test');result=self.client.post('/api/runs/answer-test/query-result')
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['content'],REPORT)
        with patch.object(server,'build_query_result',side_effect=AssertionError('cached')):
            self.assertEqual(self.client.post('/api/runs/answer-test/query-result').json(),result.json())
        after=storage.get_run('answer-test');self.assertEqual(after['result'],REPORT);self.assertEqual(after['updated_at'],before['updated_at']);self.assertEqual(after['query_result'],result.json())
    def test_rerun_clears_answer_and_rejects_late_projection(self):
        before=storage.get_run('answer-test');storage.create_run('answer-test',QUERY,QUERY,False)
        self.assertFalse(storage.save_query_result('answer-test',REPORT,before['created_at'],{'content':QUOTE}))
        self.assertIsNone(storage.get_run('answer-test')['query_result'])
        storage.append_event('answer-test',{'event':'task_result','data':{'result':REPORT,'evidence':EVIDENCE}})
        self.assertFalse(storage.save_query_result('answer-test',REPORT,before['created_at'],{'content':QUOTE}))
        storage.create_run('answer-test',QUERY,QUERY,False)
        self.assertEqual(self.client.post('/api/runs/answer-test/query-result').status_code,409)
    def test_completion_event_persists_separate_answer(self):
        storage.create_run('answer-test',QUERY,QUERY,False)
        value=projection.payload(QUOTE,['S1'],'verified_quote')
        storage.append_event('answer-test',{'event':'task_result','data':{'result':REPORT,'query_result':value,'evidence':EVIDENCE}})
        self.assertEqual(storage.get_run('answer-test')['query_result'],value)

    def test_fixed_legacy_projection_upgrades_then_caches_without_changing_core_result(self):
        run = business_fixture()
        tid = 'fixed-answer'
        storage.create_run(tid, run['query'], run['execution_query'], False)
        storage.append_event(tid, {'event': 'plan_created', 'data': run['plan']})
        review = next(e for e in reversed(run['events']) if e['event'] == 'reflection')
        storage.append_event(tid, review)
        storage.append_event(tid, {'event': 'task_result', 'data': {'result': run['result'], 'evidence': run['evidence'],
            'query_result': {'content': '', 'status': 'unavailable', 'statistical_validation_version': 1}}})
        before = storage.get_run(tid)
        with patch('agent.llm.get_llm', side_effect=AssertionError('no extraction model')):
            response = self.client.post(f'/api/runs/{tid}/query-result')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['mode'], 'verified_business')
        with patch.object(server, 'build_query_result', side_effect=AssertionError('cached')):
            self.assertEqual(self.client.post(f'/api/runs/{tid}/query-result').json(), response.json())
        after = storage.get_run(tid)
        for field in ('result', 'evidence', 'plan', 'status', 'degraded', 'created_at', 'updated_at'):
            self.assertEqual(before[field], after[field], field)
