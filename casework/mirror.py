"""Transactional MySQL projection; the source catalog remains the audit authority."""
from __future__ import annotations

import json
import os
import threading

from casework import store

_LOCK=threading.RLock()
TABLES={'cw_cases','cw_documents','cw_meetings','cw_tags','cw_sync_state'}
CASE_COLUMNS=['case_key','root_id','case_id','title','client_alias','client_case_id','owner_name','technical_domain',
              'stage','received_date','draft_due_date','client_review_due_date','filing_due_date','actual_filed_date',
              'filing_verified','ipr_contact','application_type','commission_type','important','urgent','notes','conflict_fields','source_refs','generation']
DDL=[
'''CREATE TABLE IF NOT EXISTS cw_cases (
 case_key VARCHAR(160) PRIMARY KEY,root_id VARCHAR(32) NOT NULL,case_id VARCHAR(100) NOT NULL,
 title TEXT,client_alias VARCHAR(255),client_case_id VARCHAR(255),owner_name VARCHAR(100),technical_domain VARCHAR(100),stage VARCHAR(100),
 received_date DATE,draft_due_date DATE,client_review_due_date DATE,filing_due_date DATE,actual_filed_date DATE,
 filing_verified BOOLEAN NOT NULL,ipr_contact VARCHAR(255),application_type VARCHAR(100),commission_type VARCHAR(100),
 important VARCHAR(100),urgent VARCHAR(100),notes TEXT,conflict_fields JSON NOT NULL,source_refs JSON NOT NULL,generation BIGINT NOT NULL,
 INDEX(root_id),INDEX(case_id),INDEX(received_date),INDEX(actual_filed_date),INDEX(owner_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS cw_documents (
 document_id VARCHAR(64) PRIMARY KEY,root_id VARCHAR(32) NOT NULL,case_key VARCHAR(160),case_id VARCHAR(100),
 relative_path TEXT NOT NULL,kind VARCHAR(40),revision VARCHAR(64),parser_key VARCHAR(64),state VARCHAR(40),generation BIGINT NOT NULL,INDEX(root_id),INDEX(case_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS cw_meetings (
 meeting_id VARCHAR(64) PRIMARY KEY,root_id VARCHAR(32) NOT NULL,case_key VARCHAR(160),case_id VARCHAR(100),client_alias VARCHAR(255),
 meeting_date DATE,iprs JSON NOT NULL,agents JSON NOT NULL,inventors JSON NOT NULL,content TEXT,pending TEXT,changes TEXT,
 existing_art TEXT,closest_art TEXT,distinguishing_features TEXT,comparison TEXT,primary_points TEXT,secondary_points TEXT,
 source_refs JSON NOT NULL,generation BIGINT NOT NULL,INDEX(root_id),INDEX(case_key),INDEX(meeting_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS cw_tags (
 tag_id VARCHAR(64) PRIMARY KEY,root_id VARCHAR(32) NOT NULL,case_key VARCHAR(160) NOT NULL,tag VARCHAR(100) NOT NULL,
 method VARCHAR(100),source_refs JSON NOT NULL,generation BIGINT NOT NULL,INDEX(root_id),INDEX(case_key),INDEX(tag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS cw_sync_state (
 root_id VARCHAR(32) PRIMARY KEY,label VARCHAR(100),generation BIGINT NOT NULL,classification VARCHAR(40),
 state VARCHAR(40),last_scan VARCHAR(80),snapshot_hash VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''']


def managed(table):
    return os.getenv('CASEWORK_MYSQL_MIRROR','0')=='1' and table.lower() in TABLES


def reject_direct_write(table):
    if managed(table):raise ValueError('共享盘同步表为只读投影，请在案件工作台核对字段或在原件维护，防止下次同步覆盖修改')


def _snapshot(root_id):
    with store.db() as conn:
        conn.execute('BEGIN')
        root=conn.execute('SELECT * FROM roots WHERE id=?',(root_id,)).fetchone()
        if not root:raise KeyError('共享目录不存在')
        cases=[dict(r) for r in conn.execute('SELECT * FROM cases WHERE root_id=?',(root_id,))] if root['enabled'] else []
        files=[dict(r) for r in conn.execute('''SELECT f.*,v.payload FROM files f LEFT JOIN revisions v ON f.id=v.file_id
               AND f.revision=v.revision AND f.parser_key=v.parser_key WHERE f.root_id=? AND f.active=1''',(root_id,))] if root['enabled'] else []
        return dict(root),cases,files


def _hash(root,cases,files):
    return store.digest([root['generation'],root['version'],root['enabled'],sorted((c['case_key'],c['version']) for c in cases),
                         sorted((f['id'],f['revision'],f['state']) for f in files)])


def sync_mirror(root_id):
    if os.getenv('CASEWORK_MYSQL_MIRROR','0')!='1':
        with store.db(write=True) as conn:conn.execute("UPDATE roots SET mirror_state='disabled' WHERE id=?",(root_id,))
        return {'state':'disabled'}
    with _LOCK:
        root,cases,files=_snapshot(root_id);snapshot_hash=_hash(root,cases,files)
        connection=None
        try:
            import mysql.connector
            connection=mysql.connector.connect(host=os.getenv('MYSQL_HOST','localhost'),port=int(os.getenv('MYSQL_PORT','3306')),
                database=os.getenv('MYSQL_DATABASE'),user=os.getenv('MYSQL_CASEWORK_SYNC_USER'),password=os.getenv('MYSQL_CASEWORK_SYNC_PASSWORD'),
                charset='utf8mb4',autocommit=False,use_pure=True,connection_timeout=8,read_timeout=30,write_timeout=30)
            cur=connection.cursor()
            # Row lock serializes projection writers across processes as well.
            cur.execute('INSERT IGNORE INTO cw_sync_state(root_id,generation,snapshot_hash) VALUES(%s,-1,\'\')',(root_id,))
            cur.execute('SELECT generation FROM cw_sync_state WHERE root_id=%s FOR UPDATE',(root_id,))
            previous=cur.fetchone()[0]
            if previous>root['generation']:
                connection.rollback();return {'state':'superseded'}
            for table in ('cw_tags','cw_meetings','cw_documents','cw_cases'):
                cur.execute('DELETE FROM '+table+' WHERE root_id=%s',(root_id,))
            decoded={row['case_key']:json.loads(row['data']) for row in cases}
            values=[]
            for row in cases:
                data={**decoded[row['case_key']],'case_key':row['case_key'],'root_id':root_id,'generation':root['generation'],
                      'conflict_fields':store.dumps(decoded[row['case_key']]['conflict_fields']),'source_refs':row['fields']}
                values.append(tuple(data.get(k) for k in CASE_COLUMNS))
            if values:cur.executemany('INSERT INTO cw_cases('+','.join(CASE_COLUMNS)+') VALUES('+','.join(['%s']*len(CASE_COLUMNS))+')',values)
            documents=[];meetings={};tag_rows=[]
            for file in files:
                key=root_id+':'+file['case_id'] if file['case_id'] else None
                documents.append((file['id'],root_id,key,file['case_id'],file['relative_path'],file['kind'],file['revision'],file['parser_key'],file['state'],root['generation']))
                if file['state']!='parsed' or not file['payload'] or key not in decoded:continue
                payload=json.loads(file['payload'])
                for meeting in payload['meetings']:
                    ident=store.digest({'case_key':key,**{k:v for k,v in meeting.items() if k not in ('location','case_id')}})
                    proof={'file_id':file['id'],'revision':file['revision'],'parser_key':file['parser_key'],'path':file['relative_path'],'location':meeting['location']}
                    if ident not in meetings:meetings[ident]={'record':meeting,'case_key':key,'case_id':file['case_id'],'client':decoded[key].get('client_alias'),'sources':[]}
                    meetings[ident]['sources'].append(proof)
            if documents:cur.executemany('INSERT INTO cw_documents VALUES('+','.join(['%s']*10)+')',documents)
            meeting_rows=[]
            for ident,item in meetings.items():
                m=item['record'];meeting_rows.append((ident,root_id,item['case_key'],item['case_id'],item['client'],m.get('date'),
                    store.dumps(m['iprs']),store.dumps(m['agents']),store.dumps(m['inventors']),
                    *[m.get(k) or '' for k in ('content','pending','changes','existing_art','closest_art','distinguishing_features','comparison','primary_points','secondary_points')],
                    store.dumps(item['sources']),root['generation']))
            if meeting_rows:cur.executemany('INSERT INTO cw_meetings VALUES('+','.join(['%s']*20)+')',meeting_rows)
            for row in cases:
                for tag in json.loads(row['tags']):
                    tag_rows.append((store.digest([row['case_key'],tag['tag']]),root_id,row['case_key'],tag['tag'],tag['method'],store.dumps(tag['sources']),root['generation']))
            if tag_rows:cur.executemany('INSERT INTO cw_tags VALUES('+','.join(['%s']*7)+')',tag_rows)
            cur.execute('UPDATE cw_sync_state SET label=%s,generation=%s,classification=%s,state=%s,last_scan=%s,snapshot_hash=%s WHERE root_id=%s',
                        (root['label'],root['generation'],root['classification'],root['state'] if root['enabled'] else 'disabled',root['last_scan'],snapshot_hash,root_id))
            connection.commit()
            latest,latest_cases,latest_files=_snapshot(root_id)
            state='ready' if _hash(latest,latest_cases,latest_files)==snapshot_hash else 'pending'
            with store.db(write=True) as conn:
                conn.execute('UPDATE roots SET mirror_state=?,mirror_generation=? WHERE id=?',(state,root['generation'],root_id))
            return {'state':state,'generation':root['generation'],'cases':len(cases),'meetings':len(meetings)}
        except Exception as exc:
            if connection:connection.rollback()
            with store.db(write=True) as conn:conn.execute("UPDATE roots SET mirror_state='error' WHERE id=?",(root_id,))
            return {'state':'error','error':'业务库投影同步失败：'+type(exc).__name__+'；本地目录发布版本仍保留，可重试同步'}
        finally:
            if connection:connection.close()


def check_read(sql):
    """Task SQL cannot bypass the program's department definitions."""
    import re
    if os.getenv('CASEWORK_MYSQL_MIRROR','0')!='1' or not any(re.search(r'\b'+t+r'\b',sql,re.I) for t in TABLES):return
    from api import context
    if context.get_thread_id():
        raise PermissionError('共享盘案件数据须使用query_department_cases专用工具，避免绕过日期、未知与重复案号口径。')
    roots=store.roots(enabled=True)
    if any(r['mirror_state']!='ready' or r['mirror_generation']!=r['generation'] for r in roots):
        raise PermissionError('共享盘业务投影未与当前发布代同步，请在案件工作台查看或重试业务投影。')
