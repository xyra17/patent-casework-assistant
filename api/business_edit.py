"""Local record maintenance: typed fields, primary-key locks, FK checks and atomic MySQL audit."""
import base64
import hashlib
import json
import os
import re
from datetime import date,datetime
from decimal import Decimal
from uuid import UUID
import mysql.connector
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from api.data_import import _convert
from api import business_duplicates as duplicates
from tools.semantic_layer import semantic_layer

router=APIRouter(prefix='/api/business-database',tags=['business-maintenance'])
_IDENT=re.compile(r'[A-Za-z0-9_]{1,64}\Z')
_SUPPORTED=re.compile(r'^(tinyint|smallint|mediumint|int|bigint|decimal|numeric|float|double|real|varchar|char|tinytext|text|mediumtext|longtext|date|datetime|timestamp|json)\b',re.I)


def quoted(name):
    if not _IDENT.fullmatch(name):raise ValueError('表名或字段名不受支持')
    return '`'+name+'`'


def configured():
    return os.getenv('MYSQL_EDIT_ENABLED','0')=='1' and bool(os.getenv('MYSQL_EDIT_USER') and os.getenv('MYSQL_EDIT_PASSWORD'))


def connect():
    if not configured():raise HTTPException(409,'本地数据维护连接未配置')
    if os.getenv('MYSQL_EDIT_AUDIT_DATABASE','tanyuan_management')==os.getenv('MYSQL_DATABASE'):raise HTTPException(409,'审计库必须独立于业务库')
    conn=mysql.connector.connect(host=os.getenv('MYSQL_HOST','localhost'),port=int(os.getenv('MYSQL_PORT','3306')),
        user=os.environ['MYSQL_EDIT_USER'],password=os.environ['MYSQL_EDIT_PASSWORD'],database=os.environ['MYSQL_DATABASE'],
        charset='utf8mb4',autocommit=False,use_pure=True,connection_timeout=15,read_timeout=30)
    conn.cursor().execute("SET time_zone = '+00:00'")
    return conn


def audit_table():return quoted(os.getenv('MYSQL_EDIT_AUDIT_DATABASE','tanyuan_management'))+'.`row_audit`'


def serial(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,bytes):return {'binary_base64':base64.b64encode(value).decode()}
    if isinstance(value,int) and not isinstance(value,bool) and abs(value)>9007199254740991:return str(value)
    return value


def document(row):return {k:serial(v) for k,v in row.items()}
def dumps(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str)
def version(row):return hashlib.sha256(dumps(document(row)).encode()).hexdigest()


def schema(conn,table):
    from casework.mirror import reject_direct_write
    reject_direct_write(table)
    quoted(table)
    cur=conn.cursor(dictionary=True)
    cur.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND TABLE_TYPE='BASE TABLE'",(table,))
    entry=cur.fetchone()
    if not entry:raise HTTPException(404,'业务表不存在')
    if entry['ENGINE']!='InnoDB':raise HTTPException(422,'仅支持InnoDB事务表维护')
    cur.execute(f'SHOW FULL COLUMNS FROM {quoted(table)}');cols=cur.fetchall()
    result=[]
    for c in cols:
        name=c['Field'];quoted(name);extra=(c['Extra'] or '').lower();kind=c['Type']
        result.append({'name':name,'type':kind,'nullable':c['Null']=='YES','key':c['Key'],'comment':c['Comment'] or '',
            'default':str(c['Default']) if c.get('Default') is not None else None, 'has_default':c.get('Default') is not None,
            'generated':any(x in extra for x in ('auto_increment','virtual generated','stored generated')),
            'auto_increment':'auto_increment' in extra,
            'insertable':not any(x in extra for x in ('auto_increment','virtual generated','stored generated','on update')) and bool(_SUPPORTED.match(kind)),
            'label':semantic_layer._tables.get(table,{}).get('columns',{}).get(name,{}).get('label',c['Comment'] or name),
            'editable':c['Key']!='PRI' and not any(s in extra for s in ('auto_increment','generated','on update')) and bool(_SUPPORTED.match(kind))})
    if not any(c['key']=='PRI' for c in result):raise HTTPException(422,'没有主键的表不能编辑或删除单条记录')
    return result


def coerce(value,column):
    if value is None:
        if not column['nullable']:raise ValueError('该字段不能为NULL')
        return None
    if not isinstance(value,str):raise ValueError('字段值须以文本传递，避免大整数或金额精度丢失')
    if 'char' in column['type'].lower() or 'text' in column['type'].lower():
        if not value.strip():return value
    if not value.strip():raise ValueError('不能为空；如需空值请显式选择NULL')
    return _convert(value,column)


def read_row(conn,table,key,columns,lock=False):
    primary=[c for c in columns if c['key']=='PRI']
    if set(key)!={c['name'] for c in primary}:raise HTTPException(422,'必须提供完整主键，禁止无条件或批量删改')
    try:values=[coerce(str(key[c['name']]),c) for c in primary]
    except ValueError as exc:raise HTTPException(422,'主键格式不正确') from exc
    where=' AND '.join(quoted(c['name'])+' = %s' for c in primary)
    cur=conn.cursor(dictionary=True);cur.execute(f'SELECT * FROM {quoted(table)} WHERE {where}'+(' FOR UPDATE' if lock else ''),tuple(values))
    row=cur.fetchone()
    return row,where,values


def foreign_keys(conn,table,incoming=False):
    cur=conn.cursor(dictionary=True)
    condition='REFERENCED_TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME=%s' if incoming else 'TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL'
    cur.execute('SELECT CONSTRAINT_NAME,TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_SCHEMA,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE '+condition+' ORDER BY CONSTRAINT_NAME,ORDINAL_POSITION',(table,))
    groups={}
    for r in cur.fetchall():groups.setdefault((r['TABLE_SCHEMA'],r['TABLE_NAME'],r['CONSTRAINT_NAME']),[]).append(r)
    return list(groups.values())


def references(conn,table,row,lock=False):
    found=[]
    for parts in foreign_keys(conn,table,True):
        first=parts[0];target=quoted(first['TABLE_SCHEMA'])+'.'+quoted(first['TABLE_NAME'])
        params=[row[p['REFERENCED_COLUMN_NAME']] for p in parts]
        cur=conn.cursor();cur.execute('SELECT 1 FROM '+target+' WHERE '+' AND '.join(quoted(p['COLUMN_NAME'])+' = %s' for p in parts)+' LIMIT 1'+(' FOR UPDATE' if lock else ''),tuple(params))
        if cur.fetchone():found.append({'table':first['TABLE_NAME'],'fields':[p['COLUMN_NAME'] for p in parts],'referenced_fields':[p['REFERENCED_COLUMN_NAME'] for p in parts]})
    return found


def validate_foreign_keys(conn,table,row):
    for parts in foreign_keys(conn,table):
        values=[row[p['COLUMN_NAME']] for p in parts]
        if any(v is None for v in values):continue
        first=parts[0];target=quoted(first['REFERENCED_TABLE_SCHEMA'])+'.'+quoted(first['REFERENCED_TABLE_NAME'])
        cur=conn.cursor();cur.execute('SELECT 1 FROM '+target+' WHERE '+' AND '.join(quoted(p['REFERENCED_COLUMN_NAME'])+' = %s' for p in parts)+' LIMIT 1 FOR SHARE',tuple(values))
        if not cur.fetchone():raise HTTPException(422,'关联记录不存在：'+first['REFERENCED_TABLE_NAME'])


def validate_business(table,row):
    for name in {'drugs':['generic_name'],'inventory':['batch_number']}.get(table,[]):
        if not str(row.get(name) or '').strip():raise ValueError(name+'不能为空')
    fields={'inventory':['quantity_on_hand'],'sales_records':['quantity_sold','unit_price','total_amount']}.get(table,[])
    for f in fields:
        if row.get(f) is not None and Decimal(str(row[f]))<0:raise ValueError(f'{f}不能为负数')
    if table=='inventory' and row.get('production_date') and row.get('expiry_date') and str(row['production_date'])>str(row['expiry_date']):raise ValueError('生产日期不能晚于有效期至')


class RowKey(BaseModel):
    model_config=ConfigDict(extra='forbid')
    table: str=Field(min_length=1,max_length=64)
    key: dict[str,str|int]=Field(min_length=1,max_length=16)


class Mutation(RowKey):
    expected_version: str=Field(pattern=r'^[a-f0-9]{64}$')
    changes: dict[str,str|None]=Field(default_factory=dict,max_length=100)
    reason: str=Field(min_length=1,max_length=500)
    operation_id: str=Field(min_length=36,max_length=36)
    confirmed: bool=Field(default=False,strict=True)

    def operation(self):
        try:return str(UUID(self.operation_id))
        except ValueError as exc:raise HTTPException(422,'操作编号必须为UUID') from exc


def failure(exc,operation_id=None):
    if isinstance(exc,HTTPException):return exc
    if isinstance(exc,ValueError):return HTTPException(422,str(exc))
    if getattr(exc,'errno',None) in (1062,1451,1452):return HTTPException(409,'违反唯一键或关联约束，数据未修改；请检查重复值或关联记录')
    if getattr(exc,'errno',None) in (1205,1213):return HTTPException(409,'记录正在被其他操作修改，请刷新后核对重试')
    return HTTPException(503,'维护连接或提交异常；如已提交，请用原操作编号重试核实，勿重复创建操作'+('：'+operation_id if operation_id else ''))


@router.post('/row-detail')
def detail(body:RowKey):
    conn=None
    try:
        conn=connect();columns=schema(conn,body.table);row,_,_=read_row(conn,body.table,body.key,columns)
        if row is None:raise HTTPException(404,'记录已不存在，请刷新')
        return {'values':document(row),'columns':columns,'version':version(row),'referenced_by':references(conn,body.table,row),
                'key':{c['name']:serial(row[c['name']]) for c in columns if c['key']=='PRI'}}
    except Exception as exc:raise failure(exc) from exc
    finally:
        if conn:conn.close()


def previous(conn,ident,fingerprint=None):
    cur=conn.cursor(dictionary=True);cur.execute('SELECT payload_hash,result_json FROM '+audit_table()+' WHERE operation_id=%s',(ident,));row=cur.fetchone()
    if not row:return None
    if fingerprint and row['payload_hash']!=fingerprint:raise HTTPException(409,'相同操作编号不能用于不同修改')
    return json.loads(row['result_json'])


def mutate(body:Mutation,kind):
    if not body.confirmed:raise HTTPException(400,'请核对修改或删除内容后确认')
    if not body.reason.strip():raise HTTPException(422,'请填写修改原因')
    ident=body.operation();fingerprint=hashlib.sha256(dumps({'database':os.getenv('MYSQL_DATABASE'),'kind':kind,**body.model_dump(exclude={'confirmed','operation_id'})}).encode()).hexdigest();conn=None
    try:
        conn=connect();conn.start_transaction(isolation_level='READ COMMITTED')
        existing=previous(conn,ident,fingerprint)
        if existing:conn.rollback();return {**existing,'replayed':True}
        columns=schema(conn,body.table);row,where,params=read_row(conn,body.table,body.key,columns,True)
        if row is None:raise HTTPException(404,'记录已不存在，请刷新')
        # A second same-key request may have waited on the row lock.
        existing=previous(conn,ident,fingerprint)
        if existing:conn.rollback();return {**existing,'replayed':True}
        if version(row)!=body.expected_version:raise HTTPException(409,'记录已被其他操作修改，请刷新并重新核对，不能覆盖新内容')
        before=document(row);after=None;cur=conn.cursor()
        if kind=='update':
            if not body.changes:raise ValueError('没有需要保存的修改')
            byname={c['name']:c for c in columns};updates={}
            for name,value in body.changes.items():
                column=byname.get(name)
                if not column or not column['editable']:raise ValueError('不可编辑字段：'+name)
                if value is not None and len(value)>100000:raise ValueError('单字段内容过长')
                try:updates[name]=coerce(value,column)
                except ValueError as exc:raise ValueError(name+'：'+str(exc)) from exc
            candidate={**row,**updates};validate_business(body.table,candidate);validate_foreign_keys(conn,body.table,candidate)
            linked=references(conn,body.table,row,True)
            if any(any(name in updates and str(updates[name])!=str(row[name]) for name in ref.get('referenced_fields',[])) for ref in linked):raise HTTPException(409,'修改字段被其他业务记录引用，请先处理关联，禁止隐式级联修改')
            cur.execute('UPDATE '+quoted(body.table)+' SET '+', '.join(quoted(k)+'=%s' for k in updates)+' WHERE '+where,tuple(list(updates.values())+params))
            new,_,_=read_row(conn,body.table,body.key,columns);after=document(new)
        else:
            if body.changes:raise ValueError('删除操作不能携带字段修改')
            linked=references(conn,body.table,row,True)
            if linked:raise HTTPException(409,{'message':'该记录仍被其他业务表引用，请先处理关联记录，不执行隐式级联删除','references':linked})
            cur.execute('DELETE FROM '+quoted(body.table)+' WHERE '+where,tuple(params))
            if cur.rowcount!=1:raise HTTPException(409,'删除影响行数异常，操作已回滚')
        result={'status':'completed','operation_id':ident,'operation':kind,'table':body.table,'key':body.key,'values':after}
        cur.execute('INSERT INTO '+audit_table()+' (operation_id,payload_hash,business_database,table_name,row_key,operation,actor,reason,before_json,after_json,result_json) VALUES(%s,%s,DATABASE(),%s,%s,%s,%s,%s,%s,%s,%s)',
            (ident,fingerprint,body.table,dumps(body.key),kind,'local_shared',body.reason.strip(),dumps(before),dumps(after) if after is not None else None,dumps(result)))
        conn.commit();return result
    except Exception as exc:
        if conn:
            try:conn.rollback()
            except Exception:pass
        raise failure(exc,ident) from exc
    finally:
        if conn:conn.close()


@router.patch('/row')
def update(body:Mutation):return mutate(body,'update')


@router.post('/delete-row')
def delete(body:Mutation):return mutate(body,'delete')


@router.get('/audit')
def history(table:str|None=None,limit:int=50):
    conn=None
    try:
        conn=connect();cur=conn.cursor(dictionary=True)
        if table:quoted(table)
        cur.execute('SELECT operation_id,business_database,table_name,row_key,operation,actor,reason,before_json,after_json,created_at FROM '+audit_table()+' WHERE business_database=DATABASE()'+(' AND table_name=%s' if table else '')+' ORDER BY created_at DESC,operation_id DESC LIMIT %s',tuple(([table] if table else [])+[max(1,min(limit,100))]))
        rows=cur.fetchall();records=[]
        for row in rows:
            key=json.loads(row.pop('row_key'));before=json.loads(row.pop('before_json'));after=json.loads(row.pop('after_json') or 'null')
            records.append({**row,'created_at':row['created_at'].isoformat()+'+00:00','key':key,'before':before,'after':after,'field_labels':{name:cfg.get('label',name) for name,cfg in semantic_layer._tables.get(row['table_name'],{}).get('columns',{}).items()}})
        return {'records':records}
    except Exception as exc:raise failure(exc) from exc
    finally:
        if conn:conn.close()


class TableRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    table:str=Field(min_length=1,max_length=64)

class Creation(TableRequest):
    values:dict[str,str|None]=Field(min_length=1,max_length=100)
    reason:str=Field(min_length=1,max_length=500)
    operation_id:str=Field(min_length=36,max_length=36)
    confirmed:bool=Field(default=False,strict=True)

    def operation(self):
        try:return str(UUID(self.operation_id))
        except ValueError as exc:raise HTTPException(422,'操作编号必须为UUID') from exc


def creation_target(conn,table,columns):
    return duplicates.enrich_target(conn,{'name':table,'columns':columns})

@router.post('/row-template')
def row_template(body:TableRequest):
    conn=None
    try:
        conn=connect();columns=schema(conn,body.table)
        target=creation_target(conn,body.table,columns)
        return {'columns':columns,'values':{c['name']:None for c in columns},'dedupe_rule':target['dedupe_rule']}
    except Exception as exc:raise failure(exc) from exc
    finally:
        if conn:conn.close()

@router.post('/create-row')
def create_row(body:Creation):
    if not body.confirmed:raise HTTPException(400,'请核对新记录后确认新增')
    if not body.reason.strip():raise HTTPException(422,'请填写新增原因')
    ident=body.operation();fingerprint=hashlib.sha256(dumps({'database':os.getenv('MYSQL_DATABASE'),'kind':'insert',**body.model_dump(exclude={'confirmed','operation_id'})}).encode()).hexdigest()
    conn=None
    try:
        conn=connect()
        with duplicates.table_lock(conn,body.table):
            conn.start_transaction(isolation_level='READ COMMITTED')
            existing=previous(conn,ident,fingerprint)
            if existing:conn.rollback();return {**existing,'replayed':True}
            columns=schema(conn,body.table);byname={c['name']:c for c in columns};values={}
            for name,value in body.values.items():
                c=byname.get(name)
                if not c or not c['insertable']:raise ValueError('不可新增字段：'+name)
                if value is not None and len(value)>100000:raise ValueError('单字段内容过长')
                try:values[name]=coerce(value,c)
                except ValueError as exc:raise ValueError(name+'：'+str(exc)) from exc
            missing=[c['name'] for c in columns if c['insertable'] and not c['nullable'] and not c['has_default'] and c['name'] not in values]
            if missing:raise ValueError('缺少必填字段：'+'、'.join(missing))
            for c in columns:
                if c['key']=='PRI' and not c['auto_increment'] and c['name'] not in values:raise ValueError('非自增主键需要填写：'+c['name'])
            validate_business(body.table,values)
            target=creation_target(conn,body.table,columns)
            report=duplicates.assessment(conn,target,list(values),[list(values.values())],[1])
            if report['duplicate'] or report['conflict']:raise HTTPException(409,'业务标识重复或内容冲突，请核对现有记录，未新增')
            validate_foreign_keys(conn,body.table,{c['name']:values.get(c['name']) for c in columns})
            cur=conn.cursor();cur.execute("SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ZERO_DATE,NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO'")
            cur.execute('INSERT INTO '+quoted(body.table)+' ('+','.join(quoted(n) for n in values)+') VALUES ('+','.join('%s' for _ in values)+')',tuple(values.values()))
            key={c['name']:serial(cur.lastrowid if c['auto_increment'] else values[c['name']]) for c in columns if c['key']=='PRI'}
            row,_,_=read_row(conn,body.table,key,columns)
            if row is None:raise HTTPException(409,'新增后未能读取记录，操作已回滚')
            after=document(row);result={'status':'completed','operation_id':ident,'operation':'insert','table':body.table,'key':key,'values':after}
            cur.execute('INSERT INTO '+audit_table()+' (operation_id,payload_hash,business_database,table_name,row_key,operation,actor,reason,before_json,after_json,result_json) VALUES(%s,%s,DATABASE(),%s,%s,%s,%s,%s,%s,%s,%s)',
                (ident,fingerprint,body.table,dumps(key),'insert','local_shared',body.reason.strip(),'null',dumps(after),dumps(result)))
            conn.commit();return result
    except Exception as exc:
        if conn:
            try:conn.rollback()
            except Exception:pass
        raise failure(exc,ident) from exc
    finally:
        if conn:conn.close()
