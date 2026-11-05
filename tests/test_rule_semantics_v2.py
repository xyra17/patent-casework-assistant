"""Hybrid recall, bounded formulas and task-only partial overrides; isolated fixtures."""
import asyncio
import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
os.environ['APP_DB_PATH']=str(Path(tempfile.mkdtemp(prefix='rules-v2-import-'))/'app.db')
from api import storage
from fastapi.testclient import TestClient
from api.server import app
from rules import engine,store,retrieval
from rules.models import RuleInput,MetricBinding
from rules.engine import ReviewItem,BindingPatch,FilterChange,Review
from rules.semantic import validate_binding
from tools.semantic_layer import SemanticLayer
from tools.semantic_query import SemanticQueryEngine


def rule(**changes):
    return {'title':'净销售额','category':'business','subject':'销售额','content':'销售额扣除已完成退货。','match_terms':['净销售额'],'aliases':['扣退货后的收入'],'scope_note':'销售统计','binding':{'metric_key':'total_sales','filters':[]},'active':True,**changes}


def node(op,**parts):return {'op':op,**parts}


class SemanticRulesV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.dbpatch=patch.object(storage,'_DB_PATH',self.root/'app.db');self.dbpatch.start();storage.init_db()
        self.model=SemanticLayer(Path('tests/fixtures/legacy_semantic.yaml'));self.model._introspect_enabled=False
        self.model._tables['sales_records']['columns']['payment_date']={'role':'time','type':'date','label':'付款日期'}
        self.model._tables['returns']={'primary_key':'return_id','columns':{'return_id':{'role':'id'},'sale_id':{'role':'foreign_key'},'amount':{'role':'metric'},'status':{'role':'dimension'}}}
        self.model._config['relationships'].append({'from':'returns.sale_id','to':'sales_records.sale_id','type':'many_to_one'})
        self.client=TestClient(app);engine.reset()
    def tearDown(self):
        if hasattr(self,'conn'):self.conn.close()
        engine.reset();self.dbpatch.stop();self.tmp.cleanup()
    def save(self,data):return store.save_rule(RuleInput.model_validate(data).model_dump())
    def adopt(self,binding):
        saved=self.save(rule(binding=binding));engine.configure({'applied':[saved]});return saved
    def formula(self):
        return {'metric_key':'total_sales','formula_aggregation':'sum','formula':node('sub',args=[node('field',field='sales_records.total_amount'),node('related_sum',field='returns.amount',filters=[{'field':'returns.status','op':'eq','value':'完成'}])]),'filters':[{'field':'sales_records.region','op':'eq','value':'华东'}]}
    def sqlite(self):
        self.conn=conn=sqlite3.connect(':memory:')
        conn.executescript("CREATE TABLE sales_records(sale_id integer,total_amount real,region text,sale_date text,payment_date text);CREATE TABLE returns(return_id integer,sale_id integer,amount real,status text);INSERT INTO sales_records VALUES(1,100,'华东','2026-01-01','2026-02-01'),(2,200,'华东','2026-01-01','2026-02-01'),(3,500,'华西','2026-01-01','2026-02-01');INSERT INTO returns VALUES(1,1,10,'完成'),(2,1,20,'完成'),(3,1,60,'处理中'),(4,2,40,'完成');")
        return conn
    def test_related_formula_actual_sql_no_fanout_and_parameter_order(self):
        binding=self.formula();validate_binding(binding,self.model);self.adopt(binding)
        plan=SemanticQueryEngine(self.model).plan_query('净销售额')
        self.assertEqual(plan['params'],['完成','华东',100]);self.assertNotIn('LEFT JOIN `returns`',plan['sql'])
        with self.sqlite() as conn:rows=conn.execute(plan['sql'].replace('%s','?'),plan['params']).fetchall()
        self.assertEqual(rows,[(230.0,)]) # 100-10-20 + 200-40; never 2*100.
    def test_ratio_of_aggregates_differs_from_sum_of_row_ratios(self):
        net=self.formula()['formula']
        binding={'metric_key':'total_sales','formula_mode':'aggregate','formula_aggregation':'sum','formula':node('div',args=[net,node('field',field='sales_records.total_amount')]),'filters':[{'field':'sales_records.region','op':'eq','value':'华东'}]}
        self.adopt(binding);plan=SemanticQueryEngine(self.model).plan_query('total_sales')
        with self.sqlite() as conn:value=conn.execute(plan['sql'].replace('%s','?'),plan['params']).fetchone()[0]
        self.assertAlmostEqual(value,230/300)

    def test_resolve_retains_partial_snapshot_and_rejects_whole_rule_loss(self):
        original=self.save(rule(binding={**self.formula(),'time_field':'sales_records.sale_date'}))
        query='净销售额本次按付款日期统计'
        decision=Review(decisions=[ReviewItem(rule_id=original['id'],action='partial_override',reason='只改日期',current_quote=query,patch=BindingPatch(time_field='sales_records.payment_date'))])
        from unittest.mock import AsyncMock
        with patch('tools.semantic_layer.semantic_layer',self.model),patch.object(engine,'review_candidates',AsyncMock(return_value=decision)):
            resolved=asyncio.run(engine.resolve(query))
        self.assertFalse(resolved['conflicts']);self.assertEqual(resolved['applied'][0]['binding']['time_field'],'sales_records.payment_date')
        self.assertEqual(store.get_rule(original['id'])['binding']['time_field'],'sales_records.sale_date')
        decision.decisions[0].action='current_overrides'
        with patch('tools.semantic_layer.semantic_layer',self.model),patch.object(engine,'review_candidates',AsyncMock(return_value=decision)):
            resolved=asyncio.run(engine.resolve(query))
        self.assertFalse(resolved['overridden']);self.assertTrue(resolved['conflicts'])

    def test_multimetric_formula_preserves_select_then_where_parameters(self):
        self.adopt(self.formula());query=SemanticQueryEngine(self.model).plan_multi(['total_sales'])
        self.assertEqual(query['params'],['完成','华东',100])
        with self.sqlite() as conn:self.assertEqual(conn.execute(query['sql'].replace('%s','?'),query['params']).fetchall(),[(230.0,)])
    def test_arithmetic_constants_zero_denominator_and_unknown_fields(self):
        binding={'metric_key':'total_sales','formula':node('div',args=[node('field',field='sales_records.total_amount'),node('number',value=0)]),'filters':[]}
        self.adopt(binding);plan=SemanticQueryEngine(self.model).plan_query('total_sales')
        with self.sqlite() as conn:self.assertIsNone(conn.execute(plan['sql'].replace('%s','?'),plan['params']).fetchone()[0])
        for bad in [node('field',field='sales_records.tax_missing'),node('field',field='returns.amount'),node('related_sum',field='inventory.quantity_on_hand'),node('add',args=[]),node('number',value=float('inf'))]:
            with self.assertRaises((ValueError,TypeError)):validate_binding({**binding,'formula':bad},self.model)
    def test_formula_depth_and_fanout_dimension_are_rejected(self):
        formula=node('field',field='sales_records.total_amount')
        for _ in range(8):formula=node('add',args=[formula,node('number',value=1)])
        with self.assertRaisesRegex(ValueError,'节点|层'):validate_binding({'metric_key':'total_sales','formula':formula},self.model)
        self.adopt(self.formula())
        with self.assertRaisesRegex(ValueError,'重复累计'):SemanticQueryEngine(self.model).plan_query('total_sales',['returns.status'])
    def test_partial_override_preserves_formula_other_filters_and_original_revision(self):
        original=self.save(rule(binding={**self.formula(),'time_field':'sales_records.sale_date'}))
        query='本次销售额按付款日期统计，销售区域只统计华西'
        item=ReviewItem(rule_id=original['id'],action='partial_override',reason='当前要求覆盖两个默认值',current_quote=query,patch=BindingPatch(time_field='sales_records.payment_date',filters=[FilterChange(field='sales_records.region',op='eq',value='华西')]))
        changed=engine.apply_patch(original,item,query,self.model)
        self.assertEqual(changed['binding']['formula'],original['binding']['formula']);self.assertEqual(len(changed['local_overrides']),2)
        self.assertEqual(store.get_rule(original['id'])['binding']['filters'][0]['value'],'华东')
        engine.configure({'applied':[changed]});plan=SemanticQueryEngine(self.model).plan_query('total_sales',['sales_records.sale_date'],{'sales_records.sale_date':{'op':'gte','value':'2026-02-01'}})
        self.assertIn('t0.`payment_date`',plan['sql']);self.assertNotIn('t0.`sale_date`',plan['sql']);self.assertIn('华西',plan['params'])
        with self.assertRaisesRegex(ValueError,'真实|明确|指定'):engine.apply_patch(original,item,'完全不同的要求',self.model)
    def test_time_hierarchy_and_drilldown_follow_confirmed_basis(self):
        self.adopt({'metric_key':'total_sales','time_field':'sales_records.payment_date','filters':[]})
        plan,_=SemanticQueryEngine(self.model).plan_drilldown('time','year','2026','total_sales')
        self.assertIn('payment_date',plan['sql']);self.assertNotIn('sale_date',plan['sql'])
    def test_hybrid_recall_cache_lifecycle_and_no_lexical_overlap(self):
        saved=self.save(rule(category='term',binding=None))
        embed=Mock();embed.embed_documents.return_value=[[1,0]];embed.embed_query.return_value=[1,0]
        with patch.object(retrieval,'_model',return_value=embed):
            candidates,status=asyncio.run(retrieval.retrieve('卖货扣掉退回的钱还剩多少',[saved],engine.matches))
            self.assertEqual(candidates[0]['recall']['channel'],'semantic');self.assertEqual(status['semantic'],'ready')
            asyncio.run(retrieval.retrieve('另一种说法',[saved],engine.matches));self.assertEqual(embed.embed_documents.call_count,1)
            changed=store.save_rule({**RuleInput.model_validate(rule(category='term',binding=None)).model_dump(),'content':'新定义'},ident=saved['id'],expected_revision=1)
            asyncio.run(retrieval.retrieve('另一种说法',[changed],engine.matches));self.assertEqual(embed.embed_documents.call_count,2)
            store.delete_rule(saved['id'],2)
            with storage._connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM rule_vectors').fetchone()[0],0)
    def test_semantic_candidate_can_be_irrelevant_and_is_not_adopted(self):
        saved=self.save(rule(category='term',binding=None))
        from unittest.mock import AsyncMock
        decision=Review(decisions=[ReviewItem(rule_id=saved['id'],action='not_applicable',reason='销售额与利润不同')])
        with patch.object(retrieval,'_search',return_value=[(.7,saved['id'])]),patch.object(engine,'review_candidates',AsyncMock(return_value=decision)):
            result=asyncio.run(engine.resolve('利润是多少'))
        self.assertFalse(result['applied']);self.assertFalse(result['conflicts']);self.assertIn('不适用',result['overridden'][0]['reason'])

    def test_more_than_twelve_unresolved_rules_stop_without_model_guessing(self):
        for i in range(13):self.save(rule(title=f'口径{i}',category='term',binding=None))
        with patch.object(retrieval,'_search',side_effect=RuntimeError('offline')),patch.object(engine,'review_candidates',side_effect=AssertionError('bounded')):
            result=asyncio.run(engine.resolve('完全不同的问题'))
        self.assertEqual(len(result['conflicts']),13);self.assertFalse(result['applied'])

    def test_alias_recall_and_semantic_failure_never_silently_omit_rules(self):
        saved=self.save(rule(category='term',binding=None))
        with patch.object(retrieval,'_search',side_effect=RuntimeError('offline')):
            candidates,status=asyncio.run(retrieval.retrieve('不含关键词',[saved],engine.matches))
            self.assertEqual(status['semantic'],'unavailable_review_all');self.assertEqual(len(candidates),1)
        with patch.object(retrieval,'_search',side_effect=AssertionError('alias should not embed')):
            candidates,_=asyncio.run(retrieval.retrieve('扣退货后的收入',[saved],engine.matches));self.assertEqual(candidates[0]['recall']['channel'],'keyword_or_alias')
    def test_preview_does_not_query_database_and_binding_draft_missing_is_not_saved(self):
        with patch('tools.semantic_layer.semantic_layer',self.model),patch('tools.mysql_tools.run_read_query',side_effect=AssertionError('no IO')):
            r=self.client.post('/api/long-term-rules/binding-preview',json={'binding':self.formula()})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['params'],['完成','华东',100]);self.assertEqual(store.list_rules(),[])
        with patch('agent.llm.get_llm',side_effect=RuntimeError('offline')):
            r=self.client.post('/api/long-term-rules/binding-draft',json={'content':'扣除退货和税费','metric_key':'total_sales'})
        self.assertEqual(r.status_code,200);self.assertIsNone(r.json()['binding']);self.assertTrue(r.json()['missing'])
