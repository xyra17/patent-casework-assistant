"""统计声明抽取不能授予通过；值、标签、范围和覆盖均由程序验证。"""
import asyncio
import copy
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from tools.sql_ast import bound_tree
from tools.statistical_facts import build_contract
from tools.statistics_check import validate_claims, check_statistics, statement_units
from tools.reflect_check import hard_check

DEFINITION={'key':'total_sales','label':'销售总额','synonyms':['销售额','营收'],'table':'sales_records','expression':'SUM(total_amount)','unit':'元'}
SCHEMA={'sale_date':{'role':'time'},'total_amount':{'role':'metric','label':'销售金额','unit':'元'},'region':{'role':'dimension','label':'地区'}}

def source(value=1000000, *, sql=None, params=None, columns=None, rows=None, definitions=None, trusted=True, semantic=None):
    item={'id':'S1','type':'database','sql':sql or 'SELECT SUM(total_amount) AS total_sales FROM sales_records WHERE YEAR(sale_date)=%s LIMIT %s',
          'params':params if params is not None else ([2025,101] if sql is None else []),
          'columns':columns or ['total_sales'],'rows':rows if rows is not None else [[value]],
          'semantic':semantic or {'total_sales':{'role':'metric','label':'销售总额','unit':'元'}}}
    item['statistics']=build_contract(item,definitions if definitions is not None else [DEFINITION],SCHEMA,trusted_semantic=trusted)
    return item


def claim(metric='销售额',value='100万元',unit=0,occurrence=0):
    return {'unit_id':unit,'metric_text':metric,'value_text':value,'occurrence':occurrence}


def verify(text, item=None, claims=None):
    return validate_claims(text,[item or source()],claims if claims is not None else [claim()])


class StatisticChecks(unittest.TestCase):
    def test_metric_label_strips_scope_parentheticals_only(self):
        from tools.statistics_check import metric_label
        self.assertEqual(metric_label('筛选案件数量（同一筛选范围）'), '筛选案件数量')
        self.assertEqual(metric_label('2026年7月实际收案（received_date 落在2026-07-01至2026-07-31）'), '实际收案')
        self.assertEqual(metric_label('净销售额（不含税）'), '净销售额（不含税）')
        self.assertEqual(metric_label('实际收案数量合计'), '实际收案数量合计')

    def test_equivalent_money_units_and_exact_decimal(self):
        for value,text,shown in [(1000000,'2025年销售额100万元[S1]','100万元'),
            (Decimal('1234.56'),'2025年销售额1,234.56元[S1]','1,234.56元'),
            (Decimal('0.10'),'2025年销售额0.10元[S1]','0.10元')]:
            with self.subTest(text=text): self.assertEqual(verify(text,source(value),[claim(value=shown)])['status'],'passed')
    def test_mysql_decimal_65_digits_never_rounds_before_comparison(self):
        value='12345678901234567890123456789012345678901234567890123456789012345'
        item=source(Decimal(value))
        self.assertEqual(verify('2025年销售额'+value+'元[S1]',item,[claim(value=value+'元')])['status'],'passed')
        rounded=value[:60]+'00000'
        self.assertEqual(verify('2025年销售额'+rounded+'元[S1]',item,[claim(value=rounded+'元')])['status'],'failed')

    def test_number_year_and_metric_mismatch_each_fail(self):
        for text,extracted in [('2025年销售额101万元[S1]',claim(value='101万元')),
                              ('2026年销售额100万元[S1]',claim()),('2025年利润100万元[S1]',claim(metric='利润'))]:
            self.assertEqual(verify(text,claims=[extracted])['status'],'failed')
    def test_rounding_requires_explicit_approximation(self):
        item=source(1001234)
        self.assertEqual(verify('2025年销售额100.12万元[S1]',item,[claim(value='100.12万元')])['status'],'failed')
        self.assertEqual(verify('2025年销售额约100.12万元[S1]',item,[claim(value='100.12万元')])['status'],'passed')
    def test_bare_number_with_source_reference_has_no_phantom_coverage_gap(self):
        self.assertEqual(verify('2025年销售额1000000[S1]',claims=[claim(value='1000000')])['status'],'passed')

    def test_count_result_one_row_does_not_mean_one_record_and_large_ids_preserve_precision(self):
        item=source(sql='SELECT COUNT(*) FROM items',columns=['COUNT(*)'],rows=[[1205]],definitions=[],trusted=False,semantic={})
        self.assertEqual(verify('记录数1205条[S1]',item,[claim('记录数','1205条')])['status'],'passed')
        self.assertEqual(verify('记录数1条[S1]',item,[claim('记录数','1条')])['status'],'failed')
        item['rows']=[[9007199254740993]]
        self.assertEqual(verify('记录数9007199254740993条[S1]',item,[claim('记录数','9007199254740993条')])['status'],'passed')
    def test_null_and_missing_cells_cannot_become_zero(self):
        for rows in ([[None]], [], [[]]):
            self.assertEqual(verify('2025年销售额0元[S1]',source(rows=rows),[claim(value='0元')])['status'],'failed')
    def test_percent_and_percentage_points_are_distinct(self):
        definition={**DEFINITION,'key':'rate','label':'完成率','synonyms':[],'unit':'%'}
        item=source(sql='SELECT SUM(total_amount) AS rate FROM sales_records',columns=['rate'],rows=[[12.5]],definitions=[definition],semantic={'rate':{'role':'metric'}})
        self.assertEqual(verify('完成率12.5%[S1]',item,[claim('完成率','12.5%')])['status'],'passed')
        self.assertEqual(verify('完成率12.5个百分点[S1]',item,[claim('完成率','12.5个百分点')])['status'],'failed')
    def test_sql_alias_does_not_rename_sales_to_profit_or_units(self):
        item=source(sql='SELECT SUM(total_amount) AS profit FROM sales_records',columns=['profit'],trusted=False,semantic={'profit':{'role':'metric'}})
        self.assertEqual(item['statistics']['metrics'][0]['label'],'销售总额')
        self.assertEqual(verify('利润100万元[S1]',item,[claim('利润')])['status'],'failed')
        self.assertEqual(verify('销售额100万元[S1]',item)['status'],'passed')
        wrong_table=source(sql='SELECT SUM(total_amount) AS total_sales FROM another_table',trusted=False)
        self.assertFalse(wrong_table['statistics']['metrics'][0]['definition_verified'])
    def test_join_alias_lineage_cannot_borrow_another_tables_metric_label(self):
        item=source(sql='SELECT SUM(r.total_amount) AS total_sales FROM sales_records s JOIN returns r ON s.id=r.id',trusted=False)
        self.assertFalse(item['statistics']['metrics'][0]['definition_verified'])
    def test_bound_parameters_are_lexical_and_literal_percent_s_is_not_a_placeholder(self):
        tree=bound_tree("SELECT SUM(CASE WHEN status=%s THEN x ELSE 0 END) FROM t WHERE YEAR(d)=%s AND tag='%s' LIMIT %s",['done',2025,101])
        self.assertIn('2025',tree.args['where'].sql())
        self.assertIn("'%s'",tree.sql())
        self.assertIn('101',tree.args['limit'].sql())
        with self.assertRaises(ValueError):bound_tree('SELECT %s',[1,2])
    def test_date_year_month_and_quarter_scope_boundaries(self):
        sql='SELECT SUM(total_amount) AS total_sales FROM sales_records WHERE sale_date >= %s AND sale_date < %s LIMIT %s'
        for start,end,period in [('2025-01-01','2026-01-01','2025年'),('2025-01-01','2025-04-01','2025年第一季度'),('2025-02-01','2025-03-01','2025年2月')]:
            item=source(sql=sql,params=[start,end,101])
            self.assertEqual(verify(period+'销售额100万元[S1]',item)['status'],'passed')
        item=source(sql=sql,params=['2025-01-01','2025-12-31',101])
        self.assertEqual(verify('2025年销售额100万元[S1]',item)['status'],'failed')
    def test_unbounded_relative_and_or_time_scopes_never_confirm_specific_year(self):
        for sql in ['SELECT SUM(total_amount) AS total_sales FROM sales_records',
            'SELECT SUM(total_amount) AS total_sales FROM sales_records WHERE sale_date >= CURRENT_DATE',
            "SELECT SUM(total_amount) AS total_sales FROM sales_records WHERE YEAR(sale_date)=2025 OR YEAR(sale_date)=2026"]:
            self.assertEqual(verify('2025年销售额100万元[S1]',source(sql=sql))['status'],'failed')
    def test_group_dimensions_and_equal_values_do_not_prove_another_region(self):
        item=source(sql='SELECT region, SUM(total_amount) AS total_sales FROM sales_records WHERE YEAR(sale_date)=2025 GROUP BY region',
                    columns=['region','total_sales'],rows=[['华东',1000000]],semantic={'region':{'role':'geo'},'total_sales':{'role':'metric'}})
        self.assertEqual(verify('2025年华东销售额100万元[S1]',item)['status'],'passed')
        self.assertEqual(verify('2025年华南销售额100万元[S1]',item)['status'],'failed')
    def test_table_headers_units_year_dimension_and_values_are_checked_together(self):
        item=source(sql='SELECT YEAR(sale_date) AS year, SUM(total_amount) AS total_sales FROM sales_records GROUP BY year',
            columns=['year','total_sales'],rows=[[2025,1000000]],semantic={'year':{'role':'time','label':'年份'},'total_sales':{'role':'metric'}})
        text='| 年份 | 销售额（万元） |\n|---|---|\n|2025|100|'
        self.assertEqual(verify(text,item,[claim(value='100')])['status'],'passed')
        self.assertEqual(verify(text,item,[claim(metric='销售额（万元）',value='100')])['status'],'passed')
        self.assertEqual(verify(text.replace('2025','2026'),item,[claim(value='100')])['status'],'failed')
        self.assertEqual(verify(text.replace('销售额','利润'),item,[claim('利润','100')])['status'],'failed')
    def test_coverage_rejects_empty_omitted_repeated_and_partial_claims(self):
        self.assertEqual(verify('2025年销售额100万元[S1]',claims=[])['status'],'failed')
        self.assertEqual(verify('2025年销售额100.5万元[S1]',source(1005000),[claim(value='100')])['status'],'failed')
        text='2025年销售额100万元[S1]，销售额100万元[S1]'
        self.assertEqual(verify(text,claims=[claim()])['status'],'failed')
        self.assertEqual(verify(text,claims=[claim(),claim(occurrence=1)])['status'],'passed')
    def test_closest_clause_metric_cannot_be_borrowed_for_another_value(self):
        result=verify('2025年销售额100万元[S1]，利润100万元[S1]',claims=[claim(),claim(occurrence=1)])
        self.assertEqual(result['status'],'failed')
    def test_citation_must_match_the_actual_source(self):
        self.assertEqual(verify('2025年销售额100万元[S9]')['status'],'failed')
    def test_failure_is_hard_issue_and_no_data_is_not_a_false_success(self):
        checked=verify('2026年销售额100万元[S1]')
        issues=hard_check({'report':'2026年销售额100万元[S1]','require_report':False,'statistics_check':checked})
        self.assertTrue(any('时间范围' in issue for issue in issues))
    def test_chinese_numbers_are_not_a_coverage_bypass(self):
        self.assertEqual(verify('2025年销售额一百万元[S1]',claims=[claim(value='一百万元')])['status'],'passed')
        self.assertEqual(verify('2025年销售额一百万元[S1]',claims=[])['status'],'failed')

    def test_foreign_currency_and_scaled_count_units_cannot_be_silently_ignored(self):
        for value in ['100美元','100美金','100万盒']:
            self.assertEqual(verify('2025年销售额'+value+'[S1]',source(100),[claim(value=value)])['status'],'failed')

    def test_year_dimension_cannot_expand_filtered_month_or_ignore_relative_time(self):
        columns=['year','total_sales']; semantic={'year':{'role':'time','label':'年份'},'total_sales':{'role':'metric'}}
        item=source(sql="SELECT YEAR(sale_date) AS year, SUM(total_amount) AS total_sales FROM sales_records WHERE sale_date >= '2025-02-01' AND sale_date < '2025-03-01' GROUP BY year",columns=columns,rows=[[2025,1000000]],semantic=semantic)
        self.assertEqual(verify('2025年销售额100万元[S1]',item)['status'],'failed')
        self.assertEqual(verify('2025年2月销售额100万元[S1]',item)['status'],'passed')
        relative=source(sql='SELECT YEAR(sale_date) AS year, SUM(total_amount) AS total_sales FROM sales_records WHERE sale_date >= CURRENT_DATE GROUP BY year',columns=columns,rows=[[2025,1000000]],semantic=semantic)
        self.assertEqual(verify('2025年销售额100万元[S1]',relative)['status'],'failed')

    def test_report_heading_conflicts_are_not_overridden_by_row_year(self):
        text='## 2026年销售分析\n2025年销售额100万元[S1]'
        self.assertEqual(verify(text)['status'],'failed')

    def test_numeric_group_labels_are_not_mistaken_for_metric_values(self):
        item=source(sql='SELECT region, SUM(total_amount) AS total_sales FROM sales_records GROUP BY region',columns=['region','total_sales'],rows=[['region-0',1000000]],semantic={'region':{'role':'dimension'},'total_sales':{'role':'metric'}})
        self.assertEqual(verify('region-0销售额100万元[S1]',item)['status'],'passed')

    def test_derived_comparisons_and_unrecognized_magnitudes_are_not_exact_value_claims(self):
        for text,value,item in [('2025年销售额增长100万元[S1]','100万元',source()),
                                ('2025年销售额超过100万元[S1]','100万元',source()),
                                ('2025年销售额1千亿元[S1]','1',source(1))]:
            self.assertEqual(verify(text,item,[claim(value=value)])['status'],'failed')

    def test_only_actually_executed_rule_versions_grant_metric_aliases(self):
        from tools.semantic_layer import SemanticLayer
        from tools.semantic_query import SemanticQueryEngine
        layer=SemanticLayer(Path('tests/fixtures/legacy_semantic.yaml'));layer._introspect_enabled=False
        engine=SemanticQueryEngine(layer)
        item=source();item['metric']={**DEFINITION,'rule_versions':[{'id':'r1','revision':1}]}
        rule={'id':'r1','revision':1,'binding':{'metric_key':'total_sales'},'subject':'净销售额','match_terms':[],'aliases':['扣退货后的收入']}
        with patch('rules.engine.business_rules',return_value=[rule]):
            item['statistics']=engine._statistical_contract(item)
        self.assertEqual(verify('2025年净销售额100万元[S1]',item,[claim('净销售额')])['status'],'passed')
        with patch('rules.engine.business_rules',return_value=[{**rule,'revision':2}]):
            item['statistics']=engine._statistical_contract(item)
        self.assertEqual(verify('2025年净销售额100万元[S1]',item,[claim('净销售额')])['status'],'failed')


class StatisticalExtractionTests(unittest.TestCase):
    def extract(self,report,response=None,error=None,evidence=None):
        model=AsyncMock()
        if error:model.ainvoke.side_effect=error
        else:model.ainvoke.return_value=response
        llm=Mock();llm.with_structured_output.return_value=model
        with patch('agent.llm.get_llm',return_value=llm):
            result=asyncio.run(check_statistics(report,evidence if evidence is not None else [source()]))
        return result,model
    def test_model_only_extracts_and_cannot_approve_wrong_data(self):
        result,_=self.extract('2026年销售额100万元[S1]',{'claims':[claim()]})
        self.assertEqual(result['status'],'failed')
    def test_extraction_error_and_malformed_output_are_unverified(self):
        for response,error in [(None,TimeoutError()),({},None),({'claims':[]},None)]:
            result,_=self.extract('2025年销售额100万元[S1]',response,error)
            self.assertNotEqual(result['status'],'passed')
    def test_non_database_answer_and_explicit_other_source_need_no_database_check(self):
        for report,items in [('一句制度原文',[]),('2025年公开市场规模100亿元[S2]',[source(),{'id':'S2','type':'web'}])]:
            result,model=self.extract(report,evidence=items)
            self.assertEqual(result['status'],'not_required');model.ainvoke.assert_not_called()
    def test_mixed_sources_do_not_drop_required_database_checks(self):
        result,_=self.extract('2025年销售额100万元[S1]\n市场规模300亿元[S2]',{'claims':[claim()]},evidence=[source(),{'id':'S2','type':'web'}])
        self.assertEqual(result['status'],'passed')
    def test_query_result_rechecks_projected_content_and_hides_unsafe_short_answer(self):
        from api.query_result import checked_projection,payload
        with patch('tools.statistics_check.check_statistics',AsyncMock(return_value={'status':'failed','checked_claims':0,'checks':[],'issues':['缺少时间范围']})):
            result=asyncio.run(checked_projection(payload('销售额100万元'),[source()]))
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(result['statistical_validation_version'],1)
        self.assertEqual(result['statistics_check']['status'],'failed')

    def test_unknown_numeric_result_is_unverified_instead_of_silently_not_required(self):
        item=source(sql='SELECT x FROM unknown_table',columns=['x'],rows=[[100]],definitions=[],trusted=False,semantic={'x':{'role':'dimension'}})
        result,model=self.extract('销售额100万元[S1]',evidence=[item])
        self.assertEqual(result['status'],'unverified');model.ainvoke.assert_not_called()


class StatisticalFlowTests(unittest.TestCase):
    def setUp(self):
        from api import storage, context, source_policy
        from utils import path_utils
        from rules import engine
        self.temp=tempfile.TemporaryDirectory(prefix='statistical-flow-')
        self.root=Path(self.temp.name)
        self.patches=[patch.object(storage,'_DB_PATH',self.root/'app.db'),
                      patch.object(path_utils,'OUTPUT_DIR',self.root/'output'),
                      patch.object(path_utils,'UPLOAD_DIR',self.root/'upload')]
        for item in self.patches:item.start()
        storage.init_db();engine.reset()
        self.context,self.policy,self.storage=context,source_policy,storage
    def tearDown(self):
        from api import evidence
        self.context.reset_session();self.policy.reset_policy();evidence.clear('statistics-flow')
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()
    def model(self,response=None,error=None):
        model=AsyncMock()
        if error:model.ainvoke.side_effect=error
        else:model.ainvoke.return_value=response or {'claims':[claim()]}
        llm=Mock();llm.with_structured_output.return_value=model
        return llm
    def plan(self):
        from agent.main_agent import TaskPlan
        return TaskPlan(task_type='qa',require_report=False,require_pdf=False,required_sources=['db_query'],steps=['统计销售额'],output_artifacts=[],assumptions=[])
    def test_statistics_failure_enters_next_round_with_exact_source_and_feedback(self):
        from agent import main_agent
        first={'report':'2026年销售额100万元[S1]','evidence':[source()],'called_sources':['db_query'],'pdf_produced':False}
        second={**first,'report':'2025年销售额100万元[S1]'}
        async def run():
            self.storage.create_run('statistics-flow','销售额','销售额',False)
            with patch.object(main_agent,'build_task_plan',AsyncMock(return_value=self.plan())), \
                 patch.object(main_agent,'main_agent_run',AsyncMock(side_effect=[first,second])) as runner, \
                 patch.object(main_agent,'soft_review',AsyncMock(return_value=None)) as reviewer, \
                 patch('agent.llm.get_llm',return_value=self.model()):
                result=await main_agent.run_deep_agent('只查数据库，统计2025年销售额','statistics-flow',2)
            self.assertEqual(result,second['report']);self.assertEqual(reviewer.await_count,1)
            self.assertIn('统计核验',runner.await_args_list[1].args[0])
            self.assertIn('时间范围',runner.await_args_list[1].args[0])
            repair_data='\n'.join(m.content for m in runner.await_args_list[1].kwargs['revision_context'])
            self.assertIn('销售总额',repair_data)
            events=self.storage.get_run('statistics-flow')['events']
            checks=[e['data']['statistics_check']['status'] for e in events if e['event']=='reflection']
            self.assertEqual(checks,['failed','passed'])
        asyncio.run(run())
    def test_extraction_outage_at_round_limit_is_degraded_not_approved(self):
        from agent import main_agent
        result={'report':'2025年销售额100万元[S1]','evidence':[source()],'called_sources':['db_query'],'pdf_produced':False}
        async def run():
            self.storage.create_run('statistics-flow','销售额','销售额',False)
            with patch.object(main_agent,'build_task_plan',AsyncMock(return_value=self.plan())), \
                 patch.object(main_agent,'main_agent_run',AsyncMock(return_value=result)), \
                 patch.object(main_agent,'soft_review',AsyncMock(return_value=None)) as reviewer, \
                 patch('agent.llm.get_llm',return_value=self.model(error=TimeoutError())):
                text=await main_agent.run_deep_agent('只查数据库，统计2025年销售额','statistics-flow',1)
            self.assertIn('【未达标说明】',text);self.assertIn('统计',text);reviewer.assert_not_awaited()
        asyncio.run(run())
    def test_cached_legacy_database_answer_cannot_bypass_new_projection_check(self):
        from fastapi.testclient import TestClient
        from api import server
        self.storage.create_run('statistics-flow','销售额','销售额',False)
        self.storage.append_event('statistics-flow',{'event':'task_result','data':{'result':'2026年销售额100万元[S1]','evidence':[source()],
            'query_result':{'content':'2026年销售额100万元[S1]','status':'ready','mode':'extractive','evidence_ids':['S1']}}})
        with TestClient(server.app) as client, patch('agent.llm.get_llm',return_value=self.model()):
            response=client.post('/api/runs/statistics-flow/query-result')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['status'],'unavailable')
            with patch.object(server,'build_query_result',side_effect=AssertionError('cached after verification')):
                self.assertEqual(client.post('/api/runs/statistics-flow/query-result').json(),response.json())
    def test_successful_short_answer_keeps_verified_sources_and_audit(self):
        from api.query_result import build_query_result
        with patch('agent.llm.get_llm',return_value=self.model()):
            result=asyncio.run(build_query_result('销售额','2025年销售额100万元',[source()]))
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['statistics_check']['status'],'passed')
        self.assertEqual(result['evidence_ids'],['S1'])
    def test_statistical_definition_snapshot_participates_in_evidence_identity(self):
        from api import evidence
        self.storage.create_run('statistics-flow','销售额','销售额',False)
        evidence.reset('statistics-flow')
        item=source();first=evidence.add('statistics-flow',item)
        changed=copy.deepcopy(item);changed['statistics']['metrics'][0]['unit']='万元'
        self.assertNotEqual(first,evidence.add('statistics-flow',changed))


def test_indefinite_example_is_not_a_database_quantity():
    from tools.statistics_check import statement_units
    assert statement_units('该句被标注为关键反例，即一个负例样本，用于测试。')==[]
    assert statement_units('共有一个负例样本。')
    assert statement_units('本月收到一个案件。')


def department_metric(column,label,names,unit,time,index=0):
    return {'column':column,'index':index,'label':label,'names':names,'unit':unit,
            'definition_verified':True,'time':time,'filters':[]}


def test_range_total_and_change_percentage_are_verifiable():
    """A quarter total and a month-over-month percentage come from the program."""
    quarter={'status':'bounded','start':'2026-07-01','end':'2026-10-01','field':'received_date'}
    total={'id':'S9','type':'database','columns':['实际收案数量合计'],'rows':[[28]],
           'statistics':{'version':1,'dimensions':[],
               'metrics':[department_metric('received_total_count','实际收案数量合计',['实际收案数量合计'],'件',quarter)]}}
    text='## 2026年第三季度\n2026年第三季度实际收案数量合计28件 [S9]。'
    check=validate_claims(text,[total],[{'unit_id':0,'metric_text':'实际收案数量合计','value_text':'28件','occurrence':0}])
    assert check['status']=='passed', check
    wrong=validate_claims(text.replace('28件','27件'),[total],
        [{'unit_id':0,'metric_text':'实际收案数量合计','value_text':'27件','occurrence':0}])
    assert wrong['status']=='failed'
    comparison={'status':'bounded','start':'2026-09-01','end':'2026-11-01','field':'month_comparison'}
    pct={'id':'S17','type':'database','columns':['实际收案数量变化百分比'],'rows':[[-25.0]],
         'statistics':{'version':1,'dimensions':[],
             'metrics':[department_metric('received_change_pct','实际收案数量变化百分比',
                                          ['实际收案数量变化百分比','收案数量变化百分比'],'%',comparison)]}}
    text='2026年10月相比2026年9月：实际收案数量变化百分比-25% [S17]。'
    check=validate_claims(text,[pct],[{'unit_id':0,'metric_text':'实际收案数量变化百分比','value_text':'-25%','occurrence':0}])
    assert check['status']=='passed', check
    # A percentage without the two-month scope cannot be verified.
    missing_scope='实际收案数量变化百分比-25%。'
    assert validate_claims(missing_scope,[pct],
        [{'unit_id':0,'metric_text':'实际收案数量变化百分比','value_text':'-25%','occurrence':0}])['status']=='failed'


def month_item(value,month,names=('实际收案数量','实际收案')):
    time={'status':'bounded','start':f'2026-{month:02d}-01','end':f'2026-{month+1:02d}-01',
          'field':'received_date'}
    return {'id':'S2','type':'database','columns':['实际收案数量'],'rows':[[value]],
            'statistics':{'version':1,'dimensions':[],
                'metrics':[department_metric('received_count','实际收案数量',list(names),'件',time)]}}


def test_number_unit_space_and_time_only_month_rows():
    """'共 13 件' and '7月2件' are the same facts, not unverifiable wording."""
    spaced=validate_claims('2026年7月实际收案共 13 件 [S2]。',[month_item(13,7)],
        [{'unit_id':0,'metric_text':'实际收案','value_text':'13 件','occurrence':0}])
    assert spaced['status']=='passed', spaced
    wrong=validate_claims('2026年7月实际收案共 99 件 [S2]。',[month_item(13,7)],
        [{'unit_id':0,'metric_text':'实际收案','value_text':'99 件','occurrence':0}])
    assert wrong['status']=='failed'
    # A month and a value are enough; the month still has to agree.
    assert validate_claims('2026年7月2件 [S2]。',[month_item(2,7)],
        [{'unit_id':0,'metric_text':'7月','value_text':'2件','occurrence':0}])['status']=='passed'
    assert validate_claims('2026年7月3件 [S2]。',[month_item(2,7)],
        [{'unit_id':0,'metric_text':'7月','value_text':'3件','occurrence':0}])['status']=='failed'
    assert validate_claims('2026年8月2件 [S2]。',[month_item(2,7)],
        [{'unit_id':0,'metric_text':'8月','value_text':'2件','occurrence':0}])['status']=='failed'
    # Without any month or metric name a bare number stays unverifiable.
    assert validate_claims('共2件 [S2]。',[month_item(2,7)],
        [{'unit_id':0,'metric_text':'本月','value_text':'2件','occurrence':0}])['status']=='failed'


def test_filter_scope_may_be_stated_once_for_the_whole_report():
    from tools.casework_tools import _metric_contract
    item={'id':'S7','type':'database','columns':['实际收案数量'],'rows':[[2]],
          'statistics':{'version':1,'dimensions':[],'metrics':[
              _metric_contract('received_count',0,
                  {'status':'bounded','start':'2026-07-01','end':'2026-08-01','field':'received_date'},
                  [],[{'field':'client_alias','value':'测试客户03（模拟）'}])]}}
    claims=[{'unit_id':0,'metric_text':'实际收案数量（件）','value_text':'2','occurrence':0}]
    table='| 月份 | 实际收案数量（件） |\n| --- | --- |\n| 2026年7月 | 2 [S7] |\n'
    assert validate_claims('统计范围：客户＝测试客户03（模拟）。\n\n'+table,[item],claims)['status']=='passed'
    assert validate_claims(table,[item],claims)['status']=='failed'
