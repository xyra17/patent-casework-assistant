"""Execution scope and local-only semantic fallback boundaries."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,MagicMock,patch
from tools.case_executor import explicit_contract,execute
from tools.case_relevance import CaseRelevanceDecision,DocumentRelevance

QUERY=('技术条件为：弹性臂进入锁槽且底部阻尼减振。只在符合该技术机制的案卷范围内，统计2032-03实际收案数量和2032-04已核实实际交局数量。'
       '同案副本去重，不用计划日期补实际日期。仅用本会话附件，不联网、不查数据库或知识库，不生成文件。'
       '请只返回JSON对象，字段为received_count、filed_count和evidence_ids；数量为整数，evidence_ids填实际工具返回的证据S编号数组。')

class ContinuousRepairs(unittest.TestCase):
 def test_general_structured_task_gets_program_join_contract_without_losing_filters(self):
  from api import context,evidence,storage
  from agent import main_agent
  from langchain_core.messages import AIMessage
  question=QUERY.replace('技术条件为：','目标技术为：')+'仅限甲客户。'
  graph=MagicMock();graph.ainvoke=AsyncMock(return_value={'messages':[AIMessage(content='待核实')]})
  with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'_DB_PATH',Path(tmp)/'app.db'):
   storage.init_db();context.set_session('general-join-test',tmp);evidence.reset('general-join-test')
   try:
    with patch.object(main_agent,'create_deep_agent',return_value=graph),patch.object(main_agent.path_utils,'get_upload_dir',return_value=Path(tmp)/'uploads'):
     asyncio.run(main_agent.main_agent_run(question,1,[]))
    prompt=graph.ainvoke.call_args.args[0]['messages'][0].content
    self.assertIn(question,prompt)
    self.assertIn('必须调用 aggregate_case_records',prompt)
    self.assertIn('不能自行手数',prompt)
    self.assertIn('附加条件',prompt)
   finally:context.reset_session();evidence.clear('general-join-test')

 def test_query_relations_expand_candidates_but_cannot_create_source_assertions(self):
  from casework.parser import query_tags,tags
  queries=['大模型平滑概率指导轻量预测器学习','轻量网络学习概率软目标','分层邻接图删除项等批次回收旧边']
  self.assertIn('模型蒸馏',query_tags(queries[0]))
  self.assertIn('模型蒸馏',query_tags(queries[1]))
  self.assertIn('向量近邻检索',query_tags(queries[2]))
  self.assertNotIn('模型蒸馏',tags(queries[0]))
  self.assertNotIn('模型蒸馏',query_tags('不采用大模型概率指导轻量预测器学习'))
  self.assertNotIn('模型蒸馏',query_tags('小网络仅用于概率预测'))

 def test_extra_filters_and_output_requirements_never_use_narrow_executor(self):
  self.assertEqual(explicit_contract(QUERY)['received_month'],'2032-03')
  for extra in ['仅统计承办人甲的案件。','另按客户拆分。','每一案附法定期限。','只保留2032年4月以前已公开的案子。']:
   self.assertIsNone(explicit_contract(QUERY+extra))
  self.assertIsNone(explicit_contract(QUERY.replace('2032-03实际收案','2032-03或2032-02实际收案')))

 def test_forced_join_uses_schema_not_filenames_and_independent_dates(self):
  from api import context,evidence,storage
  from tools.case_records import check_case_answer
  docs=[{'case_id':'X1','text':'弹性臂进入锁槽并以底部阻尼垫减振。'},
        {'case_id':'X2','text':'弹性臂进入锁槽，不设置阻尼件。'}]
  ledger=[{'case_id':'X1','owner_name':'甲','received_date':'2032-03-31','actual_filed_date':'2032-04-01','filing_verified':True,'stage':'撰写中'},
          {'case_id':'X2','owner_name':'乙','received_date':'2032-03-01','actual_filed_date':'2032-04-01','filing_verified':True}]
  model=MagicMock();call=AsyncMock();model.with_structured_output.return_value=call
  call.ainvoke.return_value={'parsed':CaseRelevanceDecision(documents=[
    DocumentRelevance(case_id='X1',relevant=True,quote=docs[0]['text'],detail='两项均正向满足'),
    DocumentRelevance(case_id='X2',relevant=False,detail='缺少减振')])}
  with tempfile.TemporaryDirectory() as tmp,patch.object(storage,'_DB_PATH',Path(tmp)/'app.db'):
   storage.init_db();context.set_session('forced-test',tmp);context.set_round(1);evidence.reset('forced-test')
   try:
    d=Path(tmp)/'attachments';d.mkdir()
    (d/'甲.json').write_text(json.dumps(docs));(d/'乙.json').write_text(json.dumps(ledger))
    result=asyncio.run(execute(QUERY,lambda:model));result['query']=QUERY
    self.assertEqual(json.loads(result['report'])['received_count'],1)
    self.assertEqual(json.loads(result['report'])['filed_count'],1)
    self.assertEqual(check_case_answer(result)['status'],'passed')
    self.assertEqual(call.ainvoke.call_count,1)
    context.set_round(2);context.reset_tool_budget()
    retry=asyncio.run(execute(QUERY,lambda:model,feedback='前轮请重新核对技术条件'))
    self.assertEqual(retry['execution_mode'],'bounded_case_join')
    self.assertEqual(json.loads(retry['report'])['filed_count'],1)
    self.assertEqual(call.ainvoke.call_count,2)
   finally:context.reset_session();evidence.clear('forced-test')

 def test_negation_scope_and_stable_ties(self):
  from casework.local_reranker import technical_passage
  from tools.hybrid_retrieval import LexicalIndex
  self.assertEqual(technical_passage('# 元数据\n## 技术正文\n本案不采用下述机制：HNSW分层检索。删除项保留标记。'),'')
  self.assertIn('弹性臂',technical_passage('弹性臂进入槽；不采用电池温控。'))
  records=[{'text':'技术正文相同','metadata':{'case_id':i,'source':'x','chunk_id':x}} for i,x in [('B','1'),('A','9')]]
  a=[r['metadata']['case_id'] for r in LexicalIndex(records).search('技术正文')]
  records.reverse()
  self.assertEqual(a,[r['metadata']['case_id'] for r in LexicalIndex(records).search('技术正文')])

 def test_remote_concept_endpoint_is_refused_before_connect(self):
  from casework.concept_router import propose
  for url in ['https://127.0.0.1/v1','http://example.com/v1','http://user:secret@127.0.0.1/v1']:
   with patch.dict('os.environ',{'CASEWORK_CONCEPT_ROUTER_URL':url}),patch('urllib.request.urlopen') as network:
    self.assertEqual(propose('请求')['status'],'invalid_local_endpoint');network.assert_not_called()

 def test_lexical_only_never_calls_semantic_services(self):
  from casework.local_reranker import rank
  with patch.dict('os.environ',{'CASEWORK_EMBEDDING':'0'}),patch('transformers.AutoTokenizer.from_pretrained') as load:
   self.assertIsNone(rank('q',[]));load.assert_not_called()

if __name__=='__main__':unittest.main()
