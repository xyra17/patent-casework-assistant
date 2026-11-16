"""Paired offline checks for observed business failures and human review packets."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tools.answer_quality import answer_checks, answer_issues
from tools.answer_contracts import quote_options, render_answer
from tools.assistance_handoff import build_packet, for_run, digest
from tools.content_verification import review_failure
from tools.web_excerpt import focus_excerpt


def fixture():
    text = '基准“保留七天”。修订“撤权失效”。'
    ref = hashlib.sha256(text.encode()).hexdigest()
    items = [{'id': 'S1', 'type': 'file', 'title': '基准.md', 'revision': 'a', 'location': '第2行', 'snippet': '保留七天'},
             {'id': 'S2', 'type': 'file', 'title': '修订.md', 'revision': 'b', 'location': '第2行', 'snippet': '撤权失效'}]
    run = {'status': 'completed', 'query': '比较原句', 'result': text, 'evidence': items, 'events': [
        {'event': 'product_boundary_request', 'data': {'profile': 'document-comparison', 'professional_judgments': ['机制关系']}},
        {'event': 'reflection', 'data': {'round': 1, 'candidate_report_ref': ref, 'verification': {'checks': [
            {'criterion': 'task_completion', 'status': 'failed', 'detail': '编号疑点待核对'}]}}},
        {'event': 'quality_summary', 'data': {'returned_round': 1, 'passed': False}}]}
    return run, lambda key: text if key == ref else None


class QualityChecks(unittest.TestCase):
    def test_explicit_length_only_and_notice_exclusion(self):
        self.assertTrue(answer_issues('最多3字', '这是四字', []))
        self.assertTrue(answer_issues('控制在3字', '这是四字', []))
        self.assertTrue(answer_issues('要求3字以内', '这是四字', []))
        self.assertFalse(answer_issues('简短回答', '文'*2000, []))
        self.assertFalse(answer_issues('最多3字', '**专业判断使用边界（系统说明）**：长说明\n\n三字内[S1]', []))

    def test_relative_offset_cannot_impersonate_original_page(self):
        items = [{'id':'S6', 'snippet':'文'*230, 'location':'第 1 页 · 字符258–487',
                  'spans':[{'start':257, 'end':487, 'location':'第 1 页'}]}]
        self.assertTrue(answer_issues('', '第1页字符37–60[S6]', items))
        self.assertFalse(answer_issues('', '第1页字符295–317[S6]', items))
        self.assertFalse(answer_issues('', '片段内字符37–60[S6]', items))
        self.assertTrue(answer_issues('', '片段内字符37–260[S6]', items))
        self.assertTrue(answer_issues('', '第2页字符295–317[S6]', items))
        self.assertFalse(answer_checks('', '字符37–60[S9]', items))
        self.assertFalse(answer_checks('', '字符37–60[S6]', [{'id':'S6','location':'摘要'}]))

    def test_negated_professional_conclusion_does_not_add_a_requirement(self):
        from tools.product_boundary import request_contract
        neutral=request_contract('列原句，不作专业最相近或机制等价结论。',{'profile':'disclosure-search'})
        self.assertEqual(neutral['professional_judgments'],[])
        affirmative=request_contract('判断最相近的机制，说明借鉴关系。',{'profile':'disclosure-search'})
        self.assertTrue(affirmative['professional_judgments'])

    def test_heading_boundaries_and_only_supported_overlap_removed(self):
        item = {'id':'S1', 'type':'file', 'snippet':'# 缓存方案\n缓存条目保留七天，到期后清理。\n每次检查租户。'}
        self.assertIn('缓存条目保留七天，到期后清理。', [o['original'] for o in quote_options(item)])
        rendered, audit = render_answer('“保留七天”“缓存条目保留七天，到期后清理。”[S1][S1]', [item])
        self.assertEqual(rendered, '“缓存条目保留七天，到期后清理。”[S1]')
        reverse, _ = render_answer('“缓存条目保留七天，到期后清理。”“保留七天”[S1]', [item])
        self.assertEqual(reverse, '“缓存条目保留七天，到期后清理。”[S1]')
        fake, _ = render_answer('“保留七天”“缓存条目保留七天，永远不清理。”[S1]', [item])
        self.assertIn('“保留七天”', fake)
        raw = json.dumps({'answer':'[S1][S1]'}, ensure_ascii=False)
        self.assertEqual(render_answer(raw, [item])[0], raw)


class ExcerptChecks(unittest.TestCase):
    def test_deep_body_selected_and_spans_are_literal(self):
        text = '[HNSW](#hnsw)\n\n' + 'Flat index documentation. '*400 + '\n\n## HNSW\nHNSW uses a graph index. M controls neighbors. efSearch sets search effort.\n\n' + 'ending '*500
        selected, receipt = focus_excerpt(text, 'HNSW', 1200)
        self.assertIn('uses a graph index', selected)
        self.assertTrue(receipt['focus_found'])
        self.assertTrue(receipt['focus_in_excerpt'])
        expected = '\n\n[…原文中间部分未展示…]\n\n'.join(text[s['start']:s['end']] for s in receipt['source_spans'])
        self.assertEqual(selected, expected)
        self.assertLessEqual(sum(s['end']-s['start'] for s in receipt['source_spans']),1200)

    def test_related_heading_preserves_parameter_lines_without_repeated_focus(self):
        text='Flat '*1000+'\n\n## `IndexHNSW` variants\n\nHNSW graph overview.\n\n- M controls neighbors.\n\n- efConstruction controls adding.\n\n- efSearch controls searching.\n\n### Encodings\n\nIndexHNSW supports flat.\n\n## Unrelated\nOther content.'
        excerpt,receipt=focus_excerpt(text,'HNSW')
        self.assertIn('efSearch controls searching',excerpt)
        self.assertNotIn('## Unrelated',excerpt)
        self.assertTrue(receipt['focus_in_excerpt'])

    def test_no_match_empty_and_tiny_budget_are_honest(self):
        selected, receipt = focus_excerpt('Flat '*1000, 'HNSW', 100)
        self.assertFalse(receipt['focus_found'])
        self.assertEqual(len(selected),100)
        for budget in (0, 4, 12, 3600):
            selected, receipt = focus_excerpt('a '*100+' HNSW explanation', 'HNSW', budget)
            self.assertLessEqual(sum(s['end']-s['start'] for s in receipt['source_spans']),budget)
        self.assertEqual(focus_excerpt('', 'HNSW')[0], '')

    def test_no_focus_preserves_existing_body_prefix_and_original_offsets(self):
        from tools.internet_search_tool import _result_excerpt
        raw='navigation\n'*1000+'\n# Abstract\nReadable body.'
        text,receipt,_=_result_excerpt({'content':'Summary','raw_content':raw},True)
        self.assertIn('Readable body.',text)
        span=receipt['raw_selection']['source_spans'][0]
        self.assertEqual(raw[span['start']:span['end']],'# Abstract\nReadable body.')

    def test_raw_page_text_never_blends_rewritten_summary_into_original_evidence(self):
        from tools.internet_search_tool import _result_excerpt
        text,receipt,_=_result_excerpt({'content':'HNSW always has perfect accuracy. 4d + x M 2 4',
            'raw_content':'## HNSW\nM affects accuracy. Formula: 4*d + x * M * 2 * 4.'},True,'HNSW')
        self.assertNotIn('perfect accuracy',text)
        self.assertIn('4*d + x * M * 2 * 4',text)
        self.assertFalse(receipt['summary_included'])
        self.assertEqual(receipt['content_kind'],'provider_page_text')
        text,receipt,_=_result_excerpt({'content':'HNSW summary'},False,'HNSW')
        self.assertEqual(receipt['content_kind'],'provider_search_excerpt')
        self.assertTrue(receipt['summary_included'])

    def test_search_focus_uses_raw_preserving_privacy_and_shared_budget(self):
        from tools import internet_search_tool as web
        from api import context, source_policy, task_sources
        response={'results':[{'title':'官方页','url':'https://example.org/reference','content':'Flat only',
                             'raw_content':'Flat '*2000+'\n\nHNSW is a graph index.'}]}
        with task_sources.scope(['web_search']), patch('casework.privacy.ensure_web_allowed'), \
                patch.object(web._client,'search',return_value=response) as search, \
                patch.object(context,'consume_tool_budget',return_value=True), \
                patch.object(context,'get_thread_id',return_value=''), \
                patch.object(web.evidence,'add',return_value='S1') as add:
            source_policy.configure(['web_search'])
            output=web.internet_search.invoke({'query':'Faiss HNSW'})
            self.assertIn('HNSW is a graph index',output)
            self.assertTrue(search.call_args.kwargs['include_raw_content'])
            self.assertTrue(add.call_args.args[1]['web_excerpt']['focus_found'])
        with task_sources.scope(['web_search']), patch('casework.privacy.ensure_web_allowed'), \
                patch.object(context,'consume_tool_budget',return_value=False), patch.object(web._client,'search') as search:
            self.assertIn('上限', web.internet_search.invoke({'query':'HNSW'}))
            search.assert_not_called()
        with task_sources.scope(['web_search']), patch('casework.privacy.ensure_web_allowed', side_effect=PermissionError('材料不允许联网')), \
                patch.object(web._client,'search') as search:
            self.assertIn('禁止执行', web.internet_search.invoke({'query':'HNSW'}))
            search.assert_not_called()


class PacketChecks(unittest.TestCase):
    def test_bound_packet_never_changes_verdict_and_stale_material_hidden(self):
        from tools.result_assessment import assess_result
        run, loader=fixture()
        before=assess_result(run)
        packet=build_packet(run,run['result'],run['evidence'],loader)
        self.assertIsNone(packet['human_confirmation'])
        self.assertFalse(packet['acceptance_changed'])
        self.assertFalse(packet['program_comparison_verified'])
        run['events'].append({'event':'analysis_handoff','data':packet})
        self.assertEqual(for_run(run),packet)
        after=assess_result(run)
        for key in ('facts','completeness','delivery','product_boundary'):
            self.assertEqual(before[key],after[key])
        for mutation in ('text','evidence','packet','running'):
            changed=copy.deepcopy(run)
            if mutation=='text':changed['result']+='改写'
            if mutation=='evidence':changed['evidence'][0]['revision']='other'
            if mutation=='packet':changed['events'][-1]['data']['model_draft']['text']='伪造'
            if mutation=='running':changed['status']='running'
            self.assertIsNone(for_run(changed), mutation)

    def test_program_comparison_requires_intact_proof_and_same_source_ids(self):
        run,loader=fixture()
        proof={'status':'VERIFIED','source_ids':['S1','S2'],'records':[]}
        proof['proof_sha256']=digest(proof)
        run['events'].append({'event':'product_objective','data':proof})
        self.assertTrue(build_packet(run,run['result'],run['evidence'],loader)['program_comparison_verified'])
        for ids in ([],['S9']):
            proof['source_ids']=ids
            proof['proof_sha256']=digest({k:v for k,v in proof.items() if k!='proof_sha256'})
            self.assertFalse(build_packet(run,run['result'],run['evidence'],loader)['program_comparison_verified'])
        proof['source_ids']=['S1','S2']
        self.assertFalse(build_packet(run,run['result'],run['evidence'],loader)['program_comparison_verified'])

    def test_missing_or_wrong_draft_does_not_reuse_last_round(self):
        run,loader=fixture()
        self.assertIsNone(build_packet(run,run['result'],run['evidence'],lambda _: '其他草稿'))
        run['events'][-1]['data']['returned_round']=2
        self.assertIsNone(build_packet(run,run['result'],run['evidence'],loader))

    def test_pending_findings_and_content_gaps_survive_unavailable_review(self):
        run,loader=fixture()
        review=run['events'][1]['data']
        review.update(soft_review_status='skipped',soft_review_detail='调用未完成',verification={'checks':[]},
                      evidence_gaps=[{'detail':'尚未取得后部正文'}])
        run['events'][-1]['data']['issue_lifecycle']=[{'kind':'content','status':'pending','detail':'早轮编号疑点'}]
        packet=build_packet(run,run['result'],run['evidence'],loader)
        self.assertTrue(any(c['item']=='早轮编号疑点' for c in packet['manual_checks']))
        self.assertTrue(any(c['kind']=='evidence_gap' for c in packet['manual_checks']))
        self.assertTrue(any(c['kind']=='review_unavailable' for c in packet['manual_checks']))

    def test_typed_failure_and_generic_error_are_not_model_incompetence(self):
        budget=RuntimeError('do not publish secret request content')
        budget.reason='model_call_budget'
        self.assertEqual(review_failure(budget)['kind'],'model_call_budget')
        self.assertEqual(review_failure(TimeoutError())['kind'],'timeout')
        unknown=review_failure(RuntimeError('secret'))
        self.assertEqual(unknown['kind'],'unclassified_call_failure')
        self.assertNotIn('secret',str(unknown))


class PacketAPIIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_execution_event_and_rest_return_same_source_bound_packet(self):
        from api import server, storage, evidence
        import httpx
        run,loader=fixture()
        with tempfile.TemporaryDirectory() as tmp, patch.object(storage,'_DB_PATH',Path(tmp)/'app.db'):
            storage.init_db()
            storage.create_run('handoff-test',run['query'],run['query'],False)
            async def execute(*args):
                for event in run['events']:
                    storage.append_event('handoff-test',event)
                for item in run['evidence']:
                    evidence.add('handoff-test',{k:v for k,v in item.items() if k!='id'})
                ref=run['events'][1]['data']['candidate_report_ref']
                storage.save_context_artifact('handoff-test',ref,loader(ref))
                return run['result']
            with patch.object(server,'run_deep_agent',side_effect=execute), \
                    patch.object(server,'build_query_result',AsyncMock(return_value=None)):
                await server._execute_task('handoff-test',run['query'])
            stored=storage.get_run('handoff-test')
            self.assertEqual(stored['status'],'completed',stored.get('error'))
            packet=stored['result_assessment']['review_packet']
            self.assertIsNotNone(packet)
            event=next(e for e in stored['events'] if e['event']=='task_result')
            self.assertEqual(event['data']['result_assessment']['review_packet'],packet)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
                response=await client.get('/api/runs/handoff-test')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['result_assessment']['review_packet'],packet)
            evidence.clear('handoff-test')


class PreparedComparisonReuse(unittest.TestCase):
    def test_unchanged_files_and_same_evidence_reuse_the_program_result_without_rereading(self):
        from api import context,evidence
        from tools import attachment_compare as compare
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'attachments').mkdir()
            (root/'attachments/old.md').write_text('标题\n保留七天\n检查租户\n')
            (root/'attachments/new.md').write_text('标题\n撤权失效\n检查租户\n')
            context.set_session('comparison-reuse',tmp)
            try:
                evidence.reset('comparison-reuse')
                args={'left_filename':'old.md','right_filename':'new.md','include_unchanged':True,'limit':100}
                with patch.object(compare,'_document_changes',wraps=compare._document_changes) as align:
                    first=compare.compare_uploaded_files.invoke(args)
                    self.assertEqual(json.loads(first)['status'],'compared')
                    with patch.object(Path,'read_bytes',side_effect=AssertionError('unchanged file reread')):
                        second=compare.compare_uploaded_files.invoke({**args,'left_filename':'attachments/old.md'})
                    self.assertEqual(first,second)
                    self.assertEqual(align.call_count,1)
                    # A file version change invalidates the saved alignment.
                    (root/'attachments/new.md').write_text('标题\n权限撤销后立即失效\n检查租户\n')
                    changed=compare.compare_uploaded_files.invoke(args)
                    self.assertNotEqual(first,changed)
                    self.assertEqual(align.call_count,2)
                    # Missing source identities cannot inherit a cached proof.
                    evidence.reset('comparison-reuse')
                    compare.compare_uploaded_files.invoke(args)
                    self.assertEqual(align.call_count,3)
                context.reset_session()
                context.set_session('comparison-other',tmp)
                self.assertFalse(context.task_read_cache()['entries'])
            finally:
                evidence.clear('comparison-reuse');context.reset_session()
