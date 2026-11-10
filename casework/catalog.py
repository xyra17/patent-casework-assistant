"""Atomic published case view and optimistic, auditable field confirmation."""
from __future__ import annotations

import json
from collections import defaultdict

from casework import parser, store


def fingerprint(field, source_id=None):
    values=sorted({store.dumps(c['value']) for c in field.get('candidates',[])})
    support=[(c['file_id'],c['revision']) for c in field.get('candidates',[]) if c['file_id']==source_id]
    return store.digest({'values':values,'support':support})


def rebuild(conn, root_id, generation):
    files=conn.execute('''SELECT f.*,r.payload FROM files f LEFT JOIN revisions r
      ON r.file_id=f.id AND r.revision=f.revision AND r.parser_key=f.parser_key
      WHERE f.root_id=? AND f.active=1 AND f.state IN ('parsed','inventory')
      AND f.case_id IS NOT NULL ORDER BY f.relative_path''',(root_id,)).fetchall()
    grouped=defaultdict(list)
    for file in files:
        payload=json.loads(file['payload']) if file['payload'] else {
            'kind':file['kind'],'chunks':[], 'claims':[{'field':'case_id','value':file['case_id'],'location':'案件目录命名'}]}
        grouped[file['case_id']].append((file,payload))
    conn.execute('DELETE FROM cases WHERE root_id=?',(root_id,))
    for case_id,documents in grouped.items():
        case_key=root_id+':'+case_id
        candidates=defaultdict(list);tag_sources=defaultdict(list)
        for file,payload in documents:
            source={'file_id':file['id'],'revision':file['revision'],'parser_key':file['parser_key'],'path':file['relative_path']}
            for claim in payload['claims']:
                item={**source,'location':claim['location'],'value':claim['value']}
                if item not in candidates[claim['field']]:candidates[claim['field']].append(item)
            if payload['kind'] in ('disclosure','application'):
                for chunk in payload['chunks']:
                    for tag in chunk['tags']:
                        proof={**source,'location':chunk['location'],'excerpt':chunk['text']}
                        if proof not in tag_sources[tag]:tag_sources[tag].append(proof)
        fields={};data={'case_id':case_id}
        overrides={r['field']:dict(r) for r in conn.execute('SELECT * FROM overrides WHERE case_key=?',(case_key,))}
        for field,(label,_) in parser.FIELDS.items():
            choices=candidates[field]
            distinct={store.dumps(c['value']) for c in choices}
            item={'label':label,'value':choices[0]['value'] if len(distinct)==1 else None,
                  'state':'verified_source' if len(distinct)==1 else ('conflict' if distinct else 'unknown'),
                  'candidates':choices}
            override=overrides.get(field)
            if override:
                # A separately selected supporting file must still be current.
                supporting=next((f for f,p in documents if f['id']==override['source_id'] and f['state']=='parsed'),None)
                expected=fingerprint(item,override['source_id'])
                if override['source_id'] and supporting and not any(c['file_id']==supporting['id'] for c in choices):
                    expected=store.digest({'field':fingerprint(item),'support_revision':supporting['revision']})
                valid=override['base_fingerprint']==expected and (not override['source_id'] or supporting is not None)
                item.update(value=json.loads(override['value']) if valid else None,
                            state='confirmed' if valid else 'confirmation_stale',
                            confirmation={'value':json.loads(override['value']),'actor':override['actor'],
                                          'reason':override['reason'],'source_id':override['source_id'],'updated_at':override['updated_at']})
            fields[field]=item;data[field]=item['value']
        # A recorded contact isn't evidence of meeting attendance, and dates aren't inferred.
        data['filing_verified']=bool(data.get('actual_filed_date') and data.get('filing_confirmed') is True)
        data['conflict_fields']=[name for name,f in fields.items() if f['state'] in ('conflict','confirmation_stale')]
        data['document_count']=len(documents)
        tags=[{'tag':tag,'method':'local_dictionary_v1','sources':proof[:8]} for tag,proof in sorted(tag_sources.items())]
        version=store.digest({'data':data,'fields':fields,'tags':tags})
        conn.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?,?)',
                     (case_key,root_id,case_id,store.dumps(data),store.dumps(fields),store.dumps(tags),version,generation))


def confirm_in_transaction(conn, case_key, changes, expected_version, reason, source_id=None, actor='本机操作'):
    """Apply field confirmation and its audit inside the caller's write transaction."""
    if not changes or not reason.strip():raise ValueError('请填写待核对字段及确认依据')
    if any(field not in parser.FIELDS or field=='case_id' for field in changes):
        raise ValueError('不支持修改案号或未知字段；案号需回到原件核对')
    root_id=case_key.split(':',1)[0]
    row=conn.execute('SELECT * FROM cases WHERE case_key=?',(case_key,)).fetchone()
    root=conn.execute('SELECT * FROM roots WHERE id=?',(root_id,)).fetchone()
    if not row or not root:raise KeyError('案件不存在')
    if not root['enabled'] or row['version']!=expected_version or root['state']=='scanning':
        raise ValueError('案件或同步状态已变化，请刷新后核对')
    fields=json.loads(row['fields']);before=json.loads(row['data'])
    support=None
    if source_id:
        support=conn.execute("SELECT * FROM files WHERE id=? AND root_id=? AND case_id=? AND active=1 AND state='parsed'",(source_id,root_id,before['case_id'])).fetchone()
        if not support:raise ValueError('所选依据文件不是本案当前有效文件')
    values={field:parser.normalize_value(field,value) for field,value in changes.items()}
    if values.get('filing_confirmed') is True and not source_id:
        raise ValueError('确认实际交局必须选择本案依据文件，并填写可核对的说明')
    if 'actual_filed_date' in values and not source_id:
        raise ValueError('实际交局日期必须关联本案依据文件')
    for field,value in values.items():
        base=fingerprint(fields[field],source_id)
        if support and not any(c['file_id']==source_id for c in fields[field]['candidates']):
            base=store.digest({'field':fingerprint(fields[field]),'support_revision':support['revision']})
        conn.execute('INSERT OR REPLACE INTO overrides VALUES(?,?,?,?,?,?,?,?)',
                     (case_key,field,store.dumps(value),source_id,reason[:2000],base,actor[:80],store.now()))
    rebuild(conn,root_id,root['generation']+1)
    store.audit(conn,case_key,'field_confirmation',before,values,actor,reason)
    conn.execute("UPDATE roots SET mirror_state='pending',generation=generation+1 WHERE id=?",(root_id,))
    return before


def confirm(case_key, changes, expected_version, reason, source_id=None, actor='本机操作'):
    with store.db(write=True) as conn:
        confirm_in_transaction(conn,case_key,changes,expected_version,reason,source_id,actor)
    from casework.mirror import sync_mirror
    sync_mirror(case_key.split(':',1)[0])
    return store.get_case(case_key)


def clear_confirmation(case_key,field,expected_version,reason,actor='本机操作'):
    if field not in parser.FIELDS or not reason.strip():raise ValueError('请指定字段并说明原因')
    root_id=case_key.split(':',1)[0]
    with store.db(write=True) as conn:
        case=conn.execute('SELECT * FROM cases WHERE case_key=?',(case_key,)).fetchone()
        root=conn.execute('SELECT * FROM roots WHERE id=?',(root_id,)).fetchone()
        if not case or not root:raise KeyError('案件不存在')
        if not root['enabled'] or case['version']!=expected_version or root['state']=='scanning':raise ValueError('案件已变化，请刷新')
        old=conn.execute('SELECT * FROM overrides WHERE case_key=? AND field=?',(case_key,field)).fetchone()
        conn.execute('DELETE FROM overrides WHERE case_key=? AND field=?',(case_key,field))
        rebuild(conn,root_id,root['generation']+1)
        store.audit(conn,case_key,'clear_confirmation',dict(old) if old else None,{'field':field},actor,reason)
        conn.execute("UPDATE roots SET mirror_state='pending',generation=generation+1 WHERE id=?",(root_id,))
    from casework.mirror import sync_mirror
    sync_mirror(root_id)
    return store.get_case(case_key)


def documents(case_key=None, *, root_id=None, include_removed=False):
    sql='SELECT f.*,rt.label AS root_label FROM files f JOIN roots rt ON rt.id=f.root_id WHERE rt.enabled=1'
    args=[]
    if not include_removed:sql+=' AND f.active=1'
    if case_key:
        root,case=case_key.split(':',1);sql+=' AND f.root_id=? AND f.case_id=?';args.extend([root,case])
    elif root_id:sql+=' AND f.root_id=?';args.append(root_id)
    with store.db() as conn:
        rows=conn.execute(sql+' ORDER BY f.relative_path',args).fetchall()
        return [dict(r) for r in rows]


def document(file_id,revision=None,parser_key=None):
    with store.db() as conn:
        file=conn.execute('SELECT f.*,r.enabled,r.label AS root_label FROM files f JOIN roots r ON f.root_id=r.id WHERE f.id=?',(file_id,)).fetchone()
        if not file:raise KeyError('文件记录不存在')
        revision=revision or file['revision'];parser_key=parser_key or file['parser_key']
        row=conn.execute('SELECT * FROM revisions WHERE file_id=? AND revision=? AND parser_key=?',(file_id,revision,parser_key)).fetchone()
        history=conn.execute('SELECT revision,parser_key,created_at FROM revisions WHERE file_id=? ORDER BY created_at DESC',(file_id,)).fetchall()
    if row is None:
        if file['state']!='inventory' or file['revision']!=revision or file['parser_key']!=parser_key:
            raise KeyError('该版本没有成功解析的正文')
        available=bool(file['active'] and file['enabled'])
        note='仅登记附件并按所编号关联，未提取正文或识别图形关系。旧版 .vsd 可在本机另存为 .vsdx 后重新同步，以检索图中文字。' if file['kind']=='drawing' else '仅登记附件，未提取正文。'
        return {**dict(file),'revision':revision,'parser_key':parser_key,'current':available,
                'current_revision':file['revision'],'current_parser_key':file['parser_key'],
                'content_current':available,'download_available':available,
                'version_status':'inventory' if available else ('disabled' if not file['enabled'] else 'removed'),
                'version_note':note,'parsed_at':None,
                'payload':{'kind':file['kind'],'claims':[],'meetings':[],'chunks':[],
                           'warnings':[{'location':'文件登记','reason':note}]},'history':[dict(r) for r in history]}
    current=bool(file['active'] and file['enabled'] and file['state']=='parsed' and file['revision']==revision and file['parser_key']==parser_key)
    content_current=bool(file['active'] and file['enabled'] and file['revision']==revision)
    if not file['enabled']:
        status,note='disabled','来源目录已停用；保留引用时的解析记录，不能据此确认当前案件事实。'
    elif not file['active']:
        status,note='removed','原件已从接入目录移除；这里保留引用时的解析记录，无法下载原件。'
    elif file['revision']!=revision:
        status,note='content_changed','原件内容已更新；这里展示引用时的历史内容，不能当作当前案件事实。'
    elif file['state']!='parsed':
        status,note='parse_failed','原件的最新解析未成功；这里展示上次成功解析的记录，引用前请核对原件。'
    elif not current:
        status,note='parser_updated','原件内容未变，解析方式已更新；这里保留本次引用的解析记录，可下载同一份原件，或查看当前解析。'
    else:
        status,note='current','当前已发布解析版本'
    return {**dict(file),'revision':revision,'parser_key':parser_key,'current':current,
            'current_revision':file['revision'],'current_parser_key':file['parser_key'],
            'content_current':content_current,'download_available':content_current,
            'version_status':status,'version_note':note,'parsed_at':row['created_at'],
            'payload':json.loads(row['payload']),'history':[dict(r) for r in history]}


@store.consistent_read
def meetings(*, client=None, ipr=None, case_key=None):
    cases={c['case_key']:c for c in store.list_cases()}
    result=[];seen={}
    for file in documents(case_key):
        if file['state']!='parsed':continue
        info=document(file['id']);case=cases.get(file['root_id']+':'+str(file['case_id']))
        if not case:continue
        if client and case.get('client_alias')!=client:continue
        records=info['payload']['meetings']
        if any(not m.get('field_locations') for m in records) and file['relative_path'].lower().endswith('.xlsx'):
            # Read-only provenance repair for older snapshots. Reparse only
            # hash-identical, allowlisted originals; never mutate old revisions.
            try:
                from pathlib import Path
                import hashlib
                root=store.get_root(file['root_id'])
                path=store.approved_path(Path(root['path'])/file['relative_path'])
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()==file['revision']:
                    fresh=parser.parse(raw,file['relative_path'],root['mapping'])['meetings']
                    def values(m):return {k:m.get(k) for k in parser.MEETING_FIELDS}
                    records=[{**m,'field_locations':next((n.get('field_locations',{}) for n in fresh if values(n)==values(m)),{})} for m in records]
            except (ValueError,OSError,KeyError):
                pass # No verified cells available; retain an explicit gap.
        for meeting in records:
            if ipr and ipr not in meeting['iprs']:continue
            # Duplicate copies shouldn't manufacture additional meetings.
            key=store.digest({'case_key':case['case_key'],**{k:v for k,v in meeting.items() if k not in ('location','case_id','field_locations')}})
            proof={'file_id':file['id'],'revision':file['revision'],'parser_key':file['parser_key'],'path':file['relative_path'],
                   'location':meeting['location'].split(' · 第')[0]+'（记录级；逐字段位置见field_locations）','field_locations':meeting.get('field_locations',{}),
                   'location_scope':'record only; use field_locations for individual values'}
            if key in seen:
                seen[key]['sources'].append(proof);continue
            item={**meeting,'location':proof['location'],'meeting_id':key,'case_key':case['case_key'],'case_id':case['case_id'],
                  'client':case.get('client_alias'),'title':case.get('title'),'sources':[proof]}
            seen[key]=item;result.append(item)
    return sorted(result,key=lambda x:(x.get('date') or '',x['case_id'],x['meeting_id']),reverse=True)
