"""Bounded read-only scan. Publish once; incomplete traversal never removes files."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from uuid import uuid4

from casework import catalog, parser, store
from tools.document_parser import PARSER_VERSION
from tools.mineru_parser import NoExtractedText

MAX_BYTES=20*1024*1024


def read_source(root, relative):
    """Open each component relative to its parent FD, refusing symbolic links."""
    parts=relative.split('/')
    if not parts or any(p in ('','.','..') or '\\' in p or '\x00' in p for p in parts):
        raise ValueError('非法共享盘相对路径')
    base=store.approved_path(root['path'])
    directory=os.open(base,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        try:
            before=os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):raise ValueError('只读取普通文件')
            if before.st_size>MAX_BYTES:raise ValueError('文件超过20MB解析上限')
            chunks=[];size=0
            while True:
                block=os.read(fd,min(1024*1024,MAX_BYTES+1-size))
                if not block:break
                chunks.append(block);size+=len(block)
                if size>MAX_BYTES:raise ValueError('读取中的文件超过20MB上限')
            after=os.fstat(fd)
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                raise ValueError('文件读取过程中发生变化，等待下一次稳定版本')
            return b''.join(chunks),after
        finally:os.close(fd)
    finally:os.close(directory)


def begin(root_id):
    root=store.get_root(root_id)
    if not root['enabled']:raise ValueError('目录已停用')
    with store.db(write=True) as conn:
        changed=conn.execute("UPDATE roots SET state='scanning',last_error=NULL WHERE id=? AND state!='scanning' AND enabled=1",(root_id,)).rowcount
        if not changed:raise ValueError('目录已在同步中，请等待本次完成')
        job=uuid4().hex
        conn.execute('INSERT INTO scans(id,root_id,state,started_at) VALUES(?,?,?,?)',(job,root_id,'scanning',store.now()))
    return job


def progress(job, data):
    with store.db(write=True) as conn:
        conn.execute('UPDATE scans SET progress=? WHERE id=?',(store.dumps(data),job))


def run(root_id,job=None,*,confirm_missing=False):
    job=job or begin(root_id)
    root=store.get_root(root_id)
    summary={'seen':0,'parsed':0,'unchanged':0,'inventory':0,'failed':0,'removed':0,'renamed':0,'issues':[]}
    try:
        base=store.approved_path(root['path'])
        if store.identity(base)!=root['identity']:
            raise ValueError('共享目录身份发生变化或挂载点已更换；未删除旧索引，请核对后重新确认挂载')
        with store.db() as conn:
            old={r['relative_path']:dict(r) for r in conn.execute('SELECT * FROM files WHERE root_id=?',(root_id,))}
            missing_vectors={r[0] for r in conn.execute('SELECT DISTINCT c.file_id FROM chunks c JOIN files f ON c.file_id=f.id AND c.revision=f.revision AND c.parser_key=f.parser_key WHERE f.root_id=? AND c.vector IS NULL',(root_id,))}
        from casework.search import embedding_config
        embedding_enabled=embedding_config()[0] is not None
        from casework.chunking import config as chunk_config
        from tools.mineru_parser import parser_profile
        parser_key=store.digest({'parser':parser.VERSION,'document_parser':PARSER_VERSION,'document_profile':parser_profile(),'mapping':root['mapping'],'embedding':embedding_config()[1],'chunking':chunk_config()})
        staged=[];seen=set();walk_errors=[];limited=False
        limit=int(os.getenv('CASEWORK_MAX_FILES','20000'))
        paths=[]
        for directory,dirs,names in os.walk(base,followlinks=False,onerror=lambda error:walk_errors.append(type(error).__name__)):
            dirs[:]=sorted(d for d in dirs if not d.startswith(('.', '~')) and not (Path(directory)/d).is_symlink()
                           and not (Path(directory)==base and (base/'.casework-layout.json').is_file() and d=='90_公司文档'))
            for name in sorted(names):
                if name.startswith(('.', '~$')):continue
                path=Path(directory)/name
                if path.suffix.lower() not in parser.SUPPORTED|parser.INVENTORY:continue
                if len(paths)>=limit:limited=True;break
                paths.append(path.relative_to(base).as_posix())
            if limited:break
        for number,relative in enumerate(paths,1):
            seen.add(relative);summary['seen']+=1
            previous=old.get(relative)
            # A verified archive relocation keeps citation IDs stable.
            file_id=previous['id'] if previous else hashlib.sha256((root_id+'\0'+relative).encode()).hexdigest()[:32]
            item={'id':file_id,'relative_path':relative,'revision':None,'payload':None,'vectors':None,
                  'case_id':None,'kind':parser.classify(relative),'state':'error','error':None,'size':0,'mtime':None}
            try:
                raw,info=read_source(root,relative)
                revision=hashlib.sha256(raw).hexdigest();item.update(revision=revision,size=info.st_size,mtime=info.st_mtime)
                previous=old.get(relative)
                if previous and not (embedding_enabled and file_id in missing_vectors) and previous['revision']==revision and previous['parser_key']==parser_key and previous['state'] in ('parsed','inventory'):
                    summary['unchanged']+=1;item.update(state=previous['state'],case_id=previous['case_id'],kind=previous['kind'],unchanged=True)
                elif Path(relative).suffix.lower() in parser.INVENTORY:
                    case_ids={m.group().upper() for m in parser.CASE_RE.finditer(relative)}
                    if len(case_ids)>1:raise ValueError('路径包含多个不同所编号，需人工关联，未猜测所属案件')
                    item.update(state='inventory',case_id=next(iter(case_ids),None))
                    summary['inventory']+=1
                else:
                    payload=parser.parse(raw,relative,root['mapping'])
                    item.update(payload=payload,case_id=payload['case_id'],kind=payload['kind'],state='parsed')
                    summary['parsed']+=1
                    for warning in payload['warnings']:
                        summary['issues'].append({'path':relative,**warning})
            except NoExtractedText as exc:
                # Preserve discovery of pure diagrams without claiming OCR success.
                case_ids={m.group().upper() for m in parser.CASE_RE.finditer(relative)}
                if item['kind']=='drawing' and len(case_ids)==1:
                    item.update(state='inventory',case_id=next(iter(case_ids)),error=str(exc))
                    summary['inventory']+=1
                else:
                    item['error']=str(exc);summary['failed']+=1
                summary['issues'].append({'path':relative,'reason':str(exc)})
            except Exception as exc:
                # Do not keep serving a prior successful version as current after a failed update.
                item['error']=str(exc)[:400] if isinstance(exc,ValueError) else '本机读取或解析失败：'+type(exc).__name__
                summary['failed']+=1;summary['issues'].append({'path':relative,'reason':item['error']})
            staged.append(item)
            if number%10==0:progress(job,{'phase':'parse','processed':number,'discovered':len(paths)})
        complete=not walk_errors and not limited
        if not complete:
            summary['issues'].append({'reason':'目录遍历未完成；保留未扫描文件，不执行消失文件清理','errors':walk_errors,'file_limit_reached':limited})
        missing=[p for p,item in old.items() if item['active'] and p not in seen]
        large_removal=bool(missing) and (len(missing)==sum(bool(f['active']) for f in old.values()) or len(missing)>max(10,len(old)*.2))
        held=complete and large_removal and not confirm_missing
        summary['pending_removals']=missing[:200] if held else []
        summary['pending_removal_count']=len(missing) if held else 0
        if held:summary['issues'].append({'reason':'大量文件消失，需核对挂载和文件清单后确认清理；当前未移除旧索引'})
        from casework.search import embed_chunks
        changed=[item for item in staged if item['payload']]
        text=[chunk['text'] for item in changed for chunk in item['payload']['chunks']]
        vectors,embedding_key,embedding_note=embed_chunks(text)
        summary['embedding']=embedding_note
        at=0
        for item in changed:
            size=len(item['payload']['chunks']);item['vectors']=vectors[at:at+size] if vectors else [];at+=size
        progress(job,{'phase':'publish','processed':len(paths),'discovered':len(paths)})
        if store.identity(base)!=root['identity']:raise ValueError('发布前共享目录身份变化，本次扫描不发布，保留旧索引')
        with store.db(write=True) as conn:
            latest=conn.execute('SELECT * FROM roots WHERE id=?',(root_id,)).fetchone()
            if not latest or latest['version']!=root['version']:raise ValueError('扫描期间目录配置发生变化，本次不发布')
            generation=latest['generation']+1
            for item in staged:
                if item.get('unchanged'):
                    conn.execute('UPDATE files SET active=1,generation=?,mtime=?,updated_at=? WHERE id=?',
                                 (generation,item['mtime'],store.now(),item['id']))
                    continue
                conn.execute('''INSERT INTO files(id,root_id,relative_path,case_id,revision,parser_key,kind,state,error,size,mtime,active,generation,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET case_id=excluded.case_id,revision=excluded.revision,
                  parser_key=excluded.parser_key,kind=excluded.kind,state=excluded.state,error=excluded.error,size=excluded.size,
                  mtime=excluded.mtime,active=1,generation=excluded.generation,updated_at=excluded.updated_at''',
                  (item['id'],root_id,item['relative_path'],item['case_id'],item['revision'],parser_key,item['kind'],item['state'],item['error'],item['size'],item['mtime'],1,generation,store.now()))
                if item['payload']:
                    conn.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?)',(item['id'],item['revision'],parser_key,store.dumps(item['payload']),store.now()))
                    for index,chunk in enumerate(item['payload']['chunks']):
                        chunk_id=store.digest([item['id'],item['revision'],parser_key,index])
                        vector=item['vectors'][index] if item['vectors'] else None
                        conn.execute('INSERT OR REPLACE INTO chunks VALUES(?,?,?,?,?,?,?,?,?,?)',
                                     (chunk_id,item['id'],item['revision'],parser_key,index,chunk['text'],chunk['location'],store.dumps(chunk['tags']),store.dumps(vector) if vector else None,embedding_key if vector else None))
            if complete and not held:
                new_hashes={i['revision'] for i in staged if i['relative_path'] not in old}
                for path in missing:
                    conn.execute('UPDATE files SET active=0,state=\'removed\',generation=?,updated_at=? WHERE id=?',(generation,store.now(),old[path]['id']))
                    summary['removed']+=1
                    summary['renamed']+=old[path]['revision'] in new_hashes
            catalog.rebuild(conn,root_id,generation)
            state='needs_confirmation' if held else ('partial' if not complete or summary['failed'] else 'ready')
            conn.execute('UPDATE roots SET generation=?,state=?,last_scan=?,last_error=?,mirror_state=\'pending\' WHERE id=?',
                         (generation,state,store.now(),store.dumps(summary['issues'][:50]) if summary['issues'] else None,root_id))
            summary['generation']=generation;summary['full_traversal']=complete;summary['issues']=summary['issues'][:200]
            conn.execute('UPDATE scans SET state=?,finished_at=?,summary=?,progress=? WHERE id=?',
                         (state,store.now(),store.dumps(summary),store.dumps({'phase':'finished','processed':len(paths),'discovered':len(paths)}),job))
            store.audit(conn,root_id,'scan_publish',{'generation':root['generation']},{k:v for k,v in summary.items() if k!='issues'})
        from casework.mirror import sync_mirror
        sync_mirror(root_id)
    except Exception as exc:
        reason=str(exc)[:400] if isinstance(exc,ValueError) else '同步失败：'+type(exc).__name__
        summary['issues'].append({'reason':reason})
        with store.db(write=True) as conn:
            conn.execute("UPDATE roots SET state='unavailable',last_error=? WHERE id=?",(reason,root_id))
            conn.execute("UPDATE scans SET state='unavailable',finished_at=?,summary=? WHERE id=?",(store.now(),store.dumps(summary),job))
    return get_job(job)


def get_job(ident):
    with store.db() as conn:
        row=conn.execute('SELECT * FROM scans WHERE id=?',(ident,)).fetchone()
    if row is None:raise KeyError('同步任务不存在')
    return {**dict(row),'summary':json.loads(row['summary']),'progress':json.loads(row['progress'])}


def recent_jobs(root_id=None):
    with store.db() as conn:
        rows=conn.execute('SELECT id FROM scans'+(' WHERE root_id=?' if root_id else '')+' ORDER BY started_at DESC LIMIT 30',
                          (root_id,) if root_id else ()).fetchall()
    return [get_job(r['id']) for r in rows]


def recover_interrupted():
    with store.db(write=True) as conn:
        conn.execute("UPDATE scans SET state='interrupted',finished_at=? WHERE state='scanning'",(store.now(),))
        conn.execute("UPDATE roots SET state='partial',last_error='上次进程在同步中退出，已保留发布版本，请重新扫描' WHERE state='scanning'")
