"""Drawing retrieval distinguishes shape text from filename association."""
import asyncio
import copy
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from casework import catalog, parser, store, sync
from casework.search import search


def vsdx(text=''):
    output=io.BytesIO()
    ns='http://schemas.microsoft.com/office/visio/2012/main'
    with zipfile.ZipFile(output,'w') as package:
        package.writestr('visio/document.xml',f'<VisioDocument xmlns="{ns}"/>')
        package.writestr('visio/pages/pages.xml',f'''<Pages xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><Page ID="7" Name="检索流程"><Rel r:id="r9"/></Page><Page ID="2" Name="第二页"><Rel r:id="r2"/></Page></Pages>''')
        package.writestr('visio/pages/_rels/pages.xml.rels','''<Relationships><Relationship Id="r9" Target="custom.xml"/><Relationship Id="r2" Target="page1.xml"/><Relationship Id="external" Target="https://invalid.example/page.xml" TargetMode="External"/></Relationships>''')
        package.writestr('visio/pages/custom.xml',f'<PageContents xmlns="{ns}"><Shapes><Shape ID="10"><Shapes><Shape ID="42"><Text>{text}</Text></Shape></Shapes></Shape></Shapes></PageContents>')
        package.writestr('visio/pages/page1.xml',f'<PageContents xmlns="{ns}"><Shapes/></PageContents>')
    return output.getvalue()


class VisioRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.source=self.base/'drive';self.source.mkdir()
        self.env=patch.dict(os.environ,{'CASEWORK_DB_PATH':str(self.base/'cases.db'),'CASEWORK_ALLOWED_ROOTS':json.dumps([str(self.source)]),'CASEWORK_EMBEDDING':'0','CASEWORK_MYSQL_MIRROR':'0','DATA_MODE':'demo'})
        self.env.start()
        (self.source/'.casework-demo.json').write_text(json.dumps({'kind':'independently_authored_synthetic'}))
        self.draw=self.source/'DEMO-2026-001.vsdx'
        self.draw.write_bytes(vsdx('采用HNSW<cp IX="0"/>向量近邻检索。\n代理师：错误人员\n实际交局日期：2020-01-01'))
        (self.source/'DEMO-2026-001.申请文件.md').write_text('代理师：真实人员\n利用HNSW实现向量近邻检索。')
        (self.source/'DEMO-2026-001.vsd').write_bytes(b'legacy drawing - no parsed text')
        (self.source/'DEMO-2026-002.vsd').write_bytes(b'legacy only')
        (self.source/'DEMO-2026-003.vsdx').write_bytes(vsdx())
        (self.source/'DEMO-2026-003.申请文件.md').write_text('采用HNSW近邻索引进行向量检索。')
        self.original=self.draw.read_bytes()
        self.root=store.save_root('模拟绘图',str(self.source),'synthetic')
        self.assertEqual(sync.run(self.root['id'])['state'],'ready')
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def case(self,ident):return store.get_case(self.root['id']+':'+ident)

    def test_shape_text_with_actual_page_relationship_and_no_ledger_leak(self):
        hit=search('向量近邻检索',kind='drawing')['results'][0]
        self.assertEqual(hit['case_id'],'DEMO-2026-001')
        proof=hit['sources'][0]
        self.assertIn('采用HNSW向量近邻检索',proof['excerpt'])
        for locator in ('检索流程','1','形状 42'):self.assertIn(locator,proof['location'])
        self.assertEqual(self.case(hit['case_id'])['owner_name'],'真实人员')
        self.assertIsNone(self.case(hit['case_id'])['actual_filed_date'])
        self.assertEqual(self.draw.read_bytes(),self.original)
        self.assertEqual(parser.classify('DEMO-2026-001.vsdx'),'drawing')

    def test_related_drawings_include_empty_and_legacy_but_not_semantic_evidence(self):
        hits={r['case_id']:r for r in search('向量近邻检索')['results']}
        self.assertEqual(set(hits),{'DEMO-2026-001','DEMO-2026-003'})
        self.assertEqual(len(hits['DEMO-2026-001']['related_drawings']),2)
        empty=hits['DEMO-2026-003']['related_drawings'][0]
        self.assertFalse(empty['text_indexed'])
        self.assertTrue(all(p['kind']!='drawing' for p in hits['DEMO-2026-003']['sources']))
        for ident in ('DEMO-2026-002','DEMO-2026-003'):
            row=search(ident,kind='drawing')['results'][0]
            self.assertEqual(row['match_basis'],'case_id_attachment');self.assertEqual(row['sources'],[])
        self.assertEqual(self.case('DEMO-2026-002')['document_count'],1)
        self.assertEqual(self.case('DEMO-2026-002')['tags'],[])
        self.assertEqual(search('无关的精密齿轮传动',kind='drawing')['results'],[])

    def test_inventory_api_version_and_download_hash_checks(self):
        from api.casework import router
        app=FastAPI();app.include_router(router)
        drawing=search('DEMO-2026-002',kind='drawing')['results'][0]['related_drawings'][0]
        with TestClient(app) as client:
            url='/api/casework/documents/'+drawing['file_id']
            data=client.get(url).json()
            self.assertEqual(data['version_status'],'inventory');self.assertEqual(data['total_chunks'],0)
            self.assertTrue(data['download_available']);self.assertEqual(data['source_status'],'verified')
            self.assertEqual(client.get(url+'/download',params={'revision':drawing['revision']}).content,b'legacy only')
            self.assertEqual(client.get(url,params={'revision':'wrong'}).status_code,404)
            (self.source/drawing['source']).write_bytes(b'changed')
            self.assertFalse(client.get(url).json()['download_available'])
            self.assertEqual(client.get(url+'/download',params={'revision':drawing['revision']}).status_code,409)
        with self.assertRaises(KeyError):catalog.document(drawing['file_id'],parser_key='unknown')

    def test_ambiguous_path_and_bad_package_are_not_associated(self):
        (self.source/'DEMO-2026-001.DEMO-2026-002.vsd').write_bytes(b'ambiguous')
        (self.source/'DEMO-2026-004.vsdx').write_bytes(b'not a zip')
        result=sync.run(self.root['id'])
        self.assertEqual(result['summary']['failed'],2)
        self.assertIsNone(self.case('DEMO-2026-004'))
        self.assertTrue(all('DEMO-2026-001.DEMO-2026-002' not in d['source'] for r in search('DEMO-2026-001')['results'] for d in r['related_drawings']))

    def test_drawing_text_alone_does_not_establish_experience_or_meetings(self):
        payload=parser.parse(self.original,'DEMO-2026-004.三方会议.vsdx')
        self.assertEqual(payload['meetings'],[])
        self.assertEqual([p['field'] for p in payload['claims']],['case_id'])
        (self.source/'DEMO-2026-004.vsdx').write_bytes(self.original)
        sync.run(self.root['id'])
        self.assertEqual(self.case('DEMO-2026-004')['tags'],[])

    def test_tool_render_contract_and_attachment_evidence_bindings(self):
        from tools.casework_tools import search_case_archive
        from casework.workflow import DepartmentRequest,render
        from casework.verification import verify,delivery,task_contract_issues
        from casework.routing import select_route
        entries=[]
        def add(thread,item):
            sid='S'+str(len(entries)+1);entries.append({**item,'id':sid});return sid
        with patch('tools.casework_tools._guard',return_value=None),patch('api.evidence.add',side_effect=add):
            payload=json.loads(search_case_archive.invoke({'query':'DEMO-2026-002','kind':'drawing'}))
        request=DepartmentRequest(operation='archive',query='DEMO-2026-002',archive_kind='drawing')
        report=render(payload,request)
        self.assertIn('DEMO-2026-002.vsd',report)
        self.assertEqual(verify(request,payload,entries,report)['status'],'passed')
        altered=copy.deepcopy(entries);altered[-1]['file_id']='wrong'
        self.assertEqual(verify(request,payload,altered,report)['status'],'failed')
        query='找一下DEMO-2026-002的Visio绘图附件'
        self.assertEqual(task_contract_issues(query,request,delivery(request,payload)),[])
        route=asyncio.run(select_route(query,allow_semantic=False))
        self.assertEqual(route.status,'matched');self.assertEqual(route.request.archive_kind,'drawing')


class VisioPackageTests(unittest.TestCase):
    def test_bad_relationship_and_dtd_are_rejected(self):
        from tools.visio_parser import shape_texts
        for replacement in ('<Pages><Page/></Pages>','<!DOCTYPE Pages [<!ENTITY x "expand">]><Pages/>'):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'drawing.vsdx'
                with zipfile.ZipFile(io.BytesIO(vsdx())) as source,zipfile.ZipFile(path,'w') as target:
                    for name in source.namelist():target.writestr(name,replacement if name=='visio/pages/pages.xml' else source.read(name))
                with self.assertRaises(ValueError):shape_texts(path)
