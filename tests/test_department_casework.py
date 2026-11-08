"""Independent assertions for the shared-drive business boundaries."""
import json,os,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from casework import analytics,catalog,parser,store,sync
from casework.search import search
from casework.privacy import ensure_model_allowed,ensure_web_allowed

ROOT=Path(__file__).resolve().parents[1]

class DepartmentCaseworkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.source=self.base/'drive';shutil.copytree(ROOT/'examples/department_shared_drive/source',self.source)
        self.env=patch.dict(os.environ,{'CASEWORK_DB_PATH':str(self.base/'catalog.db'),'CASEWORK_ALLOWED_ROOTS':json.dumps([str(self.source)]),'CASEWORK_EMBEDDING':'0','CASEWORK_MYSQL_MIRROR':'0','DATA_MODE':'demo'})
        self.env.start();self.root=store.save_root('测试部门',str(self.source),'synthetic');self.ident=self.root['id']
        job=sync.run(self.ident);self.assertEqual(job['state'],'ready',job['summary'])
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def case(self,ident):return store.get_case(self.ident+':'+ident)
    def test_ledger_gold_and_three_dates(self):
        self.assertEqual(len(store.list_cases()),34)
        c=self.case('DEMO-2026-024');self.assertIsNone(c['draft_due_date']);self.assertEqual(c['fields']['draft_due_date']['state'],'conflict')
        self.assertEqual(c['client_review_due_date'],'2026-10-25');self.assertEqual(c['filing_due_date'],'2026-11-10')
    def test_demo_numbering_and_independent_client_case_id(self):
        cases=store.list_cases()
        self.assertTrue(all(parser.CASE_RE.fullmatch(c['case_id']) and c['case_id'].startswith('DEMO-') for c in cases))
        self.assertEqual(len({c['client_case_id'] for c in cases}),34)
        page=analytics.cases_page(query='DEMO-2026-014')
        self.assertEqual(page['rows'][0]['client_case_id'],'DEMO-CLIENT-018')
        self.assertEqual(analytics.cases_page(query='DEMO-CLIENT-018')['rows'][0]['case_id'],'DEMO-2026-014')

    def test_six_domains_have_filterable_cases_and_verified_history(self):
        from casework.domains import DOMAINS
        self.assertEqual({c['technical_domain'] for c in store.list_cases()},set(DOMAINS))
        self.assertEqual({c['case_id'] for c in analytics.cases_page(domain='车')['rows']},{'DEMO-2026-026','DEMO-2026-029'})
        gold=json.loads((ROOT/'examples/department_shared_drive/expected.json').read_text())
        for person in gold['people']:analytics.save_person(**person)
        for query,domain,owner,ident in [('卡扣连接减振结构','结构','陆衡（模拟）','DEMO-2026-025'),('电池热管理冷却回路','车','顾远（模拟）','DEMO-2026-026'),('终端任务调度后台任务队列','终端软件','程简（模拟）','DEMO-2026-027')]:
            result=analytics.recommend(query,domain=domain,as_of='2026-10-27')
            person=next(p for p in result['candidates'] if p['name']==owner)
            self.assertTrue(person['domain_match'])
            expected = ({ident, 'DEMO-2026-028'} if ident == 'DEMO-2026-025' else
                        {ident, 'DEMO-2026-029'} if ident == 'DEMO-2026-026' else
                        {ident, 'DEMO-2026-030'})
            self.assertEqual({c['case_id'] for c in person['history_matches']}, expected)
            self.assertEqual(person['active_count'],1)
    def test_meeting_attendance_not_owner_and_client_identity(self):
        records=catalog.meetings(client='客户甲（模拟）',ipr='IPR林（模拟）')
        self.assertEqual(len(records),2)
        self.assertEqual({r['date'] for r in records},{'2025-09-12','2025-09-19'})
        for r in records:self.assertNotIn('林澄（模拟）',r['agents']);self.assertIn('周行（模拟）',r['agents'])
        self.assertEqual(len(catalog.meetings()),5)
    def test_reports_count_occurrences_not_mtime_or_due_date(self):
        r=analytics.report('2026-09','2026-10');self.assertEqual([(t['received_count'],t['filed_count']) for t in r['totals']],[(4,1),(3,1)])
        self.assertEqual([c['case_id'] for c in r['unknown_received']],['DEMO-2026-022'])
        self.assertIn('DEMO-2026-006',[c['case_id'] for c in r['unknown_filed']])
    def test_semantic_tags_and_negative_control(self):
        self.assertNotIn('向量近邻检索',parser.tags('本案不涉及向量检索，也不使用向量数据库。'))
        self.assertIn('向量近邻检索',parser.tags('通过HNSW近邻索引实现语义向量检索。'))
        self.assertIn('光通信',parser.tags('不同波长的信号在同一根光纤里传输。'))
        self.assertNotIn('光通信',parser.tags('本案不采用不同波长的光纤传输。'))
        self.assertNotIn('光通信',parser.tags('测量光纤材料的波长响应。'))
        hits=search('向量数据库近邻检索',limit=30)
        self.assertEqual({r['case_id'] for r in hits['results']},set(json.loads((ROOT/'examples/department_shared_drive/expected.json').read_text())['vector_cases']))
    def test_recommend_history_evidence_and_no_automatic_assignment(self):
        gold=json.loads((ROOT/'examples/department_shared_drive/expected.json').read_text())
        for p in gold['people']:analytics.save_person(**p)
        before=store.digest(store.list_cases());r=analytics.recommend('向量数据库近邻检索',domain='AI',as_of='2026-10-26')
        candidates={p['name']:p for p in r['candidates']}
        self.assertEqual(candidates['许宁（模拟）']['history_match_count'],0)
        self.assertGreater(candidates['林澄（模拟）']['history_match_count'],0)
        self.assertEqual({c['case_id'] for c in candidates['林澄（模拟）']['near_due_cases']},{'DEMO-2026-013','DEMO-2026-019','DEMO-2026-022'})
        self.assertEqual(candidates['林澄（模拟）']['near_due_by_node']['filing_due_date'],1) # 11-10 is outside [10-26,11-09)
        self.assertEqual({c['case_id'] for c in candidates['林澄（模拟）']['overdue_cases']},{'DEMO-2026-013'})
        self.assertIn('[2026-10-26，2026-11-09)',r['deadline_definition'])
        self.assertEqual(store.digest(store.list_cases()),before)
        c=self.case('DEMO-2026-021');person=next(p for p in analytics.list_people() if p['name']=='许宁（模拟）')
        with self.assertRaises(ValueError):analytics.assign(c['case_key'],person['id'],c['version'],'test')
        updated=analytics.assign(c['case_key'],person['id'],c['version'],'已核对技术与负荷',confirmed=True)
        self.assertFalse(updated['shared_drive_written']);self.assertEqual(updated['case']['owner_name'],person['name'])
        with self.assertRaises(ValueError):analytics.assign(c['case_key'],person['id'],c['version'],'stale',confirmed=True)
    def test_confirmation_conflict_and_invalidation(self):
        c=self.case('DEMO-2026-024');candidate=c['fields']['draft_due_date']['candidates'][0]
        updated=catalog.confirm(c['case_key'],{'draft_due_date':candidate['value']},c['version'],'核对原文',source_id=candidate['file_id'])
        self.assertEqual(updated['fields']['draft_due_date']['state'],'confirmed')
        path=self.source/candidate['path'];path.write_bytes(path.read_bytes()+b'\n') # raw content revision changes, even if field values stay stable
        sync.run(self.ident)
        self.assertEqual(self.case('DEMO-2026-024')['fields']['draft_due_date']['state'],'confirmation_stale')
    def test_actual_filing_requires_source(self):
        c=self.case('DEMO-2026-006')
        with self.assertRaises(ValueError):catalog.confirm(c['case_key'],{'actual_filed_date':'2026-10-02'},c['version'],'无来源')
        with self.assertRaises(ValueError):catalog.confirm(c['case_key'],{'filing_confirmed':True},c['version'],'无来源')
    def test_unchanged_scan_and_rename_delete(self):
        self.assertEqual(sync.run(self.ident)['summary']['parsed'],0)
        docs=catalog.documents(self.ident+':DEMO-2025-001');d=next(x for x in docs if x['kind']=='application')
        original=self.source/d['relative_path'];renamed=original.with_name('交底_改名.md');original.rename(renamed)
        job=sync.run(self.ident);self.assertEqual(job['summary']['removed'],1);self.assertEqual(job['summary']['renamed'],1)
        self.assertTrue(self.case('DEMO-2025-001'));old=catalog.document(d['id']);self.assertFalse(old['current'])
        renamed.unlink();sync.run(self.ident);self.assertTrue(self.case('DEMO-2025-001'))
    def test_mass_missing_requires_confirmation(self):
        for child in list(self.source.iterdir()):
            if child.is_dir():shutil.rmtree(child)
        j=sync.run(self.ident);self.assertEqual(j['state'],'needs_confirmation');self.assertEqual(len(store.list_cases()),34)
        sync.run(self.ident,confirm_missing=True);self.assertEqual(store.list_cases(),[])
    def test_unavailable_and_partial_scan_never_clear_catalog(self):
        self.source.rename(self.base/'unmounted')
        self.assertEqual(sync.run(self.ident)['state'],'unavailable');self.assertEqual(len(store.list_cases()),34)
        (self.base/'unmounted').rename(self.source)
        with patch.dict(os.environ,{'CASEWORK_MAX_FILES':'2'}):self.assertEqual(sync.run(self.ident)['state'],'partial')
        self.assertEqual(len(store.list_cases()),34)
    def test_paths_symlinks_and_mismatched_case(self):
        with self.assertRaises(ValueError):store.save_root('bad',str(self.base),'local_only')
        with self.assertRaises(ValueError):sync.read_source(self.root,'../secret')
        secret=self.base/'secret.txt';secret.write_text('secret');link=self.source/'DEMO-2026-099.交底.txt';link.symlink_to(secret)
        with self.assertRaises(OSError):sync.read_source(self.root,link.name)
        with self.assertRaises(ValueError):parser.parse(b'{"case_id":"DEMO-2026-002"}','DEMO-2026-001/record.json')
    def test_mislabelled_word_and_encoding(self):
        from docx import Document
        import io
        d=Document();d.add_paragraph('发明名称：测试内容');stream=io.BytesIO();d.save(stream)
        p=parser.parse(stream.getvalue(),'DEMO-2026-099.立案.doc');self.assertEqual(p['format'],'.docx')
        csv='所编号,代理师\nDEMO-2026-099,测试人\n'.encode('gb18030');self.assertTrue(parser.parse(csv,'DEMO-2026-099/案件信息.csv')['claims'])
    def test_private_material_blocks_remote_even_after_disable(self):
        r=store.save_root('私密测试',str(self.source),'synthetic',ident=self.ident,expected_version=store.get_root(self.ident)['version'])
        # Separate private root, no real user data involved.
        private=self.base/'private';private.mkdir()
        with patch.dict(os.environ,{'CASEWORK_ALLOWED_ROOTS':json.dumps([str(self.source),str(private)]),'DEEPSEEK_BASE_URL':'https://api.deepseek.com'}):
            r=store.save_root('私密',str(private),'local_only')
            with self.assertRaises(PermissionError):ensure_model_allowed()
            with self.assertRaises(PermissionError):ensure_web_allowed()
            store.save_root('私密',str(private),'local_only',ident=r['id'],expected_version=r['version'],enabled=False)
            with self.assertRaises(PermissionError):ensure_model_allowed()
        with patch.dict(os.environ,{'DEEPSEEK_BASE_URL':'http://127.0.0.1:11434/v1'}):ensure_model_allowed()
    def test_mirror_failure_explicit_not_rollback_local(self):
        with patch.dict(os.environ,{'CASEWORK_MYSQL_MIRROR':'1'}),patch('mysql.connector.connect',side_effect=RuntimeError('offline')):
            from casework.mirror import sync_mirror
            self.assertEqual(sync_mirror(self.ident)['state'],'error')
        self.assertEqual(len(store.list_cases()),34);self.assertEqual(store.get_root(self.ident)['mirror_state'],'error')

    def test_api_confirm_clear_stale_and_download_revision(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.casework import router
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            c=self.case('DEMO-2026-024');path='/api/casework/cases/'+c['case_key']
            r=client.post(path+'/confirm',json={'changes':{'draft_due_date':'2026-10-28'},'expected_version':c['version'],'reason':'核对两份原件后选择'})
            self.assertEqual(r.status_code,200,r.text);current=r.json()
            self.assertEqual(client.post(path+'/confirm',json={'changes':{'draft_due_date':'2026-10-30'},'expected_version':c['version'],'reason':'旧视图'}).status_code,409)
            r=client.post(path+'/clear-confirmation',json={'field':'draft_due_date','expected_version':current['version'],'reason':'恢复原件冲突状态'})
            self.assertEqual(r.status_code,200,r.text);self.assertIsNone(r.json()['draft_due_date'])
            doc=catalog.documents(c['case_key'])[0];url='/api/casework/documents/'+doc['id']+'/download'
            self.assertEqual(client.get(url).status_code,200)
            f=self.source/doc['relative_path'];f.write_bytes(f.read_bytes()+b' changed')
            self.assertEqual(client.get(url).status_code,409)
            self.assertGreater(client.get('/api/casework/audit').json()['total'],0)

    def test_old_parser_record_keeps_matching_original_downloadable(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.casework import router
        doc=catalog.documents(self.ident+':DEMO-2026-012')[0]
        with store.db(write=True) as conn:
            previous=conn.execute('SELECT * FROM revisions WHERE file_id=? AND revision=? AND parser_key=?',
                                  (doc['id'],doc['revision'],doc['parser_key'])).fetchone()
            conn.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',
                         (doc['id'],doc['revision'],'new-parser',previous['payload'],store.now()))
            conn.execute('UPDATE files SET parser_key=? WHERE id=?',('new-parser',doc['id']))
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            url='/api/casework/documents/'+doc['id']
            selected={'revision':doc['revision'],'parser_key':doc['parser_key']}
            old=client.get(url,params=selected).json()
            self.assertFalse(old['current']);self.assertEqual(old['version_status'],'parser_updated')
            self.assertTrue(old['content_current']);self.assertTrue(old['download_available'])
            self.assertEqual(old['source_status'],'verified');self.assertEqual(old['current_parser_key'],'new-parser')
            self.assertNotIn('移除',old['version_note'])
            raw=(self.source/doc['relative_path']).read_bytes()
            self.assertEqual(client.get(url+'/download',params={'revision':doc['revision']}).content,raw)
            latest=client.get(url).json();self.assertTrue(latest['current'])
            self.assertEqual(client.get(url,params=selected).json()['parser_key'],doc['parser_key'])
            # A later content change must never silently download different bytes for that reference.
            (self.source/doc['relative_path']).write_bytes(raw+b'\n')
            changed=client.get(url,params=selected).json()
            self.assertFalse(changed['download_available']);self.assertEqual(changed['source_status'],'changed')
            self.assertEqual(client.get(url+'/download',params={'revision':doc['revision']}).status_code,409)
            sync.run(self.ident)
            old=client.get(url,params=selected).json()
            self.assertEqual(old['version_status'],'content_changed');self.assertFalse(old['download_available'])
            self.assertEqual(client.get(url+'/download',params={'revision':doc['revision']}).status_code,409)

    def test_source_status_distinguishes_removed_disabled_and_parse_failure(self):
        doc=catalog.documents(self.ident+':DEMO-2026-012')[0]
        for state,active,enabled,expected in [('error',1,1,'parse_failed'),('parsed',0,1,'removed'),('parsed',1,0,'disabled')]:
            with self.subTest(expected=expected):
                with store.db(write=True) as conn:
                    conn.execute('UPDATE files SET state=?,active=? WHERE id=?',(state,active,doc['id']))
                    conn.execute('UPDATE roots SET enabled=? WHERE id=?',(enabled,self.ident))
                selected=catalog.document(doc['id'],doc['revision'],doc['parser_key'])
                self.assertEqual(selected['version_status'],expected);self.assertFalse(selected['current'])
                self.assertEqual(selected['download_available'],bool(active and enabled))

    def test_unreadable_original_keeps_saved_text_but_disables_download(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.casework import router
        doc=catalog.documents(self.ident+':DEMO-2026-012')[0]
        original=catalog.document(doc['id'])['payload']['chunks']
        (self.source/doc['relative_path']).unlink()
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            response=client.get('/api/casework/documents/'+doc['id'])
            self.assertEqual(response.status_code,200)
            body=response.json();self.assertEqual(body['chunks'],original[:10])
            self.assertEqual(body['source_status'],'unavailable');self.assertFalse(body['download_available'])

    def test_api_monthly_export_pagination_and_assignment_confirmation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.casework import router,csv_cell
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            page=client.get('/api/casework/cases?limit=5').json();self.assertEqual(len(page['rows']),5);self.assertTrue(page['has_more'])
            body=client.get('/api/casework/reports/monthly.csv?start_month=2026-09&end_month=2026-10')
            self.assertEqual(body.status_code,200);self.assertIn('DEMO-2026-015',body.text);self.assertTrue(csv_cell('=2+3').startswith("'"))
            c=self.case('DEMO-2026-021');person=analytics.save_person('测试代理师',['AI'])
            path='/api/casework/cases/'+c['case_key']+'/assign'
            data={'person_id':person['id'],'expected_version':c['version'],'reason':'人工核对','confirmed':False}
            self.assertEqual(client.post(path,json=data).status_code,400)
            data['confirmed']=True;r=client.post(path,json=data)
            self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['shared_drive_written'])

    def test_monthly_answer_template_checks_numbers_dates_and_metrics(self):
        import asyncio
        from tools.casework_tools import query_department_cases
        from tools.statistics_check import check_statistics
        entries=[]
        def add(thread,item):
            entry={**item,'id':'S'+str(len(entries)+1)};entries.append(entry);return entry['id']
        with patch('api.evidence.add',side_effect=add):
            result=json.loads(query_department_cases.invoke({'operation':'monthly','start_month':'2026-09','end_month':'2026-10'}))
        report=result['answer_markdown']
        checked=asyncio.run(check_statistics(report,entries))
        self.assertEqual(checked['status'],'passed',checked);self.assertEqual(checked['checked_claims'],8)
        wrong=report.replace('| 4 [','| 40 [')
        self.assertEqual(asyncio.run(check_statistics(wrong,entries))['status'],'failed')
        wrong=report.replace('2026年9月','2026年8月')
        self.assertEqual(asyncio.run(check_statistics(wrong,entries))['status'],'failed')
        wrong=report.replace('实际交局数量','实际收案数量')
        self.assertEqual(asyncio.run(check_statistics(wrong,entries))['status'],'failed')

    def test_source_gate_and_knowledge_does_not_expose_platform_override(self):
        from api import context,source_policy
        from tools.casework_tools import search_case_archive,query_department_cases
        c=self.case('DEMO-2025-001');catalog.confirm(c['case_key'],{'owner_name':'平台人工变更'},c['version'],'仅平台记录')
        try:
            context.set_session('test-department-gates',str(self.base));context.set_round(1)
            source_policy.configure(['knowledge'],['db_query','web_search'])
            self.assertIn('禁止执行',query_department_cases.invoke({'operation':'cases'}))
            with patch('api.evidence.add',return_value='S1'):
                result=json.loads(search_case_archive.invoke({'query':'DEMO-2025-001'}))
            found=result['results'][0];self.assertEqual(found['owner_name'],'林澄（模拟）');self.assertNotIn('平台人工变更',json.dumps(result,ensure_ascii=False))
            with patch('api.evidence.add',return_value='S1'):
                result=json.loads(search_case_archive.invoke({'query':'向量数据库','owner':'平台人工变更'}))
            self.assertEqual(result['results'],[])
            source_policy.configure(['db_query'],['knowledge'])
            self.assertIn('禁止执行',search_case_archive.invoke({'query':'向量'}))
        finally:
            context.clear_tool_budgets('test-department-gates');context.reset_session()

    def test_cached_model_callback_rechecks_privacy_before_each_call(self):
        from agent.llm import _PrivacyGuard
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        model=FakeListChatModel(responses=['模拟'],callbacks=[_PrivacyGuard()])
        with patch.dict(os.environ,{'DEEPSEEK_BASE_URL':'https://api.deepseek.com'}):
            self.assertEqual(model.invoke('公开模拟输入').content,'模拟')
            private=self.base/'private';private.mkdir()
            with patch.dict(os.environ,{'CASEWORK_ALLOWED_ROOTS':json.dumps([str(private)])}):store.save_root('私密',str(private),'local_only')
            with self.assertRaises(PermissionError):model.invoke('此内容不应抵达模型')

    def test_exact_case_reads_body_and_natural_description_retrieval(self):
        hits=search('DEMO-2026-013')
        self.assertEqual([h['case_id'] for h in hits['results']],['DEMO-2026-013'])
        self.assertTrue(any('HNSW' in p['excerpt'] for p in hits['results'][0]['sources']))
        self.assertIn('HNSW',hits['results'][0]['sources'][0]['excerpt'])
        self.assertFalse(search('本批案卷没有记载的月球采矿推进燃料工艺')['results'])

    def test_fixed_workflows_render_query_sources_and_validate_statistics(self):
        import asyncio
        from tools.casework_tools import query_department_cases
        from casework.workflow import DepartmentRequest,render
        from tools.statistics_check import check_statistics
        gold=json.loads((ROOT/'examples/department_shared_drive/expected.json').read_text())
        for p in gold['people']:analytics.save_person(**p)
        for params in ({'operation':'cases','due_field':'draft_due_date','due_status':'overdue','as_of':'2026-10-26'},
                       {'operation':'monthly','start_month':'2026-09','end_month':'2026-10'},
                       {'operation':'meetings','client':'客户甲（模拟）','ipr':'IPR林（模拟）'},
                       {'operation':'recommend','query':'向量数据库近邻检索','domain':'AI','as_of':'2026-10-26'}):
            entries=[]
            def add(thread,item):
                entry={**item,'id':'S'+str(len(entries)+1)};entries.append(entry);return entry['id']
            with patch('api.evidence.add',side_effect=add):result=json.loads(query_department_cases.invoke(params))
            answer=render(result,DepartmentRequest(**params))
            check=asyncio.run(check_statistics(answer,entries))
            self.assertIn(check['status'],('passed','not_required'),check)
            if params['operation']=='meetings':self.assertNotIn('林澄',answer)
            if params['operation']=='recommend':
                self.assertIn('未保存分案',answer);self.assertIn('2026-10-26',answer)
                self.assertIn('前三位候选',answer);self.assertIn('初稿已逾期',answer)
                self.assertIn('2026-11-09之前',answer);self.assertIn('状态未知案件数量',answer)
                self.assertNotIn('同案多个节点在该项中不重复计数',answer)
            if params['operation']=='cases':
                self.assertNotIn('DEMO-2026-017',answer)
                self.assertEqual({r['case_id'] for r in result['rows']},{'DEMO-2026-013','DEMO-2026-021'})
            for line in answer.splitlines():
                if line.startswith('|'):self.assertTrue(line.endswith('|'),line)

    def test_read_snapshot_does_not_mix_published_generations(self):
        import threading
        before=store.get_root(self.ident)['generation'];errors=[]
        def publish():
            try:
                with store.db(write=True) as conn:
                    conn.execute('UPDATE roots SET generation=generation+1 WHERE id=?',(self.ident,))
                    conn.execute('UPDATE cases SET generation=generation+1 WHERE root_id=?',(self.ident,))
            except Exception as exc:errors.append(exc)
        with store.read_snapshot():
            self.assertEqual(store.get_root(self.ident)['generation'],before)
            worker=threading.Thread(target=publish);worker.start();worker.join(timeout=5)
            self.assertFalse(worker.is_alive());self.assertFalse(errors)
            self.assertEqual(store.coverage()['roots'][0]['generation'],before)
            self.assertTrue(all(c['generation']==before for c in store.list_cases()))
            with self.assertRaises(RuntimeError):
                with store.db(write=True):pass
        self.assertEqual(store.get_root(self.ident)['generation'],before+1)
        self.assertTrue(all(c['generation']==before+1 for c in store.list_cases()))

    def test_empty_whitelist_and_unsupported_workflow_filters(self):
        from casework.workflow import DepartmentRequest,plan_details,validate_request
        with patch.dict(os.environ,{'CASEWORK_ALLOWED_ROOTS':'[]'}):
            self.assertEqual(store.allowed_roots(),[])
            with self.assertRaises(ValueError):store.approved_path(str(self.source))
        for params in ({'operation':'meetings','owner':'不可忽略的筛选'},
                       {'operation':'monthly','start_month':'2026-09','end_month':'2026-10','domain':'AI'},
                       {'operation':'monthly','start_month':'2026-09','end_month':'2026-10','as_of':'2026-09-10'},
                       {'operation':'cases','due_status':'overdue'},
                       {'operation':'case_brief','query':'DEMO-2026-001和DEMO-2026-002比较'},
                       {'operation':'recommend','query':''}):
            with self.assertRaises(ValueError):DepartmentRequest(**params)
        request=DepartmentRequest(operation='cases',client='客户甲（模拟）')
        page=analytics.cases_page(client=request.client)
        self.assertTrue(page['rows']);self.assertTrue(all(r['client_alias']==request.client for r in page['rows']))
        self.assertIn(request.client,plan_details(request)['intent_summary'])
        self.assertIsNone(validate_request('DEMO-2026-006可计入2026年10月吗？',DepartmentRequest(operation='monthly',start_month='2026-10',end_month='2026-10')))
        self.assertIsNone(validate_request('核对DEMO-2026-006',DepartmentRequest(operation='cases',query='DEMO-2026-007')))

    def test_archive_workflow_cites_body_owner_and_filing_separately(self):
        from tools.casework_tools import search_case_archive
        from casework.workflow import DepartmentRequest,render
        entries=[]
        def add(thread,item):
            entry={**item,'id':'S'+str(len(entries)+1)};entries.append(entry);return entry['id']
        with patch('api.evidence.add',side_effect=add):
            result=json.loads(search_case_archive.invoke({'query':'向量数据库近邻检索','completed_only':True,'query_scope':'mechanism'}))
        self.assertEqual({c['case_id'] for c in result['results']},{'DEMO-2025-001','DEMO-2025-002','DEMO-2026-001','DEMO-2026-007'})
        answer=render(result,DepartmentRequest(operation='archive',query='向量数据库近邻检索',completed_only=True))
        self.assertNotIn('DEMO-2026-013',answer);self.assertNotIn('DEMO-2025-004',answer)
        self.assertIn('原文件记载承办人',answer);self.assertIn('HNSW',answer)
        known={e['id']:e for e in entries}
        for case in result['results']:
            facts=case['document_facts']
            for sid in facts['field_evidence_ids']['owner_name']:
                self.assertIn(facts['owner_name'],known[sid]['snippet'])
            for p in case['sources']:self.assertEqual(known[p['evidence_id']]['snippet'],p['excerpt'])
        self.assertIn('仅按原件完成状态筛选',__import__('casework.workflow',fromlist=['plan_details']).plan_details(DepartmentRequest(operation='archive',query='近邻',completed_only=True))['intent_summary'])

    def test_single_case_brief_keeps_sources_and_business_uncertainty(self):
        import asyncio
        from api import context,evidence,source_policy
        from casework.workflow import DepartmentRequest,execute,sources_for
        request=DepartmentRequest(operation='case_brief',query='DEMO-2026-013')
        self.assertEqual(sources_for(request),['db_query','knowledge'])
        context.set_session('case-brief-test',str(self.base));source_policy.configure(sources_for(request),['web_search'])
        events=[];entries=[]
        async def report(thread,kind,message,data):events.append((kind,data))
        def add(thread,item):
            entry={**item,'id':'S'+str(len(entries)+1)};entries.append(entry);return entry['id']
        try:
            with patch('api.evidence.add',side_effect=add),patch('api.evidence.snapshot',return_value=entries),patch('api.monitor.monitor.report',side_effect=report):
                result=asyncio.run(execute(request,'case-brief-test',1,sources_for(request)))
            self.assertEqual([e[1]['tool_name'] for e in events if e[0]=='tool_start'],['query_department_cases','search_case_archive','collect_case_materials'])
            self.assertTrue(all((e[1]['args'].get('query') or e[1]['args'].get('case_id'))=='DEMO-2026-013' for e in events if e[0]=='tool_start'))
            self.assertIn('材料范围与版本',result['report'])
            self.assertEqual(result['execution_contract']['read_schedule'],'sequential_same_snapshot')
            self.assertIn('2026-10-24',result['report']);self.assertIn('HNSW',result['report'])
            self.assertIn('| 向局提交期限 | 实际交局日期 | 交局核实 |',result['report'])
            self.assertIn('| 未核实 | 未核实 |',result['report'])
            self.assertNotIn('## 待人工核对',result['report'])
            self.assertNotIn('尚未交局',result['report'])
            self.assertNotIn('未查业务数据库',result['report'])
            self.assertEqual({e['type'] for e in entries},{'database','knowledge'})
        finally:
            context.clear_tool_budgets('case-brief-test');context.reset_session()

    def test_route_contract_repair_is_bounded_and_keeps_case_anchor(self):
        import asyncio
        from unittest.mock import AsyncMock,MagicMock
        from casework.workflow import resolve,DepartmentIntentRoute,DepartmentIntent
        llm=MagicMock();model=MagicMock();llm.with_structured_output.return_value=model
        parsed=DepartmentIntentRoute(request=DepartmentIntent(query='DEMO-2026-013',requirements=[{'kind':'case_owner','quote':'案件信息'},{'kind':'case_technology','quote':'技术原文'}]))
        model.ainvoke=AsyncMock(side_effect=[{'parsed':None,'parsing_error':ValueError('invalid')},{'parsed':parsed}])
        with patch('agent.llm.get_llm',return_value=llm):result=asyncio.run(resolve('核对DEMO-2026-013的案件信息和技术原文'))
        self.assertEqual(result.query,'DEMO-2026-013');self.assertEqual(model.ainvoke.await_count,2)
        model.ainvoke=AsyncMock(return_value={'parsed':None,'parsing_error':ValueError('invalid')})
        with patch('agent.llm.get_llm',return_value=llm),self.assertRaises(ValueError):asyncio.run(resolve('核对DEMO-2026-013'))
        self.assertEqual(model.ainvoke.await_count,2)
        model.ainvoke=AsyncMock(return_value={'parsed':DepartmentIntentRoute(request=DepartmentIntent(requirements=[{'kind':'other','quote':'复杂比较'}]))})
        with patch('agent.llm.get_llm',return_value=llm):self.assertIsNone(asyncio.run(resolve('复杂比较')))
        self.assertEqual(model.ainvoke.await_count,1)

    def test_context_read_budget_is_shared_and_returns_control_for_synthesis(self):
        from harness.middleware import context_tools
        from api import context
        from tools.reflect_check import hard_check
        try:
            context.set_session('read-budget',str(self.base));context.set_round(1);context.reset_tool_budget()
            main_tools=context_tools('read-budget');child_tools=context_tools('read-budget')
            with patch('api.storage.load_context_artifact',return_value='足够读取的原文'*100),patch('api.storage.load_evidence',return_value=[{'id':'S1','snippet':'足够读取的原文'*100}]):
                for offset in range(12):
                    main_tools[0].invoke({'ref':'a'*64,'offset':offset,'limit':1})
                    child_tools[1].invoke({'evidence_id':'S1','offset':offset,'limit':1})
                result=json.loads(main_tools[0].invoke({'ref':'a'*64,'offset':12,'limit':1}))
                self.assertEqual(result['status'],'budget_exhausted')
                context.set_round(2);context.reset_tool_budget();main_tools[0].invoke({'ref':'a'*64})
            issues=hard_check({'report':'未完成','require_report':False,'execution_issue':'回读预算耗尽'})
            self.assertTrue(any('执行尚未完成' in i for i in issues))
        finally:context.clear_tool_budgets('read-budget');context.reset_session()

if __name__=='__main__':unittest.main()


class MonthlyAggregateContractTests(unittest.TestCase):
    """Range totals and month-over-month percentages come from the program."""

    setUp = DepartmentCaseworkTests.setUp
    tearDown = DepartmentCaseworkTests.tearDown

    def test_range_total_and_percentage_are_source_owned(self):
        import asyncio
        from unittest.mock import patch
        from tools.casework_tools import query_department_cases
        from tools.statistics_check import check_statistics
        entries=[]
        def add(thread,item):
            entry={**item,'id':'S'+str(len(entries)+1)};entries.append(entry);return entry['id']
        with patch('api.evidence.add',side_effect=add):
            result=json.loads(query_department_cases.invoke(
                {'operation':'monthly','start_month':'2026-09','end_month':'2026-10','compare_months':True}))
        report=result['answer_markdown']
        self.assertIn('区间合计',report)
        range_totals=[e for e in entries if (e.get('statistics') or {}).get('metrics')
                      and e['statistics']['metrics'][0]['column'].endswith('_total_count')]
        self.assertEqual({e['statistics']['metrics'][0]['column'] for e in range_totals},
                         {'received_total_count','filed_total_count'})
        for entry in range_totals:
            metric=entry['statistics']['metrics'][0]
            self.assertEqual(metric['unit'],'件')
            self.assertEqual(metric['time']['status'],'bounded')
        comparison=result['comparisons'][0]
        for key,column in (('received_change_pct','received_change_pct'),
                           ('filed_change_pct','filed_change_pct')):
            if comparison[key] is None:
                self.assertIn('不计算百分比',report)
                continue
            pct=[e for e in entries if e['id']==comparison['pct_evidence_id']][0]
            metrics={m['column']:m for m in pct['statistics']['metrics']}
            self.assertIn(column,metrics)
            self.assertEqual(metrics[column]['unit'],'%')
            self.assertEqual(metrics[column]['time']['field'],'month_comparison')
        checked=asyncio.run(check_statistics(report,entries))
        self.assertIn(checked['status'],('passed',),checked)


class CaseCountContractTests(unittest.TestCase):
    """The filtered total and the in-progress count are separate metrics."""

    setUp = DepartmentCaseworkTests.setUp
    tearDown = DepartmentCaseworkTests.tearDown

    def test_count_evidence_carries_filtered_and_active_totals(self):
        import asyncio,json
        from unittest.mock import patch
        from tools.casework_tools import query_department_cases
        from tools.statistics_check import check_statistics
        entries=[]
        def add(thread,item):
            entry={**item,'id':'S'+str(len(entries)+1)};entries.append(entry);return entry['id']
        from api import context, source_policy
        self.addCleanup(context.reset_session)
        context.set_session('count-contract',str(self.base)); source_policy.configure(['db_query'],[])
        with patch('api.evidence.add',side_effect=add):
            result=json.loads(query_department_cases.invoke(
                {'operation':'cases','client':'客户甲（模拟）','include_count':True}))
        count=[e for e in entries if e['id']==result['count_evidence_id']][0]
        metrics={m['column']:m for m in count['statistics']['metrics']}
        self.assertEqual(set(metrics),{'matched_case_count','active_count'})
        self.assertEqual(count['rows'][0][0],result['total'])
        self.assertLessEqual(count['rows'][0][1],result['total'])
        table=result['count_answer_markdown']
        self.assertIn('筛选案件数量（件）',table);self.assertIn('在办案件数量（件）',table)
        answer=table
        self.assertEqual(asyncio.run(check_statistics(answer,entries))['status'],'passed')
