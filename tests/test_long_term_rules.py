"""长期规则：使用独立 SQLite、模拟 LLM/SQL，不改写业务库。"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
os.environ['APP_DB_PATH']=str(Path(tempfile.mkdtemp(prefix='rule-tests-import-'))/'app.db')
from fastapi.testclient import TestClient
from api import storage,server,context,source_policy,evidence
from rules import store,engine
from rules.engine import Review,ReviewItem
from rules.semantic import validate_binding
from rules.models import RuleInput
from tools.semantic_layer import SemanticLayer
from tools.semantic_query import SemanticQueryEngine,run_semantic_query
from tools import mysql_tools,reflect_check
from utils import path_utils
from agent import main_agent


def preference(**changes):
    return {'title':'答复结构','category':'preference','subject':'答复结构','content':'回答库存问题时先给结论，再列依据。',
            'match_terms':['库存'],'scope_note':'库存问答','binding':None,'active':True,**changes}


def business(**changes):
    return preference(title='华东销售额',category='business',subject='销售额',content='销售额按数量汇总且默认限定华东。',match_terms=['销售额'],
        binding={'metric_key':'total_sales','aggregation':'sum','field':'sales_records.quantity_sold',
                 'filters':[{'field':'sales_records.region','op':'eq','value':'华东'}]},**changes)


class LongTermRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch('rules.retrieval._search',return_value=[]),patch.object(storage,'_DB_PATH',self.root/'app.db'),
                      patch.object(path_utils,'OUTPUT_DIR',self.root/'output'),patch.object(path_utils,'UPLOAD_DIR',self.root/'upload')]
        for p in self.patches:p.start()
        storage.init_db();self.client=TestClient(server.app)
        self.layer=SemanticLayer(Path('tests/fixtures/legacy_semantic.yaml'));self.layer._introspect_enabled=False
        self.model_patch=patch('tools.semantic_layer.semantic_layer', self.layer)
        self.model_patch.start();self.patches.append(self.model_patch)
        engine.reset()
    def tearDown(self):
        engine.reset();context.reset_session()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def create(self,data=None):
        response=self.client.post('/api/long-term-rules',json={**(data or preference()),'confirmed':True})
        self.assertEqual(response.status_code,201,response.text);return response.json()
    def review_use(self,candidates):
        return Review(decisions=[ReviewItem(rule_id=r['id'],action='use',reason='当前任务相关且无冲突') for r in candidates])
    def resolve(self,q,skips=()):
        async def review(query,candidates):return self.review_use(candidates)
        with patch.object(engine,'review_candidates',side_effect=review):return asyncio.run(engine.resolve(q,skips))

    def test_term_rule_cannot_be_blocked_by_invalid_partial_override_vote(self):
        rule=self.create(preference(title='交局简称',category='term',subject='专利交局',
            content='交局＝向专利局提交，不表示授权。',match_terms=['交局'],scope_note='专利业务'))
        async def wrong_vote(query,candidates):
            return Review(decisions=[ReviewItem(rule_id=rule['id'],action='partial_override',
                reason='误判为局部覆盖',current_quote='')])
        with patch.object(engine,'review_candidates',side_effect=wrong_vote):
            resolved=asyncio.run(engine.resolve('统计9月实际交局数量'))
        self.assertEqual(resolved['conflicts'],[])
        self.assertEqual([r['id'] for r in resolved['applied']],[rule['id']])

    def test_confirmation_is_required_and_correction_does_not_create_rule(self):
        r=self.client.post('/api/long-term-rules',json=preference())
        self.assertEqual(r.status_code,400)
        storage.create_run('source','库存是多少','库存是多少',False)
        storage.save_intent_revision('source','本次先给结论',None)
        self.assertEqual(store.list_rules(),[])

    def test_draft_fallback_is_editable_and_does_not_write(self):
        storage.create_run('source','库存是多少','库存是多少',False)
        revision=storage.save_intent_revision('source','库存回答先给结论',None)
        with patch('agent.llm.get_llm',side_effect=RuntimeError('offline')):
            response=self.client.post('/api/long-term-rules/draft',json={'thread_id':'source','revision_id':revision['id']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['status'],'manual_required')
        self.assertEqual(response.json()['source_snapshot'],'库存回答先给结论')
        self.assertEqual(store.list_rules(),[])

    def test_versions_conflict_disable_delete_and_source_survival(self):
        storage.create_run('source','库存是多少','库存是多少',False)
        revision=storage.save_intent_revision('source','库存回答先给结论',None)
        rule=self.create({**preference(),'source_thread_id':'source','source_revision_id':revision['id']})
        storage.delete_run('source')
        self.assertEqual(store.get_rule(rule['id'])['source_snapshot'],'库存回答先给结论')
        self.assertEqual(store.get_rule(rule['id'])['source_query'],'库存是多少')
        update={**preference(),'active':False,'confirmed':True,'expected_revision':1}
        response=self.client.put('/api/long-term-rules/'+rule['id'],json=update)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['revision'],2)
        self.assertEqual(self.client.put('/api/long-term-rules/'+rule['id'],json=update).status_code,409)
        self.assertEqual(len(store.versions(rule['id'])),2)
        self.assertEqual(self.resolve('库存')['applied'],[])
        self.assertEqual(self.client.delete('/api/long-term-rules/'+rule['id']+'?expected_revision=1').status_code,409)
        self.assertEqual(self.client.delete('/api/long-term-rules/'+rule['id']+'?expected_revision=2').status_code,200)
        self.assertEqual(store.versions(rule['id']),[])

    def test_stale_business_binding_can_be_disabled_and_is_not_silently_applied(self):
        rule=self.create(business())
        with patch('rules.semantic.validate_binding',side_effect=ValueError('字段已移除')):
            result=self.resolve('销售额')
        self.assertFalse(result['applied']);self.assertIn('定义失效',result['conflicts'][0]['reason'])
        with patch('api.long_term_rules.validate_binding',side_effect=AssertionError('disabled rule need not compile')):
            response=self.client.put('/api/long-term-rules/'+rule['id'],json={**business(),'active':False,'confirmed':True,'expected_revision':1})
        self.assertEqual(response.status_code,200)

    def test_relevance_is_bounded_and_latin_keywords_use_boundaries(self):
        self.create();self.create(preference(title='SQL格式',subject='SQL格式',match_terms=['SQL']))
        self.assertEqual(len(self.resolve('库存')['applied']),1)
        self.assertEqual(len(self.resolve('查询SQL结果')['applied']),1)
        self.assertEqual(len(self.resolve('SQL query')['applied']),1)
        self.assertEqual(len(self.resolve('MySQL query')['applied']),0)

    def test_project_wide_preferences_are_explicit_and_business_rules_remain_scoped(self):
        rule=self.create(preference(match_mode='all_tasks',match_terms=[],scope_note='本项目所有任务'))
        resolved=self.resolve('一个不含库存关键词的新问题')
        self.assertEqual(resolved['applied'][0]['id'],rule['id'])
        response=self.client.post('/api/long-term-rules',json={**business(),'match_mode':'all_tasks','match_terms':[],'confirmed':True})
        self.assertEqual(response.status_code,422)

    def test_current_task_overrides_and_explicit_skip_is_deterministic(self):
        rule=self.create()
        review=Review(decisions=[ReviewItem(rule_id=rule['id'],action='current_overrides',reason='用户明确要求先列依据',current_quote='这次先列依据')])
        with patch.object(engine,'review_candidates',AsyncMock(return_value=review)):
            result=asyncio.run(engine.resolve('库存问题这次先列依据'))
        self.assertFalse(result['applied']);self.assertEqual(result['overridden'][0]['reason'],'用户明确要求先列依据')
        with patch.object(engine,'review_candidates',side_effect=AssertionError('must not review')):
            result=asyncio.run(engine.resolve('库存问题，本次不使用长期规则'))
        self.assertFalse(result['applied']);self.assertEqual(len(result['overridden']),1)

    def test_conflicting_subjects_require_ack_and_review_failure_is_closed(self):
        a=self.create();b=self.create(preference(content='先列依据，最后结论。'))
        result=self.resolve('库存')
        self.assertEqual(len(result['conflicts']),2);self.assertFalse(result['applied'])
        stale=self.resolve('库存',[{'id':a['id'],'revision':99}])
        self.assertEqual(len(stale['conflicts']),2)
        result=self.resolve('库存',[{'id':a['id'],'revision':a['revision']}])
        self.assertEqual(result['applied'][0]['id'],b['id'])
        with patch.object(engine,'review_candidates',AsyncMock(side_effect=RuntimeError('offline'))):
            failed=asyncio.run(engine.resolve('库存'))
        self.assertEqual(len(failed['conflicts']),2)
        skipped=self.resolve('库存',[{'id':a['id'],'revision':1},{'id':b['id'],'revision':1}])
        self.assertFalse(skipped['conflicts']);self.assertFalse(skipped['applied'])

    def test_unavailable_review_skips_single_non_business_default(self):
        rule=self.create(preference())
        with patch.object(engine,'review_candidates',AsyncMock(side_effect=RuntimeError('offline'))):
            result=asyncio.run(engine.resolve('库存是多少'))
        self.assertFalse(result['conflicts'])
        self.assertFalse(result['applied'])
        self.assertEqual(result['overridden'][0]['id'],rule['id'])
        self.assertIn('未应用',result['overridden'][0]['reason'])

    def test_unavailable_review_keeps_executable_business_rule_blocked(self):
        self.create(business())
        with patch.object(engine,'review_candidates',AsyncMock(side_effect=RuntimeError('offline'))):
            result=asyncio.run(engine.resolve('销售额是多少'))
        self.assertEqual(len(result['conflicts']),1)

    def test_model_cannot_silently_choose_between_conflicting_defaults(self):
        a=self.create();b=self.create(preference(content='先列依据，再给结论。'))
        verdict=Review(decisions=[ReviewItem(rule_id=a['id'],action='use',reason='选此条'),
                                 ReviewItem(rule_id=b['id'],action='current_overrides',reason='另一条默认规则优先',current_quote='按默认方式回答')])
        with patch.object(engine,'review_candidates',AsyncMock(return_value=verdict)):
            result=asyncio.run(engine.resolve('库存是多少，按默认方式回答'))
        self.assertFalse(result['applied']);self.assertFalse(result['overridden']);self.assertEqual(len(result['conflicts']),2)

    def test_bad_bindings_and_permission_fields_are_rejected(self):
        for binding in [None,{'metric_key':'unknown'}, {'metric_key':'total_sales','aggregation':'sum','field':'sales_records.hacked;DROP'}]:
            response=self.client.post('/api/long-term-rules',json={**business(),'binding':binding,'confirmed':True})
            self.assertEqual(response.status_code,422,response.text)
        response=self.client.post('/api/long-term-rules',json={**preference(),'allowed_sources':['web_search'],'confirmed':True})
        self.assertEqual(response.status_code,422)
        invalid=business();invalid['binding']['filters']=[{'field':'sales_records.quantity_sold','op':'eq','value':'不是数字'}]
        self.assertEqual(self.client.post('/api/long-term-rules',json={**invalid,'confirmed':True}).status_code,422)
        self.assertEqual(store.list_rules(),[])

    def test_business_definition_changes_sql_and_explicit_filters_win(self):
        rule=self.create(business());engine.configure({'applied':[rule]})
        query=SemanticQueryEngine(self.layer)
        plan=query.plan_query('total_sales',['region'])
        self.assertIn('SUM(t0.quantity_sold)',plan['sql']);self.assertIn('华东',plan['params'])
        engine.configure({'applied':[{**rule,'subject':'SalesAlias'}]})
        self.assertEqual(query.plan_query('salesalias')['metric']['key'],'total_sales')
        engine.configure({'applied':[rule]})
        with self.assertRaises(PermissionError):query.plan_query('销售额',[],{'region':'华南'})
        multi=query.plan_multi(['total_sales','sales_quantity'],['region'])
        self.assertEqual(multi['sql'].count('华东'),0) # Values are parameterized.
        self.assertEqual(multi['params'].count('华东'),1) # Default not applied to unrelated metric.
        with self.assertRaises(PermissionError):query.plan_drilldown('geo','region','华南','total_sales')
        engine.configure({'applied':[],'overridden':[rule]})
        overridden=query.plan_query('total_sales',[],{'region':'华南'})
        self.assertIn('华南',overridden['params']);self.assertNotIn('华东',overridden['params'])
        self.assertEqual(plan['metric']['rule_versions'],[{'id':rule['id'],'revision':1}])

    def test_business_constraints_block_raw_io_and_semantic_records_actual_versions(self):
        rule=self.create(business());engine.configure({'applied':[rule]})
        context.set_session('rule-query',str(self.root/'output'));source_policy.configure(['db_query'])
        with patch.object(mysql_tools,'_get_connection',side_effect=AssertionError('must not connect')):
            self.assertIn('禁止执行',mysql_tools.execute_sql_query('SELECT 1'))
            self.assertIn('禁止执行',mysql_tools.get_table_data('sales_records'))
            with self.assertRaises(PermissionError):mysql_tools.run_read_query('SELECT 1')
        fake={'columns':['total_sales'],'rows':[[8]],'rowCount':1}
        with patch('tools.semantic_query.engine',SemanticQueryEngine(self.layer)),patch('tools.semantic_query.run_read_query',return_value=fake):
            result=run_semantic_query('total_sales')
        recorded=evidence.snapshot('rule-query')
        self.assertIn('8',result)
        self.assertEqual(recorded[0]['rule_versions'],[{'id':rule['id'],'revision':1}])
        self.assertTrue(reflect_check.hard_check({'report':'8','require_report':False,'required_rule_versions':[{'id':rule['id'],'revision':1}]}))
        self.assertFalse(reflect_check.hard_check({'report':'8','require_report':False,'required_rule_versions':[{'id':rule['id'],'revision':1}],'executed_rule_versions':recorded[0]['rule_versions']}))

    def test_conflict_does_not_replace_previous_run_and_skip_can_start(self):
        rule=self.create();storage.create_run('repeat','库存','库存',False)
        storage.append_event('repeat',{'event':'task_result','data':{'result':'旧结果'}})
        conflict={'applied':[],'overridden':[],'conflicts':[{**rule,'reason':'冲突'}],'candidate_count':1}
        with patch.object(engine,'resolve',AsyncMock(return_value=conflict)),patch.object(server,'_run_task',AsyncMock()) as task:
            response=self.client.post('/api/task',json={'thread_id':'repeat','query':'库存'})
        self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail']['code'],'rule_conflict')
        self.assertEqual(storage.get_run('repeat')['result'],'旧结果');task.assert_not_called();self.assertNotIn('repeat',server._starting)
        allowed={'applied':[],'overridden':[{**rule,'reason':'用户跳过'}],'conflicts':[],'candidate_count':1}
        with patch.object(engine,'resolve',AsyncMock(return_value=allowed)),patch.object(server,'_run_task',AsyncMock()):
            response=self.client.post('/api/task',json={'thread_id':'repeat','query':'库存','skip_rule_versions':[{'id':rule['id'],'revision':1}]})
        self.assertEqual(response.status_code,200);self.assertEqual(storage.get_run('repeat')['rule_context']['overridden'][0]['id'],rule['id'])

    def test_task_snapshot_survives_rule_edits_and_deletion(self):
        rule=self.create();snapshot=self.resolve('库存')
        storage.create_run('frozen','库存','库存',False,rule_context=snapshot)
        self.client.put('/api/long-term-rules/'+rule['id'],json={**preference(),'content':'新版本','expected_revision':1,'confirmed':True})
        store.delete_rule(rule['id'],2)
        restored=storage.get_run('frozen')['rule_context']['applied'][0]
        self.assertEqual(restored['revision'],1);self.assertEqual(restored['content'],rule['content'])

    def test_review_receives_only_adopted_defaults_not_overridden_content(self):
        from langchain_core.messages import AIMessage
        fake=AsyncMock();fake.ainvoke.return_value=AIMessage(content='通过')
        fake.with_structured_output=Mock(return_value=fake)
        result={'query':'本次只用一句话回答','report':'可用库存为13盒。',
                'rule_context':{'applied':[],'overridden':[{'content':'必须另起一行列出计算过程'}]}}
        with patch.object(main_agent,'get_llm',return_value=fake):
            self.assertIsNone(asyncio.run(main_agent.soft_review(result)))
        prompt=fake.ainvoke.await_args.args[0][-1].content
        self.assertNotIn('必须另起一行列出计算过程',prompt)
        self.assertIn('不得另加计算式要求',prompt)

    def test_rule_refinement_cannot_expand_sources(self):
        rule=self.create();engine.configure({'applied':[rule]})
        baseline=main_agent.TaskPlan(task_type='qa',require_report=False,require_pdf=False,required_sources=[],forbidden_sources=['web_search'])
        refined=baseline.model_copy(update={'required_sources':['web_search','db_query'],'forbidden_sources':[]})
        result={'report':'库存为5。','called_sources':[],'pdf_produced':False,'evidence':[]}
        with patch.object(main_agent,'build_task_plan',AsyncMock(side_effect=[baseline,refined])),patch.object(main_agent,'main_agent_run',AsyncMock(return_value=result)) as execute,patch.object(main_agent,'soft_review',AsyncMock(return_value=None)),patch.object(main_agent.monitor,'report',AsyncMock()):
            asyncio.run(main_agent._run_session('库存是多少，不联网','cap',self.root/'output',1))
        self.assertEqual(execute.await_args.args[2],[])
        self.assertIsNone(source_policy.current_policy()) # asyncio task-local authority does not leak to caller.
