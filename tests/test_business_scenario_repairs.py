"""Regression gates from real scenario failures, with independent changed inputs."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from casework import analytics
from tools.answer_contracts import quote_options,render_answer,contract_issues,workload_requested
from tools.attachment_tables import query_table
from tools.chart_utils import chart_delivery_issues
from tools.statistics_check import check_statistics
from tools.placeholders import has_placeholders

class BusinessScenarioRepairs(TestCase):
    def test_case_table_cannot_cite_upload_or_another_case_as_history(self):
        from tools.content_verification import archive_absence_issues
        evidence=[{'id':'S1','type':'file','snippet':'新交底机制'},
                  {'id':'S2','type':'knowledge','casework':True,'case_id':'AI-2026-004'},
                  {'id':'S3','type':'database','columns':['所编号','代理师'],'rows':[['AI-2026-005','乙']]}]
        bad='| AI-2026-005 | 机制 [S1][S2] |'
        self.assertTrue(any(x.startswith('案件引用身份不匹配') for x in archive_absence_issues(bad,evidence)))
        self.assertFalse(archive_absence_issues('| AI-2026-004 | 原文 [S2] |',evidence))
        self.assertFalse(archive_absence_issues('| AI-2026-005 | 乙 [S3] |',evidence))
        untyped=[{k:v for k,v in e.items() if k!='columns'} for e in evidence]
        self.assertTrue(archive_absence_issues('| AI-2026-005 | 乙 [S3] |',untyped))

    def test_quote_selector_preserves_pdf_wrapped_mechanism(self):
        source={'id':'S1','type':'knowledge','snippet':'摘要\n输入为文档身份。处理链为确认范围；冻结可见性；删除向量与词法\n块；回查所有入口。'}
        options=quote_options(source)
        self.assertTrue(any('删除向量与词法\n块；回查所有入口。' in option['original'] for option in options))

    def test_three_deadlines_require_field_level_provenance(self):
        from tools.answer_contracts import case_date_provenance_issues
        query='AI-2026-032 的初稿、送客户审核、向局提交分别定在什么时候？把日期和出处给我，有冲突直接说。'
        self.assertTrue(case_date_provenance_issues(query,'有三个日期。',[]))
        proof={'id':'S3','title':'本案日期字段原件出处与冲突状态',
               'columns':['所编号','字段','原件值','核对状态','原件路径','原件位置'],
               'rows':[['AI-2026-032',name,value,'verified_source','AI-2026-032/案件信息.xlsx',f'第{index}行第2列']
                       for index,(name,value) in enumerate([
                           ('初稿期限／所限','2026-02-23'),('客户送审期限','2026-03-16'),
                           ('向局提交期限','2026-03-23')],8)]}
        report,_=render_answer('三项均为计划日期。[S1]',[proof],query)
        self.assertIn('### 三项计划期限的原件出处与冲突核对',report)
        self.assertIn('未发现这三项字段的候选值冲突。[S3]',report)
        self.assertFalse(case_date_provenance_issues(query,report,[proof]))

    def test_uploaded_disclosure_and_literal_quotes_get_traceable_sources(self):
        from tools.evidence_refs import normalize_citations,citation_issues
        evidence=[{'id':'S1','type':'file','title':'交底.md','snippet':'# 材料\n## 技术方案\n使用分层索引。\n## 边界\n待核对。'},
                  {'id':'S2','type':'knowledge','title':'历史案','location':'第 2 页 · 字符20–80',
                   'snippet':'采用候选扩展并核对距离，然后给出检索结果。'}]
        report='## 交底的核心技术\n采用分层索引。\n\n历史原文：\n> “采用候选扩展并核对距离，然后给出检索结果。”'
        self.assertTrue(citation_issues(report,evidence))
        fixed=normalize_citations(report,evidence)
        self.assertIn('“技术方案”第2–3行[S1]',fixed)
        self.assertIn('[S2]（第 2 页 · 字符20–80）',fixed)
        self.assertFalse(citation_issues(fixed,evidence))

    def test_assignment_does_not_infer_immediate_availability_or_unique_zero_load(self):
        from tools.content_verification import unsupported_reasoning_issues
        def workload(ident,owner):
            return {'id':ident,'type':'database','columns':['owner_name','active_count'],
                    'rows':[[owner,0]],'answer_contract':{'kind':'workload'},
                    'statistics':{'dimensions':[{'column':'owner_name','index':0}],
                                  'metrics':[{'column':'active_count','index':1}]}}
        evidence=[workload('S1','甲'),workload('S2','乙')]
        query='新案交给谁？给几个分案候选。'
        unsafe='甲在办0件，是唯一无在手登记压力的人，能立即投入。'
        issues=unsupported_reasoning_issues(unsafe,evidence,query)
        self.assertEqual(len(issues),2)
        safe='甲、乙当前登记在办均为0件；实际可用时间尚未核实。'
        self.assertFalse(unsupported_reasoning_issues(safe,evidence,query))

    def test_source_paragraph_range_binds_each_endpoint_to_same_file(self):
        from tools.evidence_refs import normalize_citations,citation_issues
        sources=[{'id':'S3','type':'knowledge','title':'archive/case.pdf','file_id':'f1','revision':'r1',
                  'snippet':'说明书 [0001] 输入合同。'},
                 {'id':'S4','type':'knowledge','title':'archive/case.pdf','file_id':'f1','revision':'r1',
                  'snippet':'[0010] 终态检查。'}]
        report='原件 `archive/case.pdf`（说明书[0001]–[0010]）[S3][S4]'
        fixed=normalize_citations(report,sources)
        self.assertIn('[0001][S3]–[0010][S4]',fixed)
        self.assertFalse(citation_issues(fixed,sources))
        ambiguous=[*sources,{'id':'S5','type':'knowledge','title':'archive/case.pdf','file_id':'f1',
                             'revision':'r1','snippet':'说明书[0001] 另一个片段。'}]
        self.assertEqual(normalize_citations(report,ambiguous),report)
        self.assertTrue(citation_issues('原件 `archive/case.pdf`（说明书[0001]–[0010]）',ambiguous))

    def test_patent_paragraphs_accept_only_preserved_explicit_sources(self):
        from tools.evidence_refs import normalize_citations,citation_issues
        sources=[{'id':'S18','type':'knowledge','title':'archive/case.pdf','snippet':'[0004] 第一步。'},
                 {'id':'S20','type':'knowledge','title':'archive/case.pdf','snippet':'[0009] 第六步。'}]
        report='《case.pdf》（说明书[0004]–[0009]）\n| 第一步 | 第3页 [0004]（S18） |\n| 第六步 | 第3页 [0009]（S20） |'
        normalized=normalize_citations(report,sources)
        self.assertIn('[0004][S18]',normalized)
        self.assertFalse(citation_issues(normalized,sources))
        self.assertTrue(citation_issues('说明书[0004]–[0009]',sources))
        self.assertTrue(citation_issues('第3页 [0004]（S99）',sources))

    def test_meeting_todo_does_not_erase_documented_counterexample(self):
        from tools.content_verification import unsupported_reasoning_issues
        sources=[{'id':'S26','type':'knowledge','casework':True,'case_id':'AI-2026-024',
                  'snippet':'本材料的关键反例为：权限拒绝被无限重试。'}]
        query='比较两案会议待补材料和关键反例'
        self.assertTrue(unsupported_reasoning_issues('两案反例尚未成文。',sources,query))
        self.assertFalse(unsupported_reasoning_issues('会议登记仍要求把反例写入边界说明；申请文件已写有关键反例。',sources,query))

    def test_monthly_table_uses_row_and_nearest_heading_time(self):
        month_source={'id':'S1','type':'database','columns':['received_count'],'rows':[[10]],
                      'statistics':{'metrics':[{'column':'received_count','index':0,'label':'实际收案数量',
                          'names':['实际收案数量'],'unit':'件','definition_verified':True,
                          'time':{'status':'bounded','start':'2026-10-01','end':'2026-11-01','field':'received_date'},
                          'filters':[]}],'dimensions':[]}}
        report='# 2026年10月月报（对比2026年9月）\n\n统计范围：部门已接入案件。9月作对照，8月及更早不纳入。\n\n## 2026年10月实际收案 [S1]\n\n| 实际收案数量（件） |\n| --- |\n| 10 |'
        check=asyncio.run(check_statistics(report,[month_source]))
        self.assertEqual(check['status'],'passed',check['issues'])
        wrong=report.replace('## 2026年10月实际收案','## 2026年9月实际收案')
        self.assertEqual(asyncio.run(check_statistics(wrong,[month_source]))['status'],'failed')

    def test_complete_due_rows_are_delivered_from_full_evidence(self):
        from tools.answer_contracts import render_answer
        def item(ident,status,rows,complete=True):
            return {'id':ident,'type':'database','snippet':json.dumps({'due_field':'draft_due_date','due_status':status}),
                    'columns':['所编号','代理师','初稿期限／所限'],'rows':rows,
                    'completeness':{'query_rows':len(rows),'has_more':not complete,'fetch_truncated':False}}
        overdue=item('S2','overdue',[['AI-2026-001','甲','2026-01-01'],['AI-2026-002','乙','2026-01-02']])
        coming=item('S3','next14',[['AI-2026-003','丙','2026-01-04']])
        query='帮我看在办的案子，初稿哪些逾期、哪些两周内要交？带上案号和承办人。'
        report,audit=render_answer('已逾期2件，两周内1件。',[overdue,coming],query)
        self.assertEqual([a['rows'] for a in audit if a['kind']=='due_full_list'],[2,1])
        self.assertIn('| AI-2026-002 | 乙 | 2026-01-02 | [S2] |',report)
        self.assertIn('| AI-2026-003 | 丙 | 2026-01-04 | [S3] |',report)
        incomplete,_=render_answer('已逾期2件。',[{**overdue,'completeness':{'query_rows':3,'has_more':True}}],query)
        self.assertNotIn('完整案号与承办人',incomplete)

    def test_combined_due_rows_replace_partial_draft(self):
        from tools.answer_contracts import render_answer,contract_issues
        source={'id':'S2','type':'database',
                'snippet':json.dumps({'due_field':'draft_due_date','due_statuses':['overdue','next14']}),
                'columns':['所编号','代理师','初稿期限／所限'],
                'rows':[['AI-2026-001','甲','2026-11-04'],['AI-2026-002','乙','2026-11-06']],
                'completeness':{'query_rows':2,'has_more':False,'fetch_truncated':False}}
        unknown={**source,'id':'S3','snippet':json.dumps({'due_field':'draft_due_date','due_status':'unknown'}),
                 'rows':[],'completeness':{'query_rows':0,'has_more':False,'fetch_truncated':False}}
        query='初稿逾期和两周内到期、未填的案件，给全部案号和承办人。'
        with patch.object(analytics,'today',return_value=__import__('datetime').date(2026,11,5)):
            report,_=render_answer('只拿到前5条，{{quote:Q0000000000000000}}',[source,unknown],query)
        self.assertIn('AI-2026-001 | 甲',report)
        self.assertIn('AI-2026-002 | 乙',report)
        self.assertNotIn('只拿到前5条',report)
        self.assertFalse(contract_issues(report,[source,unknown]))

    def test_confirmed_scanned_table_honors_requested_bar_chart(self):
        from tools.chart_utils import build_chart_items
        source={'id':'S1','type':'file','title':'scan.png','revision':'r1',
                'extraction_method':'mineru','content_complete':True,
                'snippet':'[第 1 页 · 正文块 4 · 表格]\n| 部件 | 编号 | 数量 |\n| --- | --- | --- |\n| 传感器 | A | 3 |\n| 控制器 | B | 1 |\n| 电机 | C | 2 |'}
        visual={'id':'S2','type':'file','title':'scan.png','revision':'r1',
                'visual_observation':True,
                'snippet':'传感器 A 3\n控制器 B 1\n电机 C 2'}
        query='按部件数量画张柱状图'
        charts=build_chart_items([source,visual],query)
        self.assertEqual(len(charts),1)
        self.assertEqual(charts[0]['chart']['type'],'bar')
        self.assertEqual(charts[0]['chart']['series'][0]['data'],[3,1,2])
        self.assertEqual(charts[0]['chart']['sourceIds'],['S1','S2'])
        self.assertFalse(chart_delivery_issues(query,[source,visual]))
        self.assertTrue(chart_delivery_issues(query,[source]))

    def test_current_workload_date_is_program_bound_and_explicit_history_is_kept(self):
        from datetime import date
        from tools.answer_contracts import workload_summary_date_issues
        item={'answer_contract':{'kind':'workload_summary','as_of':'2028-02-14'}}
        with patch.object(analytics,'today',return_value=date(2028,11,4)):
            self.assertTrue(workload_summary_date_issues('按今天接入的案件信息统计',[item]))
            self.assertFalse(workload_summary_date_issues('按2028年2月14日统计',[item]))
            item['answer_contract']['as_of']='2028-11-04'
            self.assertFalse(workload_summary_date_issues('按今天统计',[item]))

    def test_full_workload_details_and_real_chart_entry_survive_delivery(self):
        from tools.answer_contracts import fact_block
        from tools.chart_utils import chart_delivery_issues,chart_presentation_spans
        item={'id':'S5','columns':['承办人','在办案件数量'], 'rows':[['甲',1],['乙',0]],
              'statistics':{'dimensions':[{'column':'owner','label':'承办人','index':0}],
                            'metrics':[{'column':'n','label':'在办案件数量','index':1,'definition_verified':True}]},
              'answer_contract':{'kind':'workload_summary','version':1,'scope':'完整快照',
                  'complete_snapshot':True,'owner_case_details':[
                      {'owner':'甲','active_case_ids':['X9'],'unknown_stage_case_ids':[]},
                      {'owner':'乙','active_case_ids':[],'unknown_stage_case_ids':['X8']}]}}
        text=fact_block(item)
        self.assertIn('|甲|X9|无|[S5]|',text)
        self.assertIn('|乙|无|X8|[S5]|',text)
        query='按承办人统计在办案件数量，画柱状图'
        self.assertFalse(chart_delivery_issues(query,[item],text+'\n图表见图表页。'))
        ascii_graph='```\n甲 ██\n```\n\n注：柱状图以代码块示意呈现，如需图片版可再告知。'
        self.assertTrue(chart_delivery_issues(query,[item],text+ascii_graph))
        self.assertEqual(len(chart_presentation_spans(ascii_graph)),2)

    def test_review_json_transport_keeps_bound_ids_and_semantic_gates(self):
        from langchain_core.messages import AIMessage
        from tools.content_verification import (CRITERIA,bound_review_schema,
            review_anchor_index,parse_review_response,finalize_review)
        result={'query':'核对案量','report':'在办2件。[S1]','required_sources':['db_query'],
                'evidence':[{'id':'S1','type':'database'}]}
        anchors=review_anchor_index(result)
        self.assertLessEqual(len(anchors['answer'][0]['id']),13)
        payload={'verdict':'passed','checks':[{'criterion':c,'status':'passed','detail':'已核对',
            'task_anchor_id':anchors['task'][0]['id'],'answer_anchor_id':anchors['answer'][0]['id'],
            'evidence_ids':['S1']} for c in CRITERIA]}
        schema=bound_review_schema(result,{'S1'})
        response={'parsed':None,'raw':AIMessage(content=json.dumps(payload)),
                  'parsing_error':ValueError('no function call')}
        decision=parse_review_response(response,schema)
        self.assertEqual(finalize_review(decision,result,{'S1'})['status'],'passed')
        payload['checks'][0]['answer_anchor_id']=anchors['answer'][0]['id'][:-1]
        response['raw']=AIMessage(content=json.dumps(payload))
        with self.assertRaises(ValueError):parse_review_response(response,schema)
        payload['checks'][0]['answer_anchor_id']=anchors['answer'][0]['id']
        payload['checks'][0].update(status='failed',action='revise_answer',suggestion='修正案量')
        response['raw']=AIMessage(content=json.dumps(payload))
        with self.assertRaisesRegex(ValueError,'总判定'):
            finalize_review(parse_review_response(response,schema),result,{'S1'})

    def test_owner_aggregate_includes_zero_and_separates_unknown_and_actual_filed(self):
        cases=[dict(case_id='X1',owner_name='甲',stage='撰写中'),
               dict(case_id='X2',owner_name='甲',stage='客户审核',filing_verified=True,actual_filed_date='2028-01-02'),
               dict(case_id='X3',owner_name='乙',stage='未知'),
               dict(case_id='X4',owner_name='丙',stage='已完成')]
        with patch.object(analytics.store,'list_cases',return_value=cases),patch.object(analytics.store,'coverage',return_value={}):
            data=analytics.workload_summary(as_of='2028-01-03')
        self.assertEqual([(r['owner'],r['active_count'],r['unknown_stage_count']) for r in data['owners']], [('甲',1,0),('丙',0,0),('乙',0,1)])
        self.assertFalse(workload_requested('按承办人画登记负荷柱状图'))
        self.assertTrue(workload_requested('查甲的登记负荷'))

    def test_empty_deadline_group_proof_is_not_a_paginated_guess(self):
        with patch.object(analytics.store,'list_cases',return_value=[dict(case_id='X1',stage='撰写中',draft_due_date='2028-01-01')]),patch.object(analytics.store,'coverage',return_value={}):
            data=analytics.cases_page(due_field='draft_due_date',due_statuses=['overdue','unknown'],as_of='2028-01-03')
        self.assertEqual(data['due_group_proofs']['unknown']['count'],0)
        self.assertTrue(data['due_group_proofs']['unknown']['complete'])
        self.assertEqual(data['due_group_proofs']['overdue']['count'],1)

    def test_short_quote_selectors_bind_source_revision_and_bad_ids_are_not_placeholders(self):
        item={'id':'S4','type':'file','revision':'a','snippet':'此方案先校验版本。再执行检索。'}
        token=quote_options(item)[0]['quote_token']
        answer,_=render_answer(token,[item]);self.assertIn('“此方案先校验版本。”[S4]',answer)
        wrong,_=render_answer(token,[{**item,'revision':'b'}])
        self.assertTrue(contract_issues(wrong,[item]))
        self.assertFalse(has_placeholders(wrong))
        self.assertTrue(has_placeholders('{{未填}}'))

    def test_grouped_chart_requires_real_aggregation_and_retains_lineage(self):
        query='按承办人统计在办案量，给我柱状图'
        self.assertTrue(chart_delivery_issues(query,[]))
        raw={'id':'S1','type':'file','columns':['承办人','数量'],'rows':[['甲',1],['乙',2]]}
        self.assertTrue(chart_delivery_issues(query,[raw]))
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'cases.csv'
            path.write_text('案号,承办人,阶段,期限\nX1,甲,写作,\nX2,甲,审核,\nX3,乙,关闭,2028-01-01\n')
            data=query_table(path,filters=[{'field':'阶段','op':'in','value':['写作','审核']}],group_by=['承办人'],aggregates=[{'field':'案号','op':'count','alias':'在办案量'}])
        self.assertEqual(data['rows'],[['甲',2]])
        self.assertEqual(data['source_row_groups'],[[2,3]])
        self.assertEqual(data['quality']['null_counts']['期限'],2)
        self.assertFalse(chart_delivery_issues(query,[{'id':'S2','type':'file',**data}]))

    def test_long_tables_are_verified_without_llm_but_wrong_cell_still_fails(self):
        item={'id':'S1','type':'database','rows':[[f'承办人{i}',i] for i in range(260)],
              'statistics':{'metrics':[{'column':'n','index':1,'label':'在办案件数量','names':['在办案件数量'],
                  'unit':'件','definition_verified':True,'time':{'status':'unbounded'},'filters':[]}],
                  'dimensions':[{'column':'owner','index':0,'label':'承办人','role':'dimension'}]}}
        text='## '+('完整发布范围说明'*30)+'\n\n| 承办人 | 在办案件数量（件） | 来源 |\n|---|---|---|\n'
        text+='\n'.join(f'| 承办人{i} | {i} | [S1] |' for i in range(260))
        with patch('agent.llm.get_llm',side_effect=AssertionError('tables need no model')):
            result=asyncio.run(check_statistics(text,[item]))
        self.assertEqual(result['status'],'passed',result['issues'][:2])
        self.assertEqual(result['checked_claims'],260)
        wrong=text.replace('| 承办人259 | 259 |','| 承办人259 | 258 |')
        self.assertEqual(asyncio.run(check_statistics(wrong,[item]))['status'],'failed')

    def test_transposed_month_table_keeps_values_bound_to_their_own_month(self):
        items=[]
        for ident,month,value in [('S1','09',10),('S2','10',12)]:
            items.append({'id':ident,'type':'database','rows':[[value]],'statistics':{'dimensions':[],
                'metrics':[{'column':'received_count','index':0,'names':['实际收案数量'],'label':'实际收案数量','unit':'件',
                'definition_verified':True,'time':{'status':'bounded','field':'received_date',
                'start':f'2028-{month}-01','end':'2028-10-01' if month=='09' else '2028-11-01'},'filters':[]}]}})
        text='# 2028年10月部门月报\n\n| 指标 | 2028年9月 | 2028年10月 | 来源 |\n|---|---|---|---|\n| 实际收案数量（件） | 10 | 12 | [S1][S2] |'
        result=asyncio.run(check_statistics(text,items))
        self.assertEqual(result['status'],'passed',result['issues'])
        wrong=text.replace('| 10 | 12 |','| 12 | 10 |')
        self.assertEqual(asyncio.run(check_statistics(wrong,items))['status'],'failed')

    def test_pending_definition_cannot_be_marked_complete_even_with_other_chart_data(self):
        from api import clarification
        from tools import reflect_check
        item={'id':'S1','type':'file','columns':['承办人','阶段'],'rows':[['甲','撰写中']]}
        with patch('api.context.get_thread_id',return_value='definition-test'),patch('api.evidence.snapshot',return_value=[item]):
            held=clarification.definition_question_from_answer('按承办人统计在办案量','在办口径需要你先确认，按哪个口径计算？',[item])
        self.assertEqual(held['kind'],'task_definition')
        self.assertEqual(clarification.pending('definition-test')['task_question']['id'],held['id'])
        clarification.clear('definition-test')
        self.assertTrue(reflect_check.hard_check({'query':'存成Markdown文件','report':'已保存报告。'}))

    def test_modified_sentences_are_not_all_preserved_even_without_deletion_records(self):
        from tools.reflect_check import hard_check
        data={'report':'修订版未删去基准版的任何句子。','evidence':[{'id':'S1','type':'file','comparison_counts':{'modified':2,'deleted':0,'added':1}}]}
        self.assertTrue(any('修改与保留' in i for i in hard_check(data)))
        self.assertTrue(has_placeholders('{S1 facts_token}'))

    def test_natural_month_comparison_and_citation_clauses_keep_the_typed_contract(self):
        from casework.workflow import DepartmentRequest
        from casework.requirements import TaskRequirement,canonical_constraint
        from casework.verification import task_contract_issues
        request=DepartmentRequest(operation='monthly',start_month='2028-09',end_month='2028-10',compare_months=True,
                  requirements=[TaskRequirement(kind='monthly_counts',quote='2028年10月实际收案和交局'),
                                TaskRequirement(kind='monthly_comparison',quote='比一比9月'),
                                TaskRequirement(kind='markdown_report',quote='存成Markdown文件')])
        query='2028年10月实际收案和交局，比一比9月，存成Markdown文件'
        self.assertFalse(task_contract_issues(query,request,{}))
        self.assertEqual(canonical_constraint('统计出处也留着'),'cite_evidence')
        from casework.requirements import action_for
        with self.assertRaises(ValueError):
            action_for([TaskRequirement(kind='markdown_report',quote='存成Markdown文件'),
                        TaskRequirement(kind='case_list',quote='按承办人列案件')],query)
