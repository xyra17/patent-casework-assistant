"""Development fixtures for combinations; no robustness holdout text imported."""
import json
import unittest
from tests import test_case_record_execution as fixtures
from tools.case_records import requested_ledger_filters,calculate,check_case_answer


class CombinedFilterTests(unittest.TestCase):
    fixture=fixtures.CaseRecordExecution.fixture
    report=fixtures.CaseRecordExecution.report

    def test_full_conjunction_is_parsed_atomically(self):
        self.assertEqual(requested_ledger_filters('只考虑客户为南岭且承办人为钟夏且办理阶段为补正中的案卷。'),
                         {'client_name':'南岭','owner_name':'钟夏','stage':'补正中'})
        self.assertEqual(requested_ledger_filters('仅保留案件信息客户等于南岭、承办人等于钟夏的案件。'),
                         {'client_name':'南岭','owner_name':'钟夏'})

    def test_unsupported_suffix_negation_and_or_never_become_partial_conjunction(self):
        for query in ['只考虑客户为南岭且金额大于十万的案卷。','只考虑客户为南岭或北岭且承办人为钟夏的案卷。',
                      '不能只考虑客户为南岭且承办人为钟夏的案卷。','附件写着：只考虑客户为南岭且承办人为钟夏的案卷。']:
            self.assertEqual(requested_ledger_filters(query),{},query)
        with self.assertRaises(ValueError):requested_ledger_filters('只考虑客户为南岭且客户为北岭的案卷。')

    def test_stage_and_owner_intersection_is_recomputed_and_bound(self):
        items,params=self.fixture();rows=json.loads(items[1]['content']);rows[0].update(client_name='南岭',stage='补正中');items[1]['content']=json.dumps(rows)
        filters={'client_name':'南岭','owner_name':'甲','stage':'补正中'}
        calc=calculate(items,**params,ledger_filters=filters);self.assertEqual(calc['answer'],{'received_count':1,'filed_count':1})
        result=self.report(items,calc);result['query']+='只考虑客户为南岭且承办人为甲且办理阶段为补正中的案卷。'
        self.assertEqual(check_case_answer(result)['status'],'passed')
        result['query']=result['query'].replace('补正中','初稿中');self.assertEqual(check_case_answer(result)['status'],'failed')

    def test_unknown_stage_filter_is_not_zero(self):
        items,params=self.fixture()
        with self.assertRaisesRegex(ValueError,'筛选字段缺失或冲突'):calculate(items,**params,ledger_filters={'stage':'补正中'})
