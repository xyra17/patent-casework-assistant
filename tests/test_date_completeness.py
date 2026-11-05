"""Cross-predicate and incomplete-query negatives must not become global facts."""
import copy
import unittest
from unittest.mock import patch
from tools.date_completeness import date_completeness_issues
from casework import analytics
from tests import test_department_contracts as fixtures


def proof(predicate='unknown_filed',count=0,**changes):
    contract={'version':1,'predicate':predicate,'stage_scope':'all','time_scope':'unassigned',
              'filters':[],'population_count':100,'matched_count':count,'complete':True}
    contract.update(changes)
    return {'id':'S1','type':'database','casework':True,'title':'实际日期未核实完整清单',
            'rows':[['CASE'+str(i)] for i in range(count)],'date_completeness':contract,
            'completeness':{'has_more':False,'evidence_truncated':False}}


class DateAssertionTests(unittest.TestCase):
    def test_empty_conflicts_do_not_prove_missing_dates_absent(self):
        text='没有查到日期冲突记录，也没有被单独列为实际交局日期为空或未核实交局的案件。[S1]'
        self.assertTrue(date_completeness_issues(text,[proof('date_conflicts')]))
        self.assertFalse(date_completeness_issues('当前接入范围没有日期冲突。[S1]',[proof('date_conflicts')]))

    def test_field_conflict_absence_does_not_prove_chronological_consistency(self):
        text='日期逻辑矛盾（交局早于收案）的情况未查到，为0件。[S1]'
        self.assertTrue(date_completeness_issues(text,[proof('date_conflicts')]))
        self.assertFalse(date_completeness_issues(text,[proof('chronology_conflicts')]))

    def test_none_and_all_verified_require_zero_matching_rows(self):
        for text in ('没有实际交局日期为空或未核实的案件。[S1]',
                     '所有案件的实际交局日期均已核实。[S1]',
                     '实际交局日期全部已核实。[S1]',
                     '实际交局日期未核实共0件。[S1]',
                     '实际交局日期全部已核实，不代表所有案子已完成。[S1]'):
            self.assertTrue(date_completeness_issues(text,[proof(count=75)]),text)
            self.assertFalse(date_completeness_issues(text,[proof()]),text)
        self.assertTrue(date_completeness_issues('没有实际交局日期为空的案件。',[proof()]))

    def test_field_filter_stage_time_and_truncation_must_match(self):
        text='当前接入范围没有实际交局日期未核实的案件。[S1]'
        for p in (proof('unknown_received'),proof(complete=False),proof(stage_scope='completed'),
                  proof(filters=[{'field':'client_alias','value':'客户甲'}])):
            self.assertTrue(date_completeness_issues(text,[p]))
        p=proof();p['completeness']['evidence_truncated']=True
        self.assertTrue(date_completeness_issues(text,[p]))
        self.assertFalse(date_completeness_issues('客户甲没有实际交局日期未核实的案件。[S1]',
            [proof(filters=[{'field':'client_alias','value':'客户甲'}])]))
        self.assertTrue(date_completeness_issues('10月没有实际交局日期未核实的案件。[S1]',[proof()]))

    def test_caution_and_supported_counts_are_not_global_absence(self):
        self.assertFalse(date_completeness_issues('## 日期缺失、无法归月或对不上的案件',[proof(count=75)]))
        self.assertFalse(date_completeness_issues('无法与计划日期比较先后。[S1]',[proof(count=75)]))
        for text in ('实际交局日期或依据未核实75件。[S1]',
                     '日期冲突为零不代表交局日期全部已核实。[S1]'):
            self.assertFalse(date_completeness_issues(text,[proof(count=75)]))

    def test_all_unknown_requires_entire_population_not_some_records(self):
        text='所有案件实际交局日期未核实。[S1]'
        self.assertTrue(date_completeness_issues(text,[proof(count=75)]))
        self.assertFalse(date_completeness_issues(text,[proof(count=100)]))

    def test_all_stages_unknown_dates_do_not_change_monthly_counts(self):
        rows=[{'case_id':'A','received_date':'2026-09-01','actual_filed_date':None,'stage':'技术交底'},
              {'case_id':'B','received_date':None,'actual_filed_date':'2026-10-01','filing_verified':False},
              {'case_id':'C','received_date':'2026-10-01','actual_filed_date':'2026-09-02','filing_verified':True}]
        for row in rows:row['case_key']=row['case_id']
        with patch('casework.store.list_cases',return_value=rows),patch('casework.store.coverage',return_value={}):
            result=analytics.report.__wrapped__('2026-09','2026-10')
        self.assertEqual([r['case_id']for r in result['unknown_filed']],['A','B'])
        self.assertEqual([r['case_id']for r in result['unknown_received']],['B'])
        self.assertEqual([(x['received_count'],x['filed_count'])for x in result['totals']],[(1,1),(1,0)])


class MonthlyIntegrityTests(unittest.TestCase):
    setUp=fixtures.ContractTests.setUp
    tearDown=fixtures.ContractTests.tearDown
    execute=fixtures.ContractTests.execute

    def test_complete_lists_are_executed_and_lost_row_is_rejected(self):
        from casework.verification import verify
        request,result=self.execute(dict(operation='monthly',start_month='2026-09',end_month='2026-10'))
        self.assertEqual(result['business_check']['status'],'passed',result['business_check']['issues'])
        contracts=result['business_delivery']['date_completeness']
        expected={c['case_id']for c in analytics.unique_cases(analytics.store.list_cases())[0]
                  if analytics.actual_date_unknown(c,'actual_filed_date')}
        self.assertEqual(contracts['unknown_filed']['matched_count'],len(expected))
        self.assertTrue(all(cid in result['report']for cid in expected))
        # A mutation cannot evade the actual source snapshot check.
        import json
        from tools.casework_tools import query_department_cases
        from api import context,source_policy
        context.set_session('monthly-mutation',str(self.base));source_policy.configure(['db_query'],[])
        try:
            entries=[]
            def add(thread,e):
                e={**e,'id':'S'+str(len(entries)+1)};entries.append(e);return e['id']
            with patch('api.evidence.add',side_effect=add):
                payload=json.loads(query_department_cases.invoke({'operation':'monthly','start_month':'2026-09','end_month':'2026-10'}))
            payload['unknown_filed'].pop()
            from casework.workflow import render
            checked=verify(request,payload,entries,render(payload,request))
            self.assertTrue(any('清单缺项' in x for x in checked['issues']))
        finally:
            context.clear_tool_budgets('monthly-mutation');context.reset_session()
