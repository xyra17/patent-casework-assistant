"""Search intent, background/negative mentions, and execution-scoped continuation."""
import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from casework.archive_intent import ArchiveIntent, bind_intent, require_scope
from casework.archive_selection import select_candidates
from casework.workflow import DepartmentIntent, DepartmentRequest, compile_intent, render
from api import clarification


BACKGROUND='SYN-AI-2026-001\n摘要\n本案主要解决缓存量化误差，测量通道分布并执行分组量化与误差回退。\n技术领域与具体问题\n背景中介绍推测解码，方案并未采用推测解码。'
STEP='通过推测解码生成草稿并仅提交目标模型接受的连续前缀'


def candidates():
    return {'results':[
        {'case_id':'AI-2026-001','sources':[{'kind':'application','excerpt':BACKGROUND,'evidence_id':'S1'}]},
        {'case_id':'AI-2026-002','sources':[{'kind':'application','excerpt':STEP,'evidence_id':'S2'}]}],
        'has_more':False,'completion_filter':'未限制案件状态'}


def relations():
    return [{'case_id':'AI-2026-001','relation':'mention','quote':'方案并未采用推测解码','detail':'背景和否定提及，未证明采用'},
            {'case_id':'AI-2026-002','relation':'mechanism','quote':STEP,'core_quote':STEP,'detail':'本案处理链采用推测解码'}]


def model_factory(decisions, calls=None):
    class Model:
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):
            if calls is not None:calls.append(messages)
            payload=json.loads(messages[-1]['content'].split('\n前次契约错误：')[0])
            ids={d['case_id'] for d in payload['documents']}
            return {'parsed':{'documents':[copy.deepcopy(d) for d in decisions if d['case_id'] in ids]}}
    return Model


class ArchiveScopeTests(unittest.TestCase):
    def test_scope_is_bound_to_original_task_not_short_retrieval_query(self):
        for task,mode in [('找所有提及推测解码的案件','mention'),('找以推测解码为核心的案件','topic'),
                          ('找采用推测解码的案件','mechanism'),('找推测解码相关案件','ambiguous')]:
            intent=DepartmentIntent(query='推测解码',requirements=[{'kind':'archive_materials','quote':task}])
            request=compile_intent(intent,task)
            self.assertEqual(request.query,'推测解码')
            self.assertEqual(request.archive_intent.mode,mode)
        with self.assertRaises(ValueError):
            bind_intent('找X相关案件',ArchiveIntent(mode='topic',quote='找X相关案件'))
        with self.assertRaises(ValueError):
            bind_intent('不要找以X为核心的案件',ArchiveIntent(mode='topic',quote='以X为核心'))

    def test_semantic_scope_synonyms_and_explicit_conflicts(self):
        task='我要的是专门解决草稿验证问题的材料'
        scope=ArchiveIntent(mode='topic',quote='专门解决草稿验证问题')
        self.assertEqual(bind_intent(task,scope).mode,'topic')
        with self.assertRaises(ValueError):
            bind_intent('找所有提及X的案件',ArchiveIntent(mode='mechanism',quote='提及X'))

    def test_ambiguous_and_mention_preserve_negative_background_with_labels(self):
        raw=candidates();before=copy.deepcopy(raw);calls=[]
        result=asyncio.run(select_candidates(raw,'推测解码相关案件',model_factory(relations(),calls)))
        self.assertEqual(raw,before)
        self.assertEqual(result['selection_review']['mode'],'ambiguous')
        self.assertEqual([c['relevance']['relation'] for c in result['results']],['mechanism','mention'])
        self.assertEqual(len(calls),2)
        self.assertIn('方案并未采用推测解码',calls[0][-1]['content'])
        mentioned=asyncio.run(select_candidates(raw,'找所有提及推测解码的案件',model_factory(relations())))
        self.assertEqual(len(mentioned['results']),2)
        self.assertEqual(mentioned['results'][1]['relevance']['evidence_id'],'S1')

    def test_topic_does_not_include_secondary_use(self):
        decisions=relations();decisions[0]['relation']='topic'
        decisions[0]['quote']='本案主要解决缓存量化误差，测量通道分布并执行分组量化与误差回退'
        decisions[0]['core_quote']=decisions[0]['quote']
        result=asyncio.run(select_candidates(candidates(),'以量化为核心的案件',model_factory(decisions)))
        self.assertEqual([c['case_id'] for c in result['results']],['AI-2026-001'])

    def test_a_step_alone_is_not_a_topic_but_a_critical_topic_need_not_adopt(self):
        decisions=relations();decisions[1]['relation']='topic'
        result=asyncio.run(select_candidates(candidates(),'推测解码相关案件',model_factory(decisions)))
        self.assertEqual(result['selection_review']['status'],'unverified')
        raw=candidates();text='本案主要研究推测解码的误差风险，方案不采用推测解码。'
        raw['results']=raw['results'][:1];raw['results'][0]['sources'][0]['excerpt']=text
        decisions=[{'case_id':'AI-2026-001','relation':'topic','quote':text,'core_quote':text,
                    'detail':'核心主题是风险分析，未采用该技术'}]
        result=asyncio.run(select_candidates(raw,'以推测解码为核心的案件',model_factory(decisions)))
        self.assertEqual(result['selection_review']['status'],'passed')
        self.assertEqual(result['results'][0]['relevance']['relation'],'topic')

    def test_high_impact_other_request_asks_scope_before_general_execution(self):
        from casework.workflow import resolve,DepartmentIntentRoute
        task='统计推测解码相关案件数量'
        intent=DepartmentIntent(query='推测解码',requirements=[
            {'kind':'archive_materials','quote':'推测解码相关案件'}, {'kind':'other','quote':task}],
            archive_intent=ArchiveIntent(purpose='count',purpose_quote=task))
        class Model:
            def with_structured_output(self,*a,**kw):return self
            async def ainvoke(self,*a,**kw):return {'parsed':DepartmentIntentRoute(request=intent)}
        with patch('agent.llm.get_llm',return_value=Model()),patch('casework.store.list_cases',return_value=[]),patch('casework.catalog.meetings',return_value=[]):
            with self.assertRaises(clarification.ClarificationRequired):asyncio.run(resolve(task))

    def test_explicit_scope_metadata_survives_bounded_semantic_parameter_repair(self):
        from casework.workflow import resolve,DepartmentIntentRoute
        task='找所有提及推测解码的案件'
        intent=DepartmentIntent(query='推测解码',requirements=[{'kind':'archive_materials','quote':task}])
        bad=intent.model_copy(update={'archive_intent':ArchiveIntent(mode='mechanism',quote='提及推测解码')})
        responses=iter([bad,intent])
        class Model:
            def with_structured_output(self,*a,**kw):return self
            async def ainvoke(self,*a,**kw):return {'parsed':DepartmentIntentRoute(request=next(responses))}
        with patch('agent.llm.get_llm',return_value=Model()),patch('casework.store.list_cases',return_value=[]),patch('casework.catalog.meetings',return_value=[]):
            request=asyncio.run(resolve(task))
        self.assertEqual(request.archive_intent.mode,'mention')

    def test_background_cannot_be_promoted_and_fabricated_quote_cannot_pass(self):
        for mutation in ('promotion','fabrication','missing','duplicate'):
            decisions=relations()
            if mutation=='promotion':
                decisions[0].update(relation='mechanism',core_quote='背景中介绍推测解码，方案并未采用推测解码')
            elif mutation=='fabrication':decisions[0]['quote']='不存在的推测解码原文'
            elif mutation=='missing':decisions.pop()
            else:decisions.append(copy.deepcopy(decisions[0]))
            result=asyncio.run(select_candidates(candidates(),'查所有提及推测解码的案件',model_factory(decisions)))
            self.assertEqual(result['selection_review']['status'],'unverified',mutation)
            self.assertEqual(len(result['results']),2)
            by_id={c['case_id']:c for c in result['results']}
            if mutation in ('promotion','fabrication'):
                # A bad quote degrades only its own case, even within one batch.
                self.assertTrue(by_id['AI-2026-001']['candidate_only'])
                self.assertFalse(by_id['AI-2026-002']['candidate_only'])
                self.assertEqual(by_id['AI-2026-002']['relevance']['quote'],STEP)
            else:
                # Missing/duplicate IDs invalidate the whole response structure.
                self.assertTrue(all(c['candidate_only'] for c in result['results']))

    def test_unknown_stays_candidate_and_exact_case_reads_bypass_classification(self):
        decisions=relations();decisions[0].update(relation='unverified',quote='')
        result=asyncio.run(select_candidates(candidates(),'推测解码相关案件',model_factory(decisions)))
        self.assertEqual(result['results'][1]['relevance']['relation'],'unverified')
        raw=candidates()
        self.assertIs(asyncio.run(select_candidates(raw,'AI-2026-001')),raw)

    def test_ambiguous_downstream_use_pauses_but_browsing_does_not(self):
        for task in ('统计X相关案件数量','判断X相关案件的办理经历','给X相关案件推荐代理师'):
            scope=bind_intent(task)
            with self.assertRaises(clarification.ClarificationRequired) as caught:
                require_scope(scope,task,{})
            question=caught.exception.question
            self.assertEqual(question['kind'],'archive_scope')
            self.assertEqual(len(question['options']),3)
        require_scope(bind_intent('找X相关案件'),'找X相关案件',{})
        require_scope(bind_intent('统计所有提及X的案件数量'),'统计所有提及X的案件数量',{})

    def test_scope_answer_is_execution_scoped_and_preserves_previous_supplement(self):
        question=clarification.search_scope_question('统计X相关案件数量',{})
        run={'execution_id':'run1','status':'awaiting_input','clarification':question,'allowed_sources':['knowledge'],
             'execution_query':'统计X相关案件数量\n已补充范围A','clarification_response':{'choice':'supplement'}}
        answer={'execution_id':'run1','question_id':question['id'],'choice':'scope_mention'}
        response=clarification.validate_answer(run,answer,['knowledge'])
        query=clarification.extend_query('原始问题',response,run)
        self.assertIn('已补充范围A',query)
        self.assertEqual(bind_intent(query).mode,'mention')
        with self.assertRaises(ValueError):clarification.validate_answer(run,{**answer,'execution_id':'stale'},['knowledge'])
        with self.assertRaises(ValueError):clarification.validate_answer(run,answer,['knowledge','db_query'])
        recommendation=clarification.recommendation_question(DepartmentRequest(operation='recommend',query='X'),{}, {'results':[]})
        with self.assertRaises(ValueError):
            clarification.validate_answer({**run,'clarification':recommendation},
                {**answer,'question_id':recommendation['id']},['knowledge'])

    def test_scope_request_propagates_instead_of_falling_back_to_general_route(self):
        from casework.routing import select_route
        q=clarification.search_scope_question('统计X相关案件数量',{})
        with patch('casework.workflow.resolve',AsyncMock(side_effect=clarification.ClarificationRequired(q,'确认',[]))):
            with self.assertRaises(clarification.ClarificationRequired):asyncio.run(select_route('统计X相关案件数量'))

    def test_render_distinguishes_mentions_and_does_not_call_them_adopted(self):
        result=asyncio.run(select_candidates(candidates(),'找所有提及推测解码的案件',model_factory(relations())))
        for c in result['results']:c['document_facts']={'field_evidence_ids':{}}
        report=render(result,DepartmentRequest(operation='archive',query='推测解码'))
        self.assertIn('查询口径：提及该概念',report)
        self.assertIn('关联程度：仅提及',report)
        self.assertIn('关联依据：方案并未采用推测解码 [S1]',report)
        self.assertNotIn('可借鉴机制的原文片段',report)


class RetrievalScopeTests(unittest.TestCase):
    def test_negative_mechanism_block_is_still_recalled_for_mentions(self):
        from casework import store,sync
        from casework.search import search
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir()
            (source/'.casework-demo.json').write_text(json.dumps({'kind':'independently_authored_synthetic'}))
            (source/'AI-2026-001.申请文件.md').write_text(
                '所编号：AI-2026-001\n发明名称：对比缓存方法\n代理师：甲\n案件状态：初稿撰写\n'
                '## 技术正文\n\n本案不采用以下机制：向量数据库近邻检索。\n采用普通哈希表。')
            with patch.dict(os.environ,{'CASEWORK_DB_PATH':str(Path(tmp)/'cases.db'),
                'CASEWORK_ALLOWED_ROOTS':json.dumps([str(source)]),'CASEWORK_EMBEDDING':'0','CASEWORK_MYSQL_MIRROR':'0'}):
                root=store.save_root('范围测试',str(source),'synthetic')
                self.assertEqual(sync.run(root['id'])['state'],'ready')
                mentioned=search('向量数据库近邻检索',query_scope='mention',allow_vector=False)
                self.assertEqual([c['case_id'] for c in mentioned['results']],['AI-2026-001'])
                self.assertFalse(mentioned['exhaustive'])
                mechanisms=search('向量数据库近邻检索',query_scope='mechanism',allow_vector=False)
                self.assertFalse(mechanisms['results'])
                # Exercise the actual tool, renderer, source binding, and business
                # verification against the same published temporary index.
                from api import context
                from casework.workflow import execute
                entries=[]
                def add(thread,item):
                    sid='S'+str(len(entries)+1);entries.append({**item,'id':sid});return sid
                task='找所有提及向量数据库近邻检索的案件'
                request=compile_intent(DepartmentIntent(query='向量数据库近邻检索',requirements=[
                    {'kind':'archive_materials','quote':task}]),task)
                decision=[{'case_id':'AI-2026-001','relation':'mention',
                           'quote':'本案不采用以下机制：向量数据库近邻检索',
                           'detail':'否定提及，不证明技术采用'}]
                context.set_session('archive-scope-test',tmp)
                try:
                    with patch('tools.casework_tools._guard',return_value=None),patch('api.evidence.add',side_effect=add),\
                        patch('api.evidence.snapshot',return_value=entries),patch('api.monitor.monitor.report',AsyncMock()),\
                        patch('agent.llm.get_llm',side_effect=lambda *a,**kw:model_factory(decision)()):
                        result=asyncio.run(execute(request,'archive-scope-test',1,['knowledge']))
                    self.assertEqual(result['business_check']['status'],'passed',result['business_check']['issues'])
                    self.assertEqual(result['business_delivery']['query_scope'],'mention')
                    self.assertFalse(result['business_delivery']['exhaustive'])
                    self.assertIn('关联程度：仅提及',result['report'])
                    self.assertNotIn('技术相关性待核实',result['report'])
                finally:
                    context.clear_tool_budgets('archive-scope-test');context.reset_session()


if __name__=='__main__':unittest.main()
