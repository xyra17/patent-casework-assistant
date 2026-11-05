"""Shared, read-only duplicate assessment; MySQL owns text collation semantics."""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
import re
from pathlib import Path
from fastapi import HTTPException
from utils.path_utils import PROJECT_ROOT, RESOURCE_ROOT

_IDENT = re.compile(r'[A-Za-z0-9_]{1,64}\Z')

def quoted(value):
    if not _IDENT.fullmatch(value): raise ValueError('非法查重字段')
    return '`'+value+'`'

def business_rule(table, columns):
    path = Path(os.getenv('BUSINESS_IMPORT_RULES_FILE', str((PROJECT_ROOT if (PROJECT_ROOT/'config'/'business-import-rules.json').is_file() else RESOURCE_ROOT)/'config'/'business-import-rules.json')))
    if not path.is_absolute(): path = PROJECT_ROOT/path
    try:
        config = json.loads(path.read_text())
        if not isinstance(config, dict): raise ValueError('查重配置应为对象')
        rule = config.get(table, {'label':'仅数据库唯一约束', 'columns':[], 'notice':'未配置业务查重标识；不同主键的业务重复不能自动识别。'})
        if not isinstance(rule, dict): raise ValueError('表查重规则应为对象')
        names = rule.get('columns', [])
        if not isinstance(names,list) or len(names)>16 or len(set(names))!=len(names) or any(n not in {c['name'] for c in columns} for n in names):
            raise ValueError('查重字段与实际表结构不符')
        return {'label':str(rule.get('label','业务查重标识')), 'columns':names, 'notice':str(rule.get('notice',''))}
    except (OSError,ValueError,TypeError) as exc:
        raise HTTPException(503,'业务查重配置无效，请核对 BUSINESS_IMPORT_RULES_FILE 与表结构') from exc

def enrich_target(conn, target):
    """Read PK/unique indexes and comparison metadata; never inspect untrusted identifiers."""
    cur=conn.cursor(dictionary=True)
    cur.execute('SELECT COLUMN_NAME,CHARACTER_SET_NAME,COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s',(target['name'],))
    meta={r['COLUMN_NAME']:r for r in cur.fetchall()}
    cur.execute('SELECT COLLATION_NAME,PAD_ATTRIBUTE FROM information_schema.COLLATIONS')
    padding={r['COLLATION_NAME']:r['PAD_ATTRIBUTE']=='PAD SPACE' for r in cur.fetchall()}
    for column in target['columns']:
        info=meta.get(column['name'],{})
        column.update(charset=info.get('CHARACTER_SET_NAME'),collation=info.get('COLLATION_NAME'),pad_space=padding.get(info.get('COLLATION_NAME'),False))
    cur.execute('SELECT INDEX_NAME,COLUMN_NAME,SUB_PART FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND NON_UNIQUE=0 ORDER BY INDEX_NAME,SEQ_IN_INDEX',(target['name'],))
    groups={}
    for row in cur.fetchall(): groups.setdefault(row['INDEX_NAME'],[]).append(row)
    target['unique_keys']=[{'label':name,'columns':[r['COLUMN_NAME'] for r in parts],'prefix':any(r['SUB_PART'] is not None for r in parts)} for name,parts in groups.items() if all(r['COLUMN_NAME'] in meta for r in parts)]
    target['dedupe_rule']=business_rule(target['name'],target['columns'])
    return target

@contextmanager
def table_lock(conn, table):
    cur=conn.cursor();cur.execute('SELECT DATABASE()');database=cur.fetchone()[0]
    name='tanyuan-write:'+hashlib.sha256((database+':'+table).encode()).hexdigest()[:40]
    cur.execute('SELECT GET_LOCK(%s,10)',(name,))
    if cur.fetchone()[0]!=1: raise HTTPException(409,'该表正在导入或新增记录，请稍后重试')
    try: yield
    finally:
        try:cur.execute('SELECT RELEASE_LOCK(%s)',(name,));cur.fetchone()
        except Exception:pass  # Closing the connection also releases its named locks.

def assessment(conn, target, names, prepared, source_rows):
    byname={c['name']:c for c in target['columns']}
    rule=target.get('dedupe_rule',{'columns':[], 'label':'仅数据库唯一约束'})
    business=rule['columns']
    missing=[n for n in business if n not in names]
    if missing: raise HTTPException(422,'需映射业务查重字段：'+'、'.join(missing))
    keys=[{'label':rule['label'],'columns':business,'business':True}] if business else []
    keys += [k for k in target.get('unique_keys',[]) if not k.get('prefix') and set(k['columns']).issubset(names)]
    # Prefix/expression indexes remain enforced by MySQL; never silently skip their conflicts.
    cache={}
    def canonical(name,value):
        if value is None:return None
        col=byname[name];kind=col['type'].lower()
        if re.match(r'^(tinyint|smallint|mediumint|int|bigint|decimal|numeric|float|double|real)\b',kind):return str(Decimal(str(value)).normalize())
        if isinstance(value,(datetime,date)):value=value.isoformat(sep=' ') if isinstance(value,datetime) else value.isoformat()
        text=str(value)
        if kind.startswith(('datetime','timestamp')):text=text.replace('T',' ')
        charset,collation=col.get('charset'),col.get('collation')
        if charset and collation:
            quoted(charset);quoted(collation)
            if col.get('pad_space'):text=text.rstrip(' ')
            ident=(name,text)
            if ident not in cache:
                cur=conn.cursor();cur.execute(f'SELECT HEX(WEIGHT_STRING(CONVERT(%s USING {quoted(charset)}) COLLATE {quoted(collation)}))',(text,));cache[ident]=cur.fetchone()[0]
            return cache[ident]
        return text
    seen=[{} for _ in keys];records=[];accepted=[]
    for index,values in enumerate(prepared):
        incoming=dict(zip(names,values));normalized={n:canonical(n,v) for n,v in incoming.items()}
        state='new';matches=[]
        for key_index,key in enumerate(keys):
            fields=key['columns']
            if any(incoming[n] is None or (key.get('business') and str(incoming[n]).strip()=='') for n in fields):
                if key.get('business'):
                    state='conflict';matches.append({'source':'标识缺失','rule':key['label']})
                continue
            marker=tuple(normalized[n] for n in fields)
            first=seen[key_index].get(marker)
            if first is not None:
                same=first['values']==normalized
                if not same:state='conflict'
                elif state=='new':state='duplicate'
                matches.append({'source':f'文件第 {first["row"]} 行','rule':key['label'],'differences':[n for n in names if first['values'][n]!=normalized[n]]})
            else:seen[key_index][marker]={'values':normalized,'row':source_rows[index]}
            cur=conn.cursor(dictionary=True)
            cur.execute('SELECT '+','.join(quoted(n) for n in names)+' FROM '+quoted(target['name'])+' WHERE '+' AND '.join(quoted(n)+' = %s' for n in fields)+' LIMIT 2',tuple(incoming[n] for n in fields))
            existing=cur.fetchall()
            if existing:
                same=len(existing)==1 and all(canonical(n,existing[0][n])==normalized[n] for n in names)
                if not same:state='conflict'
                elif state=='new':state='duplicate'
                matches.append({'source':'业务数据库','rule':key['label'],'differences':[n for n in names if canonical(n,existing[0][n])!=normalized[n]]})
        records.append({'row':source_rows[index],'status':state,'matches':matches})
        if state=='new':accepted.append(index)
    counts={s:sum(r['status']==s for r in records) for s in ('new','duplicate','conflict')}
    signature=hashlib.sha256(json.dumps(records,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return {**counts,'records':records[:100],'detail_truncated':len(records)>100,'accepted_indices':accepted,'signature':signature,'rule':rule,'unique_keys':[k['label'] for k in keys if not k.get('business')]}
