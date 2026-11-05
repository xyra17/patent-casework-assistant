"""Regression checks for the October 25 audit; all files and application state are disposable."""
import asyncio
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from api import server, storage, context, execution_files
from api.attachments import save_uploads
from api.monitor import ConnectionManager
from utils import path_utils
from tools.attachment_tables import query_table, read_text
from tools.upload_file_read_tool import read_file_content
from agent.main_agent import _prepare_session


class AuditFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='audit-fix-test-')
        self.root = Path(self.temp.name)
        self.patches = [patch.object(storage, '_DB_PATH', self.root/'app.db'),
                        patch.object(path_utils, 'UPLOAD_DIR', self.root/'upload'),
                        patch.object(path_utils, 'OUTPUT_DIR', self.root/'output')]
        for item in self.patches: item.start()
        storage.init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        context.reset_session()
        self.client.close()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def upload(self, name='sample.txt', data=b'original', **form):
        return self.client.post('/api/upload', data={'thread_id':'audit', **form}, files={'files':(name,data)})

    def test_upload_conflict_explicit_replace_limits_and_batch_validation(self):
        self.assertEqual(self.upload().status_code,200)
        conflict=self.upload(data=b'replacement')
        self.assertEqual(conflict.status_code,409)
        self.assertEqual(conflict.json()['detail']['files'],['sample.txt'])
        self.assertEqual((path_utils.get_upload_dir('audit')/'sample.txt').read_bytes(),b'original')
        self.assertEqual(self.upload(data=b'replacement',replace=json.dumps(['sample.txt'])).status_code,200)
        for name,data in [('empty.txt',b''),('bad.exe',b'data')]: self.assertEqual(self.upload(name,data).status_code,400)
        with patch('api.attachments.MAX_BYTES',2): self.assertEqual(self.upload('large.txt',b'123').status_code,413)
        self.assertFalse((path_utils.get_upload_dir('audit')/'large.txt').exists())
        response=self.client.post('/api/upload',data={'thread_id':'audit'},files=[('files',('same.txt',b'1')),('files',('same.txt',b'2'))])
        self.assertEqual(response.status_code,400)

    def test_interrupted_batch_never_truncates_original_or_publishes_other_staged_file(self):
        self.upload()
        class Broken:
            filename='sample.txt'
            count=0
            async def read(self,n):
                self.count+=1
                if self.count==1:return b'partial'
                raise OSError('interrupted')
        async def run():
            await save_uploads(path_utils.get_upload_dir('audit'),[UploadFile(filename='new.txt',file=io.BytesIO(b'new')),Broken()], '["sample.txt"]')
        with self.assertRaises(OSError): asyncio.run(run())
        directory=path_utils.get_upload_dir('audit')
        self.assertEqual((directory/'sample.txt').read_bytes(),b'original')
        self.assertFalse((directory/'new.txt').exists())
        self.assertEqual(sorted(p.name for p in directory.iterdir()),['sample.txt'])

    def test_tables_read_later_rows_other_sheets_filters_aggregates_and_encoding(self):
        path=self.root/'sample.xlsx'
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame({'region':['A']*9+['B'],'amount':range(1,11)}).to_excel(writer,sheet_name='first',index=False)
            pd.DataFrame({'marker':['second-sheet']}).to_excel(writer,sheet_name='second',index=False)
        self.assertEqual(query_table(path,list_sheets=True)['sheets'],['first','second'])
        second=query_table(path,sheet='second');self.assertEqual(second['rows'],[['second-sheet']])
        later=query_table(path,offset=9,limit=1);self.assertEqual(later['rows'],[['B',10]]);self.assertEqual(later['row_numbers'],[11])
        filtered=query_table(path,filters=[{'field':'amount','op':'gt','value':8}],aggregates=[{'field':'amount','op':'sum','alias':'total'}])
        self.assertEqual(filtered['rows'],[['19']])
        grouped=query_table(path,group_by=['region'],aggregates=[{'field':'amount','op':'sum'}]);self.assertEqual(grouped['rows'],[['A','45'],['B','10']])
        self.assertTrue(query_table(path,limit=5)['truncated'])
        csv=self.root/'chinese.csv';csv.write_bytes('地区,金额\n华东,12\n'.encode('gb18030'))
        self.assertEqual(query_table(csv)['rows'],[['华东','12']])
        context.set_session('audit',str(self.root))
        self.assertIn('华东',read_file_content.invoke({'filename':'chinese.csv'}))
        with patch('tools.attachment_tables.MAX_ROWS',1):
            with self.assertRaisesRegex(ValueError,'未返回不完整统计'): query_table(path)
        with self.assertRaises(ValueError): query_table(path,sheet='missing')
        with self.assertRaises(ValueError): query_table(path,filters=[{'field':'missing','value':1}])

    def test_csv_preserves_identifiers_and_exact_decimal_totals(self):
        path=self.root/'precision.csv'
        path.write_text('id,amount\n0001,123456789012345678.12\n9007199254740993,0.01\n',encoding='utf-8')
        self.assertEqual(query_table(path)['rows'][0][0],'0001')
        self.assertEqual(query_table(path)['rows'][1][0],'9007199254740993')
        result=query_table(path,aggregates=[{'field':'amount','op':'sum'}])
        self.assertEqual(result['rows'],[['123456789012345678.13']])
        result=query_table(path,filters=[{'field':'amount','op':'gte','value':1}])
        self.assertEqual(result['matched_rows'],1)

    def test_history_page_is_lightweight_counts_all_statuses_and_preserves_older_access(self):
        for n in range(53):
            tid=f'history-{n:03d}';storage.create_run(tid,'question','question',False)
            storage.append_event(tid,{'event':'task_result','data':{'result':'report'*1000,'degraded':n==1,'evidence':[{'snippet':'proof'}]}})
        storage.create_run('cancelled','q','q',False)
        storage.append_event('cancelled',{'event':'task_cancelled','data':{'stop_reason':'user_cancel'}})
        page=self.client.get('/api/runs?limit=50&tracked=history-000').json()
        self.assertEqual(page['total'],54);self.assertEqual(page['stats']['cancelled'],1);self.assertEqual(page['stats']['degraded'],1)
        self.assertEqual(page['tracked']['history-000'],'completed')
        self.assertNotIn('result',page['runs'][0]);self.assertNotIn('evidence',page['runs'][0])
        older=self.client.get('/api/runs',params={'offset':page['next_offset']}).json()
        self.assertEqual(len(older['runs']),4)
        self.assertEqual(len({r['thread_id'] for r in page['runs']+older['runs']}),54)
        self.assertEqual(self.client.get('/api/runs?offset=-1').status_code,422)

    def test_history_cursor_survives_new_sessions_deletion_and_reruns(self):
        for n in range(6): storage.create_run(f'cursor-{n}', 'q','q',False)
        first=self.client.get('/api/runs?limit=3').json()
        storage.create_run('new','q','q',False)
        storage.delete_run('cursor-5')
        storage.create_run('cursor-4','q','q',False)
        second=self.client.get('/api/runs',params={'limit':3,'before':first['next_cursor']}).json()
        self.assertEqual([r['thread_id'] for r in second['runs']],['cursor-2','cursor-1','cursor-0'])

    def test_cancel_persists_reason_and_cannot_override_completion(self):
        storage.create_run('cancelled','q','q',False)
        storage.append_event('cancelled',{'event':'task_cancelled','message':'任务已终止','data':{'stop_reason':'user_cancel'}})
        storage.append_event('cancelled',{'event':'task_result','data':{'result':'late'}})
        self.assertEqual(storage.get_run('cancelled')['status'],'cancelled')
        self.assertEqual(storage.get_run('cancelled')['stop_reason'],'user_cancel')
        storage.create_run('finished','q','q',False)
        storage.append_event('finished',{'event':'task_result','data':{'result':'done'}})
        storage.append_event('finished',{'event':'task_cancelled'})
        self.assertEqual(storage.get_run('finished')['status'],'completed')

    def test_cancel_before_coroutine_starts_and_completion_race(self):
        async def run():
            storage.create_run('early','q','q',False)
            task=asyncio.create_task(asyncio.sleep(100))
            server._tasks['early']=task
            self.assertEqual((await server.cancel_task('early'))['status'],'cancelled')
            self.assertEqual(storage.get_run('early')['status'],'cancelled')
            storage.create_run('race','q','q',False)
            async def complete():
                storage.append_event('race',{'event':'task_result','data':{'result':'done'}})
            task=asyncio.create_task(complete());await task
            server._tasks['race']=task
            self.assertEqual((await server.cancel_task('race'))['status'],'completed')
        asyncio.run(run())

    def test_cancel_response_cannot_cancel_a_new_execution_started_during_cleanup(self):
        async def run():
            storage.create_run('cancel-restart','q','q',False)
            old=asyncio.create_task(asyncio.sleep(100))
            server._tasks['cancel-restart']=old
            new=[]
            def restart(_):
                storage.create_run('cancel-restart','q','q',False)
                new.append(asyncio.create_task(asyncio.sleep(100)))
                server._tasks['cancel-restart']=new[0]
            old.add_done_callback(restart)
            try:
                response=await server.cancel_task('cancel-restart')
                self.assertEqual(response['status'],'running')
                self.assertEqual(storage.get_run('cancel-restart')['status'],'running')
                self.assertEqual(storage.list_executions('cancel-restart')['executions'][0]['status'],'cancelled')
                self.assertIs(server._tasks['cancel-restart'],new[0])
            finally:
                for task in new: task.cancel()
                await asyncio.gather(*new,return_exceptions=True)
                server._tasks.pop('cancel-restart',None)
        asyncio.run(run())

    def test_export_reserves_session_against_rerun_and_deletion(self):
        storage.create_run('export','q','q',False)
        storage.append_event('export',{'event':'task_result','data':{'result':'done'}})
        entered,release=threading.Event(),threading.Event()
        def export(run,target):
            entered.set()
            if not release.wait(5): raise TimeoutError('release')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(b'%PDF-test')
        async def run():
            with patch('tools.chart_report.build_chart_report',side_effect=export):
                task=asyncio.create_task(server.chart_report('export'))
                try:
                    await asyncio.to_thread(entered.wait,2)
                    with self.assertRaises(server.HTTPException) as blocked: await server.delete_run('export')
                    self.assertEqual(blocked.exception.status_code,409)
                    with self.assertRaises(server.HTTPException): await server.submit_task(server.TaskRequest(thread_id='export',query='rerun'))
                finally:
                    release.set()
                    await task
                self.assertNotIn('export',server._starting)
        asyncio.run(run())

    def test_legacy_execution_keeps_existing_report_accessible_after_first_rerun(self):
        storage.create_run('legacy','q','q',False)
        root=path_utils.get_session_root('legacy');root.mkdir(parents=True)
        (root/'old.md').write_text('old report')
        storage.append_event('legacy',{'event':'task_result','data':{'result':'old report'}})
        with storage._connect() as conn: conn.execute("UPDATE runs SET execution_id=NULL WHERE thread_id='legacy'")
        info=storage.create_run('legacy','q','q',False)
        activate=execution_files.activate('legacy',info['execution_id'])
        (activate/'new.md').write_text('new report')
        history=storage.list_executions('legacy')['executions'][0]
        manifest=execution_files.manifest('legacy',history['execution_id'])
        self.assertTrue(manifest['legacy'])
        self.assertEqual([item['name'] for item in manifest['artifacts']],['old.md'])

    def test_execution_files_and_snapshots_are_isolated_and_deletion_cleans_every_copy(self):
        self.upload('report.md',b'input, not report')
        first=storage.create_run('audit','q','q',False)
        directory=execution_files.activate('audit',first['execution_id']);_prepare_session('audit',directory)
        self.assertFalse((directory/'report.md').exists())
        (directory/'report.md').write_text('old report')
        storage.append_event('audit',{'event':'task_result','data':{'result':'old report','evidence':[{'id':'S1','snippet':'old proof'}]}})
        execution_files.write_manifest(directory,'completed')
        second=storage.create_run('audit','q','q',False)
        new=execution_files.activate('audit',second['execution_id']);_prepare_session('audit',new)
        self.assertFalse((new/'report.md').exists())
        self.assertEqual((new/'attachments'/'report.md').read_bytes(),b'input, not report')
        history=self.client.get('/api/runs/audit/executions').json()
        self.assertEqual(history['executions'][0]['execution_id'],first['execution_id'])
        detail=self.client.get(f"/api/runs/audit/executions/{first['execution_id']}").json()
        self.assertEqual(detail['result'],'old report');self.assertEqual(detail['manifest']['artifacts'][0]['name'],'report.md')
        self.assertEqual(self.client.get('/api/download',params={'thread_id':'audit','path':str(directory/'report.md')}).text,'old report')
        self.assertEqual(self.client.get('/api/download',params={'thread_id':'other','path':str(directory/'report.md')}).status_code,403)
        self.assertEqual(self.client.delete('/api/attachments',params={'thread_id':'audit','filename':'report.md'}).status_code,200)
        self.assertFalse((directory/'attachments'/'report.md').exists());self.assertFalse((new/'attachments'/'report.md').exists())
        self.assertEqual((directory/'report.md').read_text(),'old report')
        self.assertTrue(execution_files.manifest('audit',first['execution_id'])['inputs'][0]['deleted'])
        storage.append_event('audit',{'event':'task_result','data':{'result':'new result'}})
        self.assertEqual(self.client.delete('/api/runs/audit').status_code,200)
        self.assertTrue(directory.exists())
        self.assertEqual(self.client.post('/api/trash/audit/restore').status_code,200)
        self.assertEqual(self.client.get(f"/api/runs/audit/executions/{first['execution_id']}").json()['result'],'old report')
        self.assertEqual(self.client.delete('/api/runs/audit').status_code,200)
        self.assertEqual(self.client.delete('/api/trash/audit').status_code,200)
        self.assertFalse(path_utils.get_session_root('audit').exists())
        self.assertIsNone(storage.get_execution('audit',first['execution_id']))


class SenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_client_cannot_delay_producer_or_healthy_client_and_replay_is_durable(self):
        class WS:
            def __init__(self,slow=False): self.slow=slow;self.messages=[];self.closed=False
            async def accept(self): pass
            async def close(self,code): self.closed=True
            async def send_text(self,text):
                if self.slow: await asyncio.Event().wait()
                self.messages.append(text)
        manager=ConnectionManager(queue_size=2,send_timeout=.03)
        slow,fast=WS(True),WS()
        await manager.connect('sender-audit',slow);await manager.connect('sender-audit',fast)
        with patch.object(storage,'append_event') as persist:
            await asyncio.wait_for(manager.send_event('sender-audit','tool_end','one'),.01)
            await asyncio.sleep(.06)
            self.assertEqual(len(fast.messages),1);self.assertTrue(slow.closed);persist.assert_called_once()
        await manager.shutdown()

    async def test_queue_overflow_closes_connection_for_rest_recovery(self):
        class WS:
            async def accept(self): pass
            async def close(self,code): self.code=code
            async def send_text(self,text): await asyncio.Event().wait()
        manager=ConnectionManager(queue_size=1,send_timeout=.03);ws=WS()
        await manager.connect('overflow',ws)
        with patch.object(storage,'append_event'):
            await manager.send_event('overflow','tool_end','1')
            await manager.send_event('overflow','tool_end','2')
        await asyncio.sleep(.01)
        self.assertEqual(ws.code,1013);self.assertNotIn('overflow',manager._connections)
        await manager.shutdown()
