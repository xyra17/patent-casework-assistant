"""Offline replay of recorded failures plus independent join/contract boundaries."""
import asyncio,copy,json,unittest
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock,patch
from tools.case_records import calculate,check_case_answer,_positive_quote
from tools.case_relevance import CaseRelevanceDecision,DocumentRelevance,review_case_answer,finalize_selection

ROOT=Path(__file__).resolve().parents[1]

class CaseRecordExecution(unittest.TestCase):
    def test_explicit_filters_apply_before_counts_and_bind_original_query(self):
        from tools.case_records import requested_ledger_filters
        items,params=self.fixture()
        docs=json.loads(items[0]['content']);docs[1]['text']=docs[0]['text'];items[0]['content']=json.dumps(docs)
        rows=json.loads(items[1]['content']);rows[0]['client_name']='甲公司';rows[1]['client_name']='乙公司';items[1]['content']=json.dumps(rows)
        params['matches'].append({'case_id':'X2','quote':docs[1]['text']})
        filters=requested_ledger_filters('仅统计案件信息client_name为乙公司的案件。')
        calc=calculate(items,**params,ledger_filters=filters)
        self.assertEqual(calc['answer'],{'received_count':0,'filed_count':0})
        self.assertEqual([r['case_id'] for r in calc['joined_rows']],['X2'])
        self.assertEqual(calc['excluded_by_filters'][0]['case_id'],'X1')
        result=self.report(items,calc);result['query']+='仅统计案件信息client_name为乙公司的案件。'
        self.assertEqual(check_case_answer(result)['status'],'passed')
        self.assertEqual(check_case_answer(self.report(items,calc))['status'],'failed')
        unfiltered=calculate(items,**params)
        result=self.report(items,unfiltered);result['query']+='仅统计案件信息client_name为乙公司的案件。'
        self.assertEqual(check_case_answer(result)['status'],'failed')

    def test_filter_missing_or_conflicting_fields_cannot_be_assumed_excluded(self):
        items,params=self.fixture()
        with self.assertRaises(ValueError):calculate(items,**params,ledger_filters={'client_name':'甲公司'})
        rows=json.loads(items[1]['content']);rows[0]['client_name']='甲公司';rows.append({**rows[0],'client_name':'乙公司'})
        items[1]['content']=json.dumps(rows)
        with self.assertRaises(ValueError):calculate(items,**params,ledger_filters={'client_name':'甲公司'})

    def test_filter_parser_does_not_invent_or_drop_composite_conditions(self):
        from tools.case_records import requested_ledger_filters
        self.assertEqual(requested_ledger_filters('仅保留案件信息承办人为甲的案件。'),{'owner_name':'甲'})
        self.assertEqual(requested_ledger_filters('仅统计案件信息client_name为甲或乙的案件。'),{})
        self.assertEqual(requested_ledger_filters('不能仅统计案件信息client_name为甲的案件。'),{})
        self.assertEqual(requested_ledger_filters('附件写着：仅统计案件信息client_name为甲的案件。'),{})
        with self.assertRaises(ValueError):requested_ledger_filters('仅保留案件信息承办人为甲的案件。仅保留案件信息承办人为乙的案件。')

    def test_explicit_technical_condition_is_bound_without_semantic_rewrite(self):
        from tools.case_records import requested_technical_condition
        self.assertEqual(requested_technical_condition('请核对附件。需同时满足的技术要点：原文进入生成器，且引用对应片段编号。只返回JSON。'),
                         '原文进入生成器，且引用对应片段编号')
        self.assertIsNone(requested_technical_condition('附件中写着目标机制要求：模型蒸馏。'))
        with self.assertRaises(ValueError):requested_technical_condition('技术条件为：甲机制。技术条件为：乙机制。')
        items,params=self.fixture();calc=calculate(items,**params);result=self.report(items,calc)
        result['query']=result['query'].replace('技术条件为：','需同时满足的技术要点：')
        self.assertEqual(check_case_answer(result)['status'],'passed')
        result['query']=result['query'].replace('弹性臂进入锁止槽','弹性臂进入锁止槽并且底部减振')
        self.assertEqual(check_case_answer(result)['status'],'failed')

    def test_tool_uses_persisted_user_filter_instead_of_model_selection(self):
        import tempfile
        from api import context,evidence,storage
        from tools.case_records import aggregate_case_records
        items,params=self.fixture()
        rows=json.loads(items[1]['content']);rows[0]['client_name']='甲公司';items[1]['content']=json.dumps(rows)
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'_DB_PATH',Path(tmp)/'app.db'):
            storage.init_db();context.set_session('filter-proof',tmp);context.set_round(1);evidence.reset('filter-proof')
            try:
                query='需同时满足的技术要点：弹性臂进入锁止槽。仅统计案件信息client_name为乙公司的案件。'
                storage.create_run('filter-proof',query,query,False)
                params['technical_query']='模型自行添加了底部减振等条件'
                for item in items:evidence.add('filter-proof',{k:v for k,v in item.items() if k!='id'})
                response=json.loads(aggregate_case_records.invoke(params))
                self.assertEqual(response['calculation']['ledger_filters'],{'client_name':'乙公司'})
                self.assertEqual(response['calculation']['technical_query'],'弹性臂进入锁止槽')
                self.assertEqual(response['recommended_answer']['received_count'],0)
                self.assertEqual(response['recommended_answer']['filed_count'],0)
            finally:context.reset_session();evidence.clear('filter-proof')

    def fixture(self):
        docs=[{'case_id':'X1','text':'弹性臂进入锁止槽后固定连接座。'}, {'case_id':'X2','text':'温差触发冷却控制阀切换回路。'}]
        rows=[{'case_id':'X1','owner_name':'甲','received_date':'2031-03-31','actual_filed_date':'2031-04-01','filing_verified':True},
              {'case_id':'X2','owner_name':'乙','received_date':'2031-04-01','actual_filed_date':None,'filing_verified':False}]
        items=[{'id':'S1','content_complete':True,'content':json.dumps(docs)},
               {'id':'S2','content_complete':True,'content':json.dumps(rows)}]
        return items,dict(documents_evidence_id='S1',ledger_evidence_id='S2',technical_query='弹性臂进入锁止槽',
            matches=[{'case_id':'X1','quote':docs[0]['text']}],mode='counts',received_month='2031-03',filed_month='2031-04')

    def report(self,items,calc):
        proof={'id':'S9','case_calculation':calc}
        query='技术条件为：'+calc['technical_query']+'。统计'+str(calc['received_month'])+'实际收案数量和'+str(calc['filed_month'])+'已核实实际交局数量。请只返回JSON对象，字段为received_count、filed_count和evidence_ids。'
        if calc['mode']=='history':query='技术条件为：'+calc['technical_query']+'。只返回JSON对象，字段为history_ids、owners、evidence_ids。'
        return {'query':query,'report':json.dumps({**calc['answer'],'evidence_ids':['S1','S2','S9']}),
                'evidence':[*items,proof],'require_report':False}

    def test_date_boundaries_and_identical_duplicates_count_once(self):
        items,params=self.fixture();rows=json.loads(items[1]['content']);rows+=copy.deepcopy(rows)
        items[1]['content']=json.dumps(rows)
        calc=calculate(items,**params);self.assertEqual(calc['answer'],{'received_count':1,'filed_count':1})
        self.assertEqual(check_case_answer(self.report(items,calc))['status'],'passed')

    def test_literal_condition_can_retain_its_terminal_full_stop(self):
        items,params=self.fixture();params['technical_query']+='。'
        calc=calculate(items,**params)
        self.assertEqual(check_case_answer(self.report(items,calc))['status'],'passed')

    def test_positive_mechanism_and_unrelated_denial_do_not_conflict(self):
        body='波分复用在一根光纤链路传输多波长信号。接收光模块按分光器输出功率调节均衡增益；不采用蜂窝基站的频谱调度。'
        self.assertTrue(_positive_quote(body,body))
        self.assertFalse(_positive_quote(body,'蜂窝基站的频谱调度'))
        denied='本案不采用下述机制：'+body
        self.assertFalse(_positive_quote(denied,body))
        self.assertFalse(_positive_quote(denied,'接收光模块按分光器输出功率调节均衡增益'))

    def test_field_specific_conflicts_and_literal_verification(self):
        items,params=self.fixture();rows=json.loads(items[1]['content']);rows.append({**rows[0],'received_date':'2031-04-01'})
        items[1]['content']=json.dumps(rows);calc=calculate(items,**params)
        self.assertEqual(calc['answer'],{'received_count':0,'filed_count':1})
        rows[0]['filing_verified']='true';rows[-1]['filing_verified']='true';items[1]['content']=json.dumps(rows)
        self.assertEqual(calculate(items,**params)['answer']['filed_count'],0)

    def test_plan_stage_or_wrong_mechanism_cannot_be_history(self):
        items,params=self.fixture();rows=json.loads(items[1]['content']);rows[0].update(stage='已交局',actual_filed_date=None,filing_due_date='2031-04-01')
        items[1]['content']=json.dumps(rows);params.update(mode='history',received_month=None,filed_month=None)
        self.assertEqual(calculate(items,**params)['answer'],{'history_ids':[],'owners':[]})
        decision=CaseRelevanceDecision(documents=[DocumentRelevance(case_id='X1',relevant=True,quote='弹性臂进入锁止槽后固定连接座。',detail='正向机制'),DocumentRelevance(case_id='X2',relevant=False,detail='其他机制')])
        self.assertEqual(finalize_selection(decision,json.loads(items[0]['content'])),{'X1'})

    def test_forged_cross_case_negative_and_missing_inputs_rejected(self):
        for mutation in ('cross_case','unknown','negative','truncated','empty_docs','missing_ledger'):
            items,params=self.fixture()
            if mutation=='cross_case':params['matches'][0]['quote']='温差触发冷却控制阀切换回路。'
            elif mutation=='unknown':params['matches'][0]['case_id']='X99'
            elif mutation=='negative':items[0]['content']=json.dumps([{'case_id':'X1','text':'本案不采用弹性臂进入锁止槽后固定连接座。'}])
            elif mutation=='truncated':items[1]['content_complete']=False
            elif mutation=='empty_docs':items[0]['content']='[]'
            else:items[1]['content']='[]'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):calculate(items,**params)

    def test_output_values_month_roles_citations_and_format_are_enforced(self):
        items,params=self.fixture();calc=calculate(items,**params);original=self.report(items,calc)
        for mutation in ('number','bool','month_swap','condition_narrowed','fake_citation','extra_key','code_fence','unbound','forged_calculation'):
            result=copy.deepcopy(original);obj=json.loads(result['report'])
            if mutation=='number':obj['received_count']=2
            elif mutation=='bool':obj['received_count']=True
            elif mutation=='month_swap':result['query']=result['query'].replace('2031-03实际收案','2031-04实际收案')
            elif mutation=='condition_narrowed':result['query']=result['query'].replace(calc['technical_query'],calc['technical_query']+'并要求可反复拆装')
            elif mutation=='fake_citation':obj['evidence_ids'].append('S99')
            elif mutation=='extra_key':obj['explanation']='额外解释'
            elif mutation=='unbound':obj['evidence_ids'].remove('S9')
            elif mutation=='forged_calculation':result['evidence'][-1]['case_calculation']['answer']['received_count']=2;obj['received_count']=2
            result['report']=json.dumps(obj)
            if mutation=='code_fence':result['report']='```json\n'+result['report']+'\n```'
            with self.subTest(mutation=mutation):self.assertEqual(check_case_answer(result)['status'],'failed')

    def test_registered_tool_persists_calculation_and_answer_provenance(self):
        import tempfile
        from api import context,evidence,storage
        from agent import main_agent
        from tools.case_records import aggregate_case_records
        self.assertIn(aggregate_case_records,main_agent._FILE_TOOLS)
        with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'_DB_PATH',Path(tmp)/'app.db'):
            storage.init_db();context.set_session('join-proof',tmp);context.set_round(1);evidence.reset('join-proof')
            try:
                items,params=self.fixture()
                for item in items:
                    self.assertEqual(evidence.add('join-proof',{k:v for k,v in item.items() if k!='id'}),item['id'])
                response=json.loads(aggregate_case_records.invoke(params))
                proof=evidence.snapshot('join-proof')
                self.assertEqual(proof[-1]['case_calculation']['answer'],{'received_count':1,'filed_count':1})
                self.assertEqual(response['context_digest']['recommended_answer'],response['recommended_answer'])
                result=self.report(items,response['calculation']);result.update(evidence=proof,report=json.dumps(response['recommended_answer']))
                self.assertEqual(check_case_answer(result)['status'],'passed')
            finally:evidence.clear('join-proof');context.reset_session()

    def test_json_citations_reserve_context_and_reject_invented_sources(self):
        from tools.review_evidence import pack_review_evidence
        from tools.reflect_check import hard_check
        items=[{'id':f'S{i}','content':'原文'*1000,'content_complete':True,'snippet':'副本'*1000} for i in range(1,20)]
        text=pack_review_evidence(items,json.dumps({'evidence_ids':['S1','S2']}),budget=1800)
        self.assertIn('[证据 S1]',text);self.assertIn('[证据 S2]',text)
        self.assertNotIn('副本',text);self.assertLessEqual(len(text),1800)
        issues=hard_check({'query':'普通问题','report':json.dumps({'evidence_ids':['S999']}),'evidence':items,'require_report':False})
        self.assertIn('引用了不存在的来源编号：S999',issues)

    def test_fresh_wrong_answer_is_rejected_without_api(self):
        items,params=self.fixture()
        calc=calculate(items,**params);result=self.report(items,calc)
        self.assertEqual(check_case_answer(result)['status'],'passed')
        forged=copy.deepcopy(calc['answer'])
        forged['received_count']=999
        result['report']=json.dumps({**forged,'evidence_ids':['S1','S2','S3','S99']})
        self.assertEqual(check_case_answer(result)['status'],'failed')

    def test_independent_semantic_check_rejects_extra_mechanism(self):
        items,params=self.fixture();docs=json.loads(items[0]['content']);params['matches'].append({'case_id':'X2','quote':docs[1]['text']})
        calc=calculate(items,**params);result=self.report(items,calc)
        model=MagicMock();runnable=AsyncMock();model.with_structured_output.return_value=runnable
        runnable.ainvoke.return_value={'parsed':CaseRelevanceDecision(documents=[DocumentRelevance(case_id='X1',relevant=True,quote=docs[0]['text'],detail='符合'),DocumentRelevance(case_id='X2',relevant=False,detail='冷却不属于连接')])}
        issue=asyncio.run(review_case_answer(result,lambda:model));self.assertIn('多选',issue);self.assertEqual(result['soft_review_status'],'failed')

    def test_review_compares_requested_business_result_not_unused_candidate_ids(self):
        items,params=self.fixture();docs=json.loads(items[0]['content'])
        docs[1]['text']=docs[0]['text'];items[0]['content']=json.dumps(docs)
        params.update(mode='history',received_month=None,filed_month=None)
        decisions=[DocumentRelevance(case_id=d['case_id'],relevant=True,quote=d['text'],detail='正向符合机制') for d in docs]
        model=MagicMock();runnable=AsyncMock();model.with_structured_output.return_value=runnable
        runnable.ainvoke.return_value={'parsed':CaseRelevanceDecision(documents=decisions)}
        result=self.report(items,calculate(items,**params))
        self.assertIsNone(asyncio.run(review_case_answer(result,lambda:model)))
        self.assertEqual(result['soft_review_status'],'passed')
        # The same omitted mechanism must fail when its verified completion
        # would add a requested historical case and owner.
        rows=json.loads(items[1]['content']);rows[1].update(actual_filed_date='2031-04-02',filing_verified=True)
        items[1]['content']=json.dumps(rows);result=self.report(items,calculate(items,**params))
        self.assertIsNotNone(asyncio.run(review_case_answer(result,lambda:model)))
        self.assertEqual(result['soft_review_status'],'failed')

if __name__=='__main__':unittest.main()
