"""Department workflows. Management actions never become agent write tools."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import mimetypes
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter,BackgroundTasks,HTTPException,Query
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field

from casework import analytics,catalog,parser,store,sync
from casework.domains import options as domain_options
from casework.mirror import sync_mirror
from casework.privacy import local_endpoint,protected

router=APIRouter(prefix='/api/casework',tags=['department-casework'])


@contextmanager
def errors():
    try:yield
    except HTTPException:raise
    except KeyError as exc:raise HTTPException(404,str(exc).strip("'")) from exc
    except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
    except sqlite3.IntegrityError as exc:raise HTTPException(409,'记录重复或关联约束冲突，请刷新后核对') from exc
    except ValueError as exc:
        status=409 if any(word in str(exc) for word in ('变化','刷新','同步中','扫描','配置已')) else 400
        raise HTTPException(status,str(exc)) from exc
    except OSError as exc:raise HTTPException(503,'本机目录不可访问；已保留当前发布版本') from exc


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class RootRequest(Strict):
    label:str=Field(min_length=1,max_length=100)
    path:str=Field(min_length=1,max_length=2000)
    classification:str=Field(default='local_only',pattern='^(synthetic|local_only)$')
    mapping:dict[str,list[str]]=Field(default_factory=dict)
    enabled:bool=True
    expected_version:int|None=None
    reattach:bool=False


class ScanRequest(Strict):
    confirm_missing:bool=False


class Confirmation(Strict):
    changes:dict[str,str|bool|None]=Field(min_length=1,max_length=30)
    expected_version:str=Field(min_length=64,max_length=64)
    reason:str=Field(min_length=1,max_length=2000)
    source_id:str|None=None
    actor:str=Field(default='本机操作',min_length=1,max_length=80)


class ClearConfirmation(Strict):
    field:str
    expected_version:str
    reason:str=Field(min_length=1,max_length=2000)
    actor:str=Field(default='本机操作',max_length=80)


class PersonRequest(Strict):
    name:str=Field(min_length=1,max_length=80)
    domains:list[str]=Field(default_factory=list,max_length=20)
    enabled:bool=True
    notes:str=Field(default='',max_length=1000)
    expected_version:int|None=None


class Recommendation(Strict):
    query:str=Field(min_length=1,max_length=4000)
    domain:str|None=None
    as_of:str|None=None
    case_key:str|None=None
    recommendation_mode:Literal['technical','workload_only']='technical'


class Assignment(Strict):
    person_id:str
    expected_version:str
    reason:str=Field(min_length=1,max_length=2000)
    confirmed:bool=False
    actor:str=Field(default='本机操作',max_length=80)


@router.get('/overview')
def overview():
    with errors():
        cases=store.list_cases();base=analytics.today();roots=store.roots()
        return {'case_count':len(cases),'active_count':sum(analytics.active(c,base) for c in cases),
                'conflict_cases':sum(bool(c['conflict_fields']) for c in cases),
                'unknown_owner':sum(not c.get('owner_name') for c in cases),
                'roots':roots,'coverage':store.coverage(),'people':analytics.list_people(),
                'owners':sorted({c['owner_name'] for c in cases if c.get('owner_name')}),
                'allowed_roots':[str(p) for p in store.allowed_roots()],
                'domains':domain_options([c.get('technical_domain') for c in cases]+[d for p in analytics.list_people() for d in p['domains']]),
                'privacy':{'mode':os.getenv('DATA_MODE','demo'),'outbound_blocked':protected(),
                           'local_model':local_endpoint(os.getenv('DEEPSEEK_BASE_URL','https://api.deepseek.com'))},
                'field_definitions':{k:v[0] for k,v in parser.FIELDS.items()},'jobs':sync.recent_jobs()}


@router.post('/roots')
def create_root(body:RootRequest):
    with errors():
        data=body.model_dump();data.pop('expected_version');data.pop('reattach')
        return store.save_root(**data)


@router.put('/roots/{ident}')
def update_root(ident:str,body:RootRequest):
    with errors():
        result=store.save_root(**body.model_dump(),ident=ident);sync_mirror(ident);return result


@router.delete('/roots/{ident}')
def purge_root(ident:str,expected_version:int,confirmed:bool=False):
    with errors():
        root=store.get_root(ident)
        if not confirmed or root['enabled'] or root['state']=='scanning' or root['version']!=expected_version:
            raise ValueError('请先停用目录，刷新并明确确认移除索引；原件不会被删除')
        mirrored=sync_mirror(ident)
        if mirrored['state'] not in ('ready','disabled'):raise ValueError('业务库投影尚未清理，请重试后再移除本地索引')
        with store.db(write=True) as conn:
            file_ids=[r[0] for r in conn.execute('SELECT id FROM files WHERE root_id=?',(ident,))]
            for file_id in file_ids:
                conn.execute('DELETE FROM chunks WHERE file_id=?',(file_id,));conn.execute('DELETE FROM revisions WHERE file_id=?',(file_id,))
            conn.execute('DELETE FROM overrides WHERE case_key IN (SELECT case_key FROM cases WHERE root_id=?)',(ident,))
            conn.execute('DELETE FROM overrides WHERE case_key LIKE ?',(ident+':%',))
            for table in ('cases','files','scans'):conn.execute('DELETE FROM '+table+' WHERE root_id=?',(ident,))
            conn.execute('DELETE FROM roots WHERE id=?',(ident,))
            store.audit(conn,ident,'purge_catalog',{'files':len(file_ids)},None,reason='人工确认，仅移除应用索引与解析副本，保留源盘原件')
        return {'removed':True,'source_files_deleted':False,'privacy_lock_retained':protected()}


@router.post('/roots/{ident}/scan',status_code=202)
def scan_root(ident:str,body:ScanRequest,background:BackgroundTasks):
    with errors():
        job=sync.begin(ident);background.add_task(sync.run,ident,job,confirm_missing=body.confirm_missing)
        return {'job_id':job,'state':'scanning'}


@router.post('/roots/{ident}/mirror')
def retry_mirror(ident:str):
    with errors():store.get_root(ident);return sync_mirror(ident)


@router.get('/scans/{ident}')
def scan_job(ident:str):
    with errors():return sync.get_job(ident)


@router.get('/archive')
def archive_overview(query: str = '', offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), domain: str = '', year: str = ''):
    from casework import archive
    with errors(): return archive.overview(query, offset, limit, domain, year)


@router.get('/archive/unassigned')
def archive_unassigned():
    from casework import archive
    with errors(): return archive.unassigned()


@router.get('/archive/{case_key}')
def archive_detail(case_key: str):
    from casework import archive
    with errors(): return archive.detail(case_key)


@router.get('/cases')
def cases(query:str='',owner:str|None=None,domain:str|None=None,stage:str|None=None,due_field:str|None=None,
          due_status:str|None=None,as_of:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200)):
    with errors():return analytics.cases_page(query=query,owner=owner,domain=domain,stage=stage,due_field=due_field,due_status=due_status,as_of=as_of,offset=offset,limit=limit)


@router.get('/cases/{case_key}')
def case_detail(case_key:str):
    with errors():
        case=store.get_case(case_key)
        if not case:raise KeyError('案件不存在或目录已停用')
        return {'case':case,'documents':catalog.documents(case_key,include_removed=True),'meetings':catalog.meetings(case_key=case_key)}


@router.post('/cases/{case_key}/confirm')
def confirm_fields(case_key:str,body:Confirmation):
    with errors():return catalog.confirm(case_key,**body.model_dump())


@router.post('/cases/{case_key}/clear-confirmation')
def clear_fields(case_key:str,body:ClearConfirmation):
    with errors():return catalog.clear_confirmation(case_key,**body.model_dump())


@router.get('/documents/{ident}')
def document(ident:str,revision:str|None=None,parser_key:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(10,ge=1,le=20),
             focus_chunk_id:str|None=Query(None,max_length=128),focus_location:str|None=Query(None,max_length=8000),
             focus_excerpt:str|None=Query(None,max_length=8000)):
    with errors():
        from casework.document_view import document_page
        item=catalog.document(ident,revision,parser_key)
        page=document_page(item,offset=offset,limit=limit,focus_chunk_id=focus_chunk_id,
                           focus_location=focus_location,focus_excerpt=focus_excerpt)
        payload=item.pop('payload');payload.pop('chunks')
        item['source_status']='not_available';item['source_notice']=''
        if item['download_available']:
            try:
                import hashlib
                raw,_=sync.read_source(store.get_root(item['root_id']),item['relative_path'])
                if hashlib.sha256(raw).hexdigest()==item['revision']:
                    item['source_status']='verified'
                else:
                    item.update(download_available=False,source_status='changed',source_notice='磁盘原件已变化，与当前展示内容不一致；请先到共享盘同步更新。')
            except (OSError,ValueError,PermissionError):
                item.update(download_available=False,source_status='unavailable',source_notice='暂时无法读取磁盘原件；解析记录仍保留，请检查共享目录连接与文件状态。')
        return {**item,**payload,**page}


@router.get('/documents/{ident}/download')
def download_document(ident:str,revision:str|None=None):
    with errors():
        with store.db() as conn:row=conn.execute('SELECT * FROM files WHERE id=?',(ident,)).fetchone()
        if not row or not row['active']:raise KeyError('当前源文件已被移除')
        if revision and row['revision']!=revision:raise HTTPException(409,'原件版本已变化，不能用当前文件替代所选历史内容')
        root=store.get_root(row['root_id'])
        if not root['enabled']:raise ValueError('来源目录已停用')
        raw,_=sync.read_source(root,row['relative_path'])
        import hashlib
        if row['revision']!=hashlib.sha256(raw).hexdigest():raise ValueError('源文件已变化，请先同步，避免下载与引用版本不一致')
        filename=row['relative_path'].rsplit('/',1)[-1]
        return Response(raw,media_type=mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                        headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename),'X-Content-Type-Options':'nosniff'})


@router.get('/search')
def search_archive(query:str=Query(min_length=1,max_length=4000),limit:int=Query(8,ge=1,le=30),root_id:str|None=None,domain:str|None=None,owner:str|None=None,kind:str|None=None,query_scope:Literal['ambiguous','mention','topic','mechanism']='ambiguous'):
    from casework.search import search
    with errors():return search(query,limit=limit,root_id=root_id,domain=domain,owner=owner,kind=kind,query_scope=query_scope)


@router.get('/meetings')
def meeting_records(client:str|None=None,ipr:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200)):
    with errors():
        all_records=catalog.meetings()
        records=[r for r in all_records if (not client or r.get('client')==client) and (not ipr or ipr in r['iprs'])]
        clients=sorted({r['client'] for r in records if r.get('client')})
        people={}
        for record in records:
            for name in set(record['agents']):
                person=people.setdefault(name,{'name':name,'meeting_count':0,'case_keys':set(),'latest_date':None})
                person['meeting_count']+=1
                person['case_keys'].add(record['case_key'])
                if record.get('date') and (not person['latest_date'] or record['date']>person['latest_date']):
                    person['latest_date']=record['date']
        summary=[{'name':p['name'],'meeting_count':p['meeting_count'],'case_count':len(p['case_keys']),'latest_date':p['latest_date']} for p in people.values()]
        summary.sort(key=lambda p:(-p['meeting_count'],p['name']))
        return {'rows':records[offset:offset+limit],'total':len(records),'has_more':len(records)>offset+limit,
                'clients':clients,'same_name_across_clients':bool(ipr and not client and len(clients)>1),
                'agent_summary':summary,
                'options':{'clients':sorted({r['client'] for r in all_records if r.get('client')}),
                           'iprs':sorted({name for r in all_records for name in r['iprs']})},
                'meaning':'仅按逐场记录列出实际登记参会人；立案联系人不等于参会人。意见按案件与日期保留，不自动归纳为永久个人偏好。',
                'coverage':store.coverage()}



@router.get('/reports/monthly')
def monthly(start_month:str,end_month:str,group_by:str='technical_domain',client:str|None=None):
    with errors():return analytics.report(start_month,end_month,group_by=group_by,client=client)


@router.get('/reports/topics')
def topic_report(start_month:str,end_month:str,client:str|None=None):
    with errors():return analytics.topics(start_month,end_month,client)


def csv_cell(value):
    text=str(value if value is not None else '')
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text


@router.get('/reports/monthly.csv')
def export_monthly(start_month:str,end_month:str,group_by:str='technical_domain',client:str|None=None):
    with errors():
        report=analytics.report(start_month,end_month,group_by=group_by,client=client)
        stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['月份','分组','实际收案数量','实际交局数量','收案案号','交局案号'])
        bykey={c['case_key']:c['case_id'] for c in report['cases']}
        for row in report['rows']:
            writer.writerow([csv_cell(row['month']),csv_cell(row['group']),row['received_count'],row['filed_count'],
                             csv_cell('、'.join(bykey[k] for k in row['received_case_keys'])),csv_cell('、'.join(bykey[k] for k in row['filed_case_keys']))])
        writer.writerow([]);writer.writerow(['范围',report['start'],report['end'],'右侧边界不含'])
        writer.writerow(['收案日期未知',len(report['unknown_received']),'交局日期未核实',len(report['unknown_filed'])])
        writer.writerow(['范围说明',csv_cell(report['coverage']['scope_note'])])
        return Response(('\ufeff'+stream.getvalue()).encode(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="monthly-cases.csv"'})


@router.get('/people')
def people():
    with errors():return {'people':analytics.list_people()}


@router.post('/people')
def add_person(body:PersonRequest):
    with errors():return analytics.save_person(**body.model_dump())


@router.put('/people/{ident}')
def edit_person(ident:str,body:PersonRequest):
    with errors():return analytics.save_person(**body.model_dump(),ident=ident)


@router.post('/recommendations')
@store.consistent_read
def recommendations(body:Recommendation):
    with errors():
        result=analytics.recommend(**body.model_dump(exclude={'recommendation_mode'}))
        result['recommendation_mode']=body.recommendation_mode
        if body.recommendation_mode=='workload_only':
            analytics.workload_view(result)
        elif analytics.recommendation_needs_confirmation(result):
            from casework.search import search
            lookup=search(body.query,limit=6)
            result.update(requires_user_confirmation=True,candidates=[],technical_lookup=lookup,
                message='目前还不能确认谁办理过这类技术的案件，已暂停推荐。请补充技术点后重新查询，或明确选择仅查看人员负荷。')
        else:
            result['requires_user_confirmation']=False
        return result


@router.post('/cases/{case_key}/assign')
def assign(case_key:str,body:Assignment):
    with errors():return analytics.assign(case_key,**body.model_dump())


@router.get('/audit')
def audit(target:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),action:str|None=Query(None,max_length=80)):
    with errors(),store.db() as conn:
        conditions=[];args=[]
        if target:conditions.append('target=?');args.append(target)
        if action:conditions.append('action=?');args.append(action)
        where=' WHERE '+' AND '.join(conditions) if conditions else ''
        count=conn.execute('SELECT COUNT(*) FROM audit'+where,args).fetchone()[0]
        rows=conn.execute('SELECT * FROM audit'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return {'rows':[{**dict(row),'before':json.loads(row['before_json']),'after':json.loads(row['after_json'])} for row in rows],
                'total':count,'offset':offset,'has_more':count>offset+limit,'actor_note':'操作人由本机共享界面填写，不等于已验证的登录身份'}


async def periodic_sync():
    interval=int(os.getenv('CASEWORK_SYNC_INTERVAL','0'))
    if interval<=0:return
    interval=max(30,min(interval,86400))
    while True:
        await asyncio.sleep(min(interval,30))
        for root in store.roots(enabled=True):
            if root['state']=='scanning':continue
            last=datetime.fromisoformat(root['last_scan']) if root['last_scan'] else None
            if last and (datetime.now(timezone.utc)-last).total_seconds()<interval:continue
            try:
                job=sync.begin(root['id']);await asyncio.to_thread(sync.run,root['id'],job)
            except (ValueError,OSError):continue
