"""Explicit numeric edges. These tiny records are not business corpus cases."""
import asyncio,os,tempfile,unittest
from pathlib import Path
from datetime import date
from unittest.mock import patch,AsyncMock
from casework import analytics,store,sync
from casework.search import search
class NumericBoundaryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.env=patch.dict(os.environ,{'CASEWORK_DB_PATH':str(Path(self.tmp.name)/'case.db'),'CASEWORK_EMBEDDING':'0','CASEWORK_MYSQL_MIRROR':'0'})
  self.env.start();self.addCleanup(self.env.stop)
 def case(self,n,**kw):return {'case_id':str(n),'case_key':'u:'+str(n),'stage':'撰写中','filing_verified':False,'received_date':None,'actual_filed_date':None,'conflict_fields':[],**kw}
 def report(self,rows,start='2026-09',end='2026-09'):
  with patch.object(store,'list_cases',return_value=rows),patch.object(store,'coverage',return_value={}),patch.object(analytics,'today',return_value=date(2026,11,3)):
   return analytics.report(start,end)
 def test_DATE_01(self):
  r=self.report([self.case('before',received_date='2026-08-31'),self.case('start',received_date='2026-09-01'),self.case('last',received_date='2026-09-30'),self.case('end',received_date='2026-10-01')])
  self.assertEqual([c['case_id'] for c in r['cases']],['start','last']);self.assertEqual(r['totals'][0]['received_count'],2)
 def test_DATE_02(self):self.assertEqual(tuple(map(str,analytics.month_bounds('2026-12'))),('2026-12-01','2027-01-01'))
 def test_DATE_03(self):
  self.assertEqual(tuple(map(str,analytics.month_bounds('2024-02'))),('2024-02-01','2024-03-01'))
  self.assertEqual(self.report([self.case(1,received_date='2024-02-29'),self.case(2,received_date='2024-03-01')],'2024-02','2024-02')['totals'][0]['received_count'],1)
 def test_DATE_04(self):
  for month in ['2026-00','2026-13','2026-1','26-01','']:
   with self.subTest(month=month),self.assertRaises(ValueError):analytics.month_bounds(month)
 def test_DATE_05(self):
  with self.assertRaises(ValueError):self.report([],'2026-10','2026-09')
 def test_DATE_06(self):
  for end,n in [('2024-01',1),('2026-12',36)]:self.assertEqual(len(self.report([],'2024-01',end)['totals']),n)
  with self.assertRaises(ValueError):self.report([],'2024-01','2027-01')
 def test_DATE_07(self):
  r=self.report([self.case('null')]);self.assertEqual(r['totals'][0]['received_count'],0);self.assertEqual([c['case_id'] for c in r['unknown_received']],['null'])
 def test_DATE_08(self):
  r=self.report([self.case('planned',filing_due_date='2026-09-10',draft_due_date='2026-09-01',stage='已交局')]);self.assertEqual(r['totals'][0]['filed_count'],0);self.assertEqual([c['case_id'] for c in r['unknown_filed']],['planned'])
 def test_DATE_09(self):
  r=self.report([self.case('future',actual_filed_date='2026-11-04',filing_verified=True)],'2026-11','2026-11');self.assertEqual(r['totals'][0]['filed_count'],0);self.assertEqual([c['case_id'] for c in r['future_filed']],['future'])
  self.assertFalse(analytics.is_completed(self.case(1,actual_filed_date='2026-11-04',filing_verified=True),date(2026,11,3)))
 def test_DATE_10(self):
  r=self.report([self.case('unverified',actual_filed_date='2026-09-01')]);self.assertEqual(r['totals'][0]['filed_count'],0);self.assertEqual(len(r['unknown_filed']),1)
 def due(self,field,status):
  rows=[self.case('yesterday',**{field:'2026-11-02'}),self.case('today',**{field:'2026-11-03'}),self.case('day13',**{field:'2026-11-16'}),self.case('day14',**{field:'2026-11-17'}),self.case('day15',**{field:'2026-11-18'}),self.case('null')]
  with patch.object(store,'list_cases',return_value=rows),patch.object(store,'coverage',return_value={}):return {c['case_id'] for c in analytics.cases_page(due_field=field,due_status=status,as_of='2026-11-03')['rows']}
 def test_DUE_01(self):self.assertEqual(self.due('draft_due_date','overdue'),{'yesterday'})
 def test_DUE_02(self):self.assertEqual(self.due('draft_due_date','next14'),{'today','day13'})
 def test_DUE_03(self):
  for f in ('draft_due_date','client_review_due_date','filing_due_date'):
   with self.subTest(field=f):self.assertEqual(self.due(f,'next14'),{'today','day13'})
 def test_DUE_04(self):self.assertEqual(self.due('draft_due_date','unknown'),{'null'})
 def test_COUNT_01(self):
  r=self.report([self.case('dup',received_date='2026-09-01'),self.case('dup',received_date='2026-09-01'),self.case('unique',received_date='2026-09-01')]);self.assertEqual(r['totals'][0]['received_count'],1);self.assertEqual(r['duplicate_case_ids_excluded'],['dup'])
 def test_COUNT_02(self):
  for stage,want in [('撰写中',True),('已完成',False),('已交局',False),('暂停',False),('已撤回',False),(None,False),('未知状态',False)]:
   with self.subTest(stage=stage):self.assertEqual(analytics.active(self.case(1,stage=stage),date(2026,11,3)),want)
 def test_PAGE_01(self):
  for limit in [1,200]:self.assertEqual(analytics.cases_page(limit=limit)['limit'],limit)
  for limit in [0,201]:
   with self.subTest(limit=limit),self.assertRaises(ValueError):analytics.cases_page(limit=limit)
 def test_PAGE_02(self):
  self.assertEqual(analytics.cases_page(offset=0)['offset'],0)
  with self.assertRaises(ValueError):analytics.cases_page(offset=-1)
 def test_SEARCH_01(self):
  for limit in [1,30]:self.assertEqual(search('机制',limit=limit,allow_vector=False)['results'],[])
  for limit in [0,31]:
   with self.subTest(limit=limit),self.assertRaises(ValueError):search('机制',limit=limit)
 def test_SEARCH_02(self):
  for query in ['', '  ', '\n']:
   with self.subTest(query=query),self.assertRaises(ValueError):search(query)
 def test_SEARCH_03(self):
  for scope in ['mention','topic','mechanism','ambiguous']:self.assertEqual(search('机制',query_scope=scope,allow_vector=False)['query_scope'],scope)
  with self.assertRaises(ValueError):search('机制',query_scope='invalid')
 def test_FILE_02(self):
  folder=Path(self.tmp.name);root={'path':str(folder)}
  with patch.object(store,'approved_path',return_value=folder):
   for size,valid in [(20*1024*1024-1,True),(20*1024*1024,True),(20*1024*1024+1,False)]:
    path=folder/'edge.txt'
    with path.open('wb') as f:f.truncate(size)
    if valid:self.assertEqual(len(sync.read_source(root,'edge.txt')[0]),size)
    else:
     with self.assertRaises(ValueError):sync.read_source(root,'edge.txt')
 def test_MODEL_01(self):
  from api import storage,server,evidence
  from openai import APIStatusError
  import httpx
  with patch.object(storage,'_DB_PATH',Path(self.tmp.name)/'app.db'):
   storage.init_db()
   errors=[TimeoutError('deadline'),APIStatusError('Insufficient Balance',response=httpx.Response(402,request=httpx.Request('POST','https://example.invalid')),body={'error':'balance'})]
   for n,exc in enumerate(errors):
    thread='boundary-error-'+str(n);storage.create_run(thread,'测试失败保留','测试失败保留',False)
    with patch.object(server,'run_deep_agent',AsyncMock(side_effect=exc)),patch('api.execution_files.write_manifest'):
     asyncio.run(server._execute_task(thread,'测试失败保留'))
    run=storage.get_run(thread);self.assertEqual(run['status'],'failed');self.assertFalse(any(e['event']=='task_result' for e in run['events']));self.assertFalse(any(e['event']=='quality_summary' and e['data'].get('passed') for e in run['events']))
