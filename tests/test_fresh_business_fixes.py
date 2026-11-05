"""Failures exposed by fresh synthetic cases; no old 36-question scoring."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

class FreshBusinessFixes(unittest.TestCase):
    def test_unknown_due_group_respects_same_active_scope_as_other_groups(self):
        from casework import analytics
        from tests import test_department_contracts as fixtures
        test=fixtures.ContractTests();test.setUp()
        try:
            with patch('casework.store.list_cases',return_value=[
                {'case_id':'S26P999001','stage':'已交局','draft_due_date':None},
                {'case_id':'S26P999002','stage':'已暂停','draft_due_date':None},
                {'case_id':'S26P999003','stage':'撰写中','draft_due_date':None},
                {'case_id':'S26P999004','stage':'撰写中','draft_due_date':'2026-12-04'},
            ]):
                result=analytics.cases_page(due_field='draft_due_date',due_statuses=['overdue','next14','unknown'],as_of='2026-12-03')
                self.assertEqual({r['case_id'] for r in result['rows']},{'S26P999003','S26P999004'})
        finally:test.tearDown()

    def test_relative_months_handles_previous_and_year_boundary(self):
        from datetime import date
        from casework.routing import months
        for now,query,expected in [(date(2027,1,4),'本月与前一个月',['2026-12','2027-01']),
             (date(2027,2,8),'上一个自然月和当前自然月',['2027-01','2027-02'])]:
            with patch('casework.analytics.today',return_value=now):self.assertEqual(months(query),expected)

    def test_explicit_workload_authority_does_not_depend_on_word_order(self):
        from casework.requirements import workload_authorized
        for query in ['按在办和期限给候选，经验不用查','先不管专业适配和旧案经验，仅凭在办量给接案顺序','只凭期限负荷推荐；无需评估技术经历','按负荷推荐，专业经历不参与']:
            self.assertTrue(workload_authorized(query),query)
        for query in ['按历史经历及期限负荷推荐','按负荷推荐但没找到技术经历','不能不查技术经历，再看负荷']:
            self.assertFalse(workload_authorized(query),query)

    def test_postposed_exclusion_allows_adverbs_but_not_double_negation(self):
        from casework.requirements import workload_authorized
        for query in ['专业和过去经验这次都不管，按已知在办量与期限负荷选接案人并排序。',
                      '技术适配这轮先不管，按负荷推荐。','历史经验本轮不考虑，仅按在办期限排候选。']:
            self.assertTrue(workload_authorized(query),query)
        for query in ['专业经验不能不管，按负荷给候选。','不是不考虑专业经验，只看负荷。',
                      '历史经验并非不考虑，结合负荷推荐。','查不到经验，只按在办负荷推荐。']:
            self.assertFalse(workload_authorized(query),query)

    def test_json_attachment_is_preserved_in_full_and_failed_read_is_not_evidence(self):
        from api import storage,context,evidence
        from tools.upload_file_read_tool import read_file_content
        from api.monitor import _record_tool_evidence
        from tools.review_evidence import pack_review_evidence
        with tempfile.TemporaryDirectory(prefix='fresh-json-') as tmp,patch.object(storage,'_DB_PATH',Path(tmp)/'app.db'):
            storage.init_db();context.set_session('fresh-json',tmp);evidence.reset('fresh-json')
            try:
                payload=[{'case_id':f'S26P{900000+i}','confirmed':i==29} for i in range(30)]
                (Path(tmp)/'ledger.json').write_text(json.dumps(payload))
                result=read_file_content.invoke({'filename':'ledger.json'})
                self.assertIn('S269',result.replace('P',''))
                saved=evidence.snapshot('fresh-json')[0]
                self.assertEqual(json.loads(saved['content']),payload)
                self.assertGreater(len(saved['content']),1200)
                review=pack_review_evidence([saved],'[S1]')
                self.assertIn('S26P900029',review)
                (Path(tmp)/'bad.json').write_text('{ invalid json')
                failed=read_file_content.invoke({'filename':'bad.json'})
                self.assertTrue(failed.startswith('文件读取失败：'))
                _record_tool_evidence('fresh-json','read_file_content',{'filename':'bad.json'},failed)
                _record_tool_evidence('fresh-json','read_file_content',{'filename':'x.bin'},'不支持的文件类型：.bin')
                self.assertEqual(len(evidence.snapshot('fresh-json')),1)
            finally:evidence.clear('fresh-json');context.reset_session()

    def test_json_table_counts_unique_cases_after_typed_filters(self):
        from tools.attachment_tables import query_table
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'new-ledger.json'
            p.write_text(json.dumps([
                {'case_id':'Q1','received':'2028-04-01','verified':True},
                {'case_id':'Q1','received':'2028-04-01','verified':True},
                {'case_id':'Q2','received':'2028-04-30','verified':False},
                {'case_id':'Q3','received':'2028-05-01','verified':True},
                {'case_id':'Q4','received':None,'verified':True}]))
            result=query_table(p,filters=[{'field':'case_id','op':'in','value':['Q1','Q2','Q3','Q4']},
                {'field':'received','op':'not_null'}, {'field':'received','op':'gte','value':'2028-04-01'},
                {'field':'received','op':'lt','value':'2028-05-01'}, {'field':'verified','value':True}],
                aggregates=[{'field':'case_id','op':'nunique','alias':'actual_count'}])
            self.assertEqual(result['rows'],[[1]])
            self.assertEqual(result['matched_rows'],2)
            self.assertFalse(result['truncated'])

    def test_review_format_retry_is_bounded_and_keeps_failed_verdict(self):
        import copy
        from tests.test_content_verification import review_fixture,review_model
        from tools.content_verification import ContentReviewDecision
        from agent import main_agent
        result,payload=review_fixture();payload['verdict']='failed'
        from tools.content_verification import review_anchor_index
        anchor=review_anchor_index(result)['task'][0]['id']
        for check in payload['checks']:
            check['task_anchor_id']=anchor
        payload['checks'][1].update(status='failed',action='revise_answer',suggestion='按真实数值修正')
        fixed=copy.deepcopy(payload);fixed['checks'][1]['answer_quote']='净销售额80元'
        payload['checks'][1]['task_anchor_id']=''  # First response lacks the required bound task location.
        model,reviewer=review_model()
        reviewer.ainvoke.side_effect=[{'parsed':ContentReviewDecision(**payload)}, {'parsed':ContentReviewDecision(**fixed)}]
        with patch.object(main_agent,'get_llm',return_value=model):
            issue=asyncio.run(main_agent.soft_review(result))
        self.assertEqual(reviewer.ainvoke.await_count,2)
        self.assertEqual(result['soft_review_status'],'failed')
        self.assertEqual(len(result['review_format_errors']),1)
        self.assertIn('不通过',issue)

    def test_compound_known_constraints_never_absorb_an_unsupported_suffix(self):
        from casework.workflow import DepartmentIntent,compile_intent
        from casework.requirements import normalize_constraints
        quote='日期未知另列，预定节点不能补位'
        intent=DepartmentIntent(requirements=[{'quote':'统计2028年6月实际收案交局','kind':'monthly_counts'},
            {'quote':quote,'kind':'other'}],start_month='2028-06',end_month='2028-06',
            constraints=[{'quote':'日期未知另列','kind':'unknown_separate'}])
        normalized,changes=normalize_constraints(intent)
        self.assertFalse(any(r.kind=='other' for r in normalized.requirements))
        self.assertEqual(compile_intent(intent,'统计2028年6月实际收案交局，'+quote).operation,'monthly')
        intent.requirements[-1].quote+='，再计算增长率'
        normalized,_=normalize_constraints(intent)
        self.assertTrue(any(r.kind=='other' for r in normalized.requirements))

if __name__=='__main__':unittest.main()
