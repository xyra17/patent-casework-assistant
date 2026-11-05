"""Explicit local-only advanced maintenance. Never exposed as an agent tool."""
import hashlib
import hmac
import os
import time
from uuid import UUID, uuid5
import mysql.connector
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import parse, exp
from api import business_edit as e

router=APIRouter(prefix='/api/business-database',tags=['advanced-maintenance'])

class Plan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:str=Field(pattern='^(bulk_update|bulk_delete|rename_key)$')
    table:str=Field(min_length=1,max_length=64)
    keys:list[dict[str,str|int]]=Field(min_length=1,max_length=100)
    changes:dict[str,str|None]=Field(default_factory=dict,max_length=100)
    reason:str=Field(min_length=1,max_length=500)

class Apply(Plan):
    token:str=Field(max_length=256)
    operation_id:str
    confirmed:bool=Field(default=False,strict=True)


def digest(value):return hashlib.sha256(e.dumps(value).encode()).hexdigest()
def signature(payload,state,expires):
    secret=os.environ.get('MYSQL_EDIT_PASSWORD') or os.environ.get('MYSQL_CONSOLE_PASSWORD')
    if not secret:raise HTTPException(409,'维护签名密钥未配置')
    return hmac.new(secret.encode(),e.dumps([os.getenv('MYSQL_DATABASE'),payload,state,expires]).encode(),hashlib.sha256).hexdigest()
def token_for(payload,state):
    expires=int(time.time())+600
    return f'{expires}.{signature(payload,state,expires)}'
def verify(token,payload,state):
    try:
        expiry,sig=token.split('.');expiry=int(expiry)
        valid=int(time.time())<=expiry<=int(time.time())+601 and hmac.compare_digest(sig,signature(payload,state,expiry))
    except (ValueError,TypeError):valid=False
    if not valid:raise HTTPException(409,'预览已过期、数据或表结构已变化，请重新预览并核对')
def primary(columns,row):return {c['name']:e.serial(row[c['name']]) for c in columns if c['key']=='PRI'}
def write_audit(conn,ident,fingerprint,table,key,kind,reason,before,after,result):
    conn.cursor().execute('INSERT INTO '+e.audit_table()+' (operation_id,payload_hash,business_database,table_name,row_key,operation,actor,reason,before_json,after_json,result_json) VALUES(%s,%s,DATABASE(),%s,%s,%s,%s,%s,%s,%s,%s)',
        (ident,fingerprint,table,e.dumps(key),kind,'local_shared',reason,e.dumps(before),e.dumps(after) if after is not None else None,e.dumps(result)))

def prepare(conn,body,lock=False):
    if not body.reason.strip():raise ValueError('请填写操作原因')
    columns=e.schema(conn,body.table);byname={c['name']:c for c in columns}
    if len({e.dumps(k) for k in body.keys})!=len(body.keys):raise ValueError('勾选记录不能重复')
    if body.kind=='rename_key' and len(body.keys)!=1:raise ValueError('每次只变更一条主键')
    if body.kind=='bulk_delete' and body.changes:raise ValueError('删除不接受字段修改')
    if body.kind!='bulk_delete' and not body.changes:raise ValueError('请选择要修改的字段')
    updates={}
    for name,value in body.changes.items():
        col=byname.get(name)
        if not col or (body.kind=='rename_key' and col['key']!='PRI') or (body.kind!='rename_key' and not col['editable']):raise ValueError('不可修改字段：'+name)
        if value is not None and len(value)>100000:raise ValueError('单字段内容过长')
        updates[name]=e.coerce(value,col)
    if body.kind=='rename_key' and set(updates)!={c['name'] for c in columns if c['key']=='PRI'}:raise ValueError('请提供完整的新主键')
    records=[];children=[];child_seen=set()
    for key in sorted(body.keys,key=e.dumps):
        row,where,params=e.read_row(conn,body.table,key,columns,lock)
        if row is None:raise HTTPException(409,'选中记录已不存在，请刷新')
        candidate={**row,**updates};e.validate_business(body.table,candidate)
        if body.kind!='bulk_delete':e.validate_foreign_keys(conn,body.table,candidate)
        refs=e.references(conn,body.table,row,lock)
        if body.kind=='bulk_delete' and refs:raise HTTPException(409,'选中记录仍被关联引用，整批不执行：'+e.dumps(key))
        if body.kind=='bulk_update' and any(any(n in updates and str(updates[n])!=str(row[n]) for n in ref['referenced_fields']) for ref in refs):raise HTTPException(409,'修改字段被其他表引用，请使用主键变更或先处理关联')
        if body.kind=='rename_key':
            newkey=primary(columns,candidate)
            if primary(columns,row)==newkey:raise ValueError('新旧主键相同')
            existing,_,_=e.read_row(conn,body.table,newkey,columns,lock)
            if existing:raise HTTPException(409,'新主键已存在')
            for parts in e.foreign_keys(conn,body.table,True):
                first=parts[0];child=first['TABLE_NAME']
                if not any(p['REFERENCED_COLUMN_NAME'] in updates and candidate[p['REFERENCED_COLUMN_NAME']]!=row[p['REFERENCED_COLUMN_NAME']] for p in parts):continue
                if first['TABLE_SCHEMA']!=os.environ['MYSQL_DATABASE'] or child==body.table:raise ValueError('跨库或自引用主键变更需先调整关联结构')
                ccols=e.schema(conn,child);cur=conn.cursor(dictionary=True)
                condition=' AND '.join(e.quoted(p['COLUMN_NAME'])+'=%s' for p in parts)
                cur.execute('SELECT * FROM '+e.quoted(child)+' WHERE '+condition+' ORDER BY '+','.join(e.quoted(c['name']) for c in ccols if c['key']=='PRI')+' LIMIT 501'+(' FOR UPDATE' if lock else ''),tuple(row[p['REFERENCED_COLUMN_NAME']] for p in parts))
                linked=cur.fetchall()
                if len(linked)>500 or len(children)+len(linked)>500:raise ValueError('关联记录超过500条，请分阶段迁移')
                for old in linked:
                    ck=primary(ccols,old);marker=(child,e.dumps(ck))
                    if marker in child_seen:raise ValueError('同一关联记录存在多条引用路径，需人工迁移')
                    child_seen.add(marker)
                    changes={p['COLUMN_NAME']:candidate[p['REFERENCED_COLUMN_NAME']] for p in parts}
                    # Prevent hidden cascades through grandchildren or child primary keys.
                    if any(c['key']=='PRI' and c['name'] in changes for c in ccols):raise ValueError('关联字段属于子表主键，需人工迁移')
                    if any(any(n in changes and changes[n]!=old[n] for n in r['referenced_fields']) for r in e.references(conn,child,old,lock)):raise ValueError('存在多层级联引用，需人工迁移')
                    e.validate_business(child,{**old,**changes})
                    children.append({'table':child,'columns':ccols,'key':ck,'before':old,'changes':changes})
        records.append({'key':primary(columns,row),'before':row,'where':where,'params':params,'candidate':candidate})
    # SHOW CREATE binds defaults, indexes, FK actions and generated columns, not just field names.
    tables=sorted({body.table,*[c['table'] for c in children]});definitions={}
    for table in tables:
        cur=conn.cursor();cur.execute('SHOW CREATE TABLE '+e.quoted(table));definitions[table]=cur.fetchone()[1]
    state=digest({'schema':definitions,'records':[e.document(r['before']) for r in records],'children':[{**c,'before':e.document(c['before'])} for c in children]})
    return columns,updates,records,children,state

@router.post('/advanced/preview')
def preview(body:Plan):
    conn=None
    try:
        conn=e.connect();cols,updates,records,children,state=prepare(conn,body)
        return {'token':token_for(body.model_dump(),state),'count':len(records),'related_count':len(children),
            'records':[{'key':r['key'],'before':e.document(r['before']),'after':None if body.kind=='bulk_delete' else e.document(r['candidate'])} for r in records],
            'related':[{'table':c['table'],'key':c['key'],'changes':e.document(c['changes'])} for c in children]}
    except Exception as exc:raise e.failure(exc) from exc
    finally:
        if conn:conn.close()

@router.post('/advanced/apply')
def apply(body:Apply):
    if not body.confirmed:raise HTTPException(400,'请确认预览内容')
    try:ident=str(UUID(body.operation_id))
    except ValueError:raise HTTPException(422,'操作编号必须为UUID')
    payload=body.model_dump(exclude={'token','operation_id','confirmed'});fingerprint=digest(payload);conn=None
    try:
        conn=e.connect();conn.start_transaction(isolation_level='READ COMMITTED')
        old=e.previous(conn,ident,fingerprint)
        if old:return {**old,'replayed':True}
        cols,updates,records,children,state=prepare(conn,body,True)
        old=e.previous(conn,ident,fingerprint)
        if old:return {**old,'replayed':True}
        verify(body.token,payload,state);cur=conn.cursor();result={'status':'completed','operation_id':ident,'operation':body.kind,'table':body.table,'count':len(records),'related_count':len(children)}
        for index,r in enumerate(records):
            if body.kind=='rename_key':
                # Clone -> move direct references -> remove old parent. FK checks stay enabled.
                cur.execute('SHOW FULL COLUMNS FROM '+e.quoted(body.table));rawcols=cur.fetchall()
                # Cursor is tuple based: Field, Type, Collation, Null, Key, Default, Extra, ...
                names=[c[0] for c in rawcols if not any(x in (c[6] or '').upper() for x in ('VIRTUAL GENERATED','STORED GENERATED'))]
                if children:
                    cur.execute('INSERT INTO '+e.quoted(body.table)+' ('+','.join(map(e.quoted,names))+') VALUES ('+','.join(['%s']*len(names))+')',tuple(r['candidate'][n] for n in names))
                    for j,c in enumerate(children):
                        _,where,params=e.read_row(conn,c['table'],c['key'],c['columns'],True)
                        cur.execute('UPDATE '+e.quoted(c['table'])+' SET '+','.join(e.quoted(n)+'=%s' for n in c['changes'])+' WHERE '+where,tuple(list(c['changes'].values())+params))
                        after,_,_=e.read_row(conn,c['table'],c['key'],c['columns'])
                        write_audit(conn,str(uuid5(UUID(ident),f'child:{j}')),fingerprint,c['table'],c['key'],'rekey_child',body.reason,e.document(c['before']),e.document(after),result)
                    cur.execute('DELETE FROM '+e.quoted(body.table)+' WHERE '+r['where'],tuple(r['params']))
                else:cur.execute('UPDATE '+e.quoted(body.table)+' SET '+','.join(e.quoted(n)+'=%s' for n in updates)+' WHERE '+r['where'],tuple(list(updates.values())+r['params']))
                result['key']=primary(cols,r['candidate'])
                after,_,_=e.read_row(conn,body.table,result['key'],cols)
            elif body.kind=='bulk_update':
                cur.execute('UPDATE '+e.quoted(body.table)+' SET '+','.join(e.quoted(n)+'=%s' for n in updates)+' WHERE '+r['where'],tuple(list(updates.values())+r['params']))
                after,_,_=e.read_row(conn,body.table,r['key'],cols)
            else:
                cur.execute('DELETE FROM '+e.quoted(body.table)+' WHERE '+r['where'],tuple(r['params']));after=None
                if cur.rowcount!=1:raise HTTPException(409,'影响行数异常，整批回滚')
            write_audit(conn,str(uuid5(UUID(ident),f'row:{index}')),fingerprint,body.table,r['key'],'rename_key' if body.kind=='rename_key' else body.kind.replace('bulk_',''),body.reason,e.document(r['before']),e.document(after) if after else None,result)
        write_audit(conn,ident,fingerprint,body.table,{'batch_count':len(records)},'rekey_total' if body.kind=='rename_key' else body.kind,body.reason,{'count':len(records)},{'count':len(records),'related_count':len(children)},result)
        conn.commit();return result
    except Exception as exc:
        if conn:
            try:conn.rollback()
            except Exception:pass
        raise e.failure(exc,ident) from exc
    finally:
        if conn:conn.close()

# Console: a different account, scoped to business tables; no server/account management.
class SQLPlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sql:str=Field(min_length=1,max_length=50000)
    reason:str=Field(min_length=1,max_length=500)
class SQLApply(SQLPlan):
    token:str=Field(max_length=256)
    operation_id:str
    confirmed:bool=Field(default=False,strict=True)

def sql_table():return e.quoted(os.getenv('MYSQL_EDIT_AUDIT_DATABASE','tanyuan_management'))+'.`sql_operations`'
def console_connect():
    if os.getenv('MYSQL_CONSOLE_ENABLED')!='1':raise HTTPException(409,'SQL控制台连接未配置')
    conn=mysql.connector.connect(host=os.getenv('MYSQL_HOST','localhost'),port=int(os.getenv('MYSQL_PORT','3306')),user=os.environ['MYSQL_CONSOLE_USER'],password=os.environ['MYSQL_CONSOLE_PASSWORD'],database=os.environ['MYSQL_DATABASE'],charset='utf8mb4',autocommit=False,use_pure=True,connection_timeout=15,read_timeout=30)
    conn.cursor().execute("SET time_zone='+00:00'");return conn

def classify(sql):
    try:trees=parse(sql,read='mysql')
    except Exception as exc:raise ValueError('SQL解析失败，请检查MySQL语法') from exc
    if len(trees)!=1 or trees[0] is None:raise ValueError('每次只执行一条SQL')
    tree=trees[0];kind=type(tree).__name__.upper()
    if not isinstance(tree,(exp.Select,exp.Union,exp.Insert,exp.Update,exp.Delete,exp.Create,exp.Alter,exp.Drop,exp.TruncateTable,exp.Show,exp.Describe)):raise ValueError('仅支持查询、增删改，以及业务表CREATE/ALTER/DROP/TRUNCATE')
    ddl=isinstance(tree,(exp.Create,exp.Alter,exp.Drop,exp.TruncateTable))
    if isinstance(tree,(exp.Create,exp.Drop)) and tree.args.get('kind')!='TABLE':raise ValueError('仅支持业务表DDL，不支持库、视图、函数或账号操作')
    database=os.environ['MYSQL_DATABASE'];tables=set()
    if isinstance(tree,exp.Show):
        if tree.this not in {'TABLES','COLUMNS','INDEX','CREATE TABLE'}:raise ValueError('SHOW仅支持当前业务库的表、字段、索引或建表定义')
        db=tree.args.get('db');target=tree.args.get('target')
        if db and (not isinstance(db,exp.Identifier) or db.name.lower()!=database.lower()):raise ValueError('SHOW只能访问当前业务库')
        if target:
            if not isinstance(target,exp.Identifier):raise ValueError('表名不受支持')
            e.quoted(target.name);tables.add(target.name)
    if isinstance(tree,exp.Describe) and not isinstance(tree.this,(exp.Table,exp.Select,exp.Union)):raise ValueError('只支持描述业务表或解释查询计划')
    for t in tree.find_all(exp.Table):
        if t.catalog or (t.db and t.db.lower()!=database.lower()):raise ValueError('SQL只能访问当前业务库')
        if not isinstance(t.this,exp.Identifier):raise ValueError('不支持动态表或表函数')
        e.quoted(t.name)
        tables.add(t.name)  # Include CTE aliases conservatively, so they cannot hide a physical write target.
    if not isinstance(tree,(exp.Select,exp.Union,exp.Show,exp.Describe)):
        from casework.mirror import reject_direct_write
        for table in tables:reject_direct_write(table)
    # Reject external I/O, server side effects, locking and unparsed parser fallbacks.
    for node in tree.walk():
        if isinstance(node,(exp.Command,exp.Into,exp.Lock)):raise ValueError('不支持文件输出、锁定或无法解析的语句')
        if isinstance(node,exp.Func):
            name=node.name.upper() if isinstance(node,exp.Anonymous) else node.sql_name().upper()
            if name in {'LOAD_FILE','SLEEP','BENCHMARK','GET_LOCK','RELEASE_LOCK','SYS_EXEC','SYS_EVAL'}:raise ValueError('不支持文件、等待或服务器管理函数')
    if isinstance(tree,exp.Alter):
        allowed=(exp.ColumnDef,exp.Drop,exp.AlterColumn,exp.AddConstraint,exp.RenameColumn,exp.AlterRename)
        if any(not isinstance(a,allowed) for a in tree.args.get('actions',[])):raise ValueError('此ALTER操作尚不支持；支持字段、索引、约束和重命名')
    if isinstance(tree,(exp.Select,exp.Union)):
        limit=tree.args.get('limit')
        value=limit.expression if limit else None
        if value is None or not isinstance(value,exp.Literal) or not value.is_int or int(value.this)>501:tree=tree.limit(501)
    normalized=tree.sql(dialect='mysql')
    return normalized,kind,ddl,sorted(tables)

def sql_state(conn,tables,ddl):
    definitions={};cur=conn.cursor()
    for table in tables:
        cur.execute('SELECT ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s',(table,));row=cur.fetchone()
        if row:
            if row[1]!='BASE TABLE' or (not ddl and row[0]!='InnoDB'):raise ValueError('仅支持业务基础表，事务写入要求InnoDB')
            cur.execute('SHOW CREATE TABLE '+e.quoted(table));definitions[table]=cur.fetchone()[1]
        else:definitions[table]=None
    return digest(definitions)

@router.post('/sql/preview')
def sql_preview(body:SQLPlan):
    conn=None
    try:
        if not body.reason.strip():raise ValueError('请填写执行原因')
        normalized,kind,ddl,tables=classify(body.sql);conn=console_connect();state=sql_state(conn,tables,ddl)
        return {'sql':normalized,'kind':kind,'ddl':ddl,'tables':tables,'token':token_for(body.model_dump(),state),'notice':'DDL可能隐式提交，不能保证事务回滚；删除或清空的数据不会自动备份。' if ddl else 'SQL写入记录语句和影响行数，不生成逐行差异或自动恢复备份。查询最多返回500条。'}
    except Exception as exc:raise e.failure(exc) from exc
    finally:
        if conn:conn.close()

@router.post('/sql/execute')
def sql_execute(body:SQLApply):
    if not body.confirmed:raise HTTPException(400,'请核对SQL并确认')
    try:ident=str(UUID(body.operation_id))
    except ValueError:raise HTTPException(422,'操作编号必须为UUID')
    conn=None;claimed=False;ddl=False;payload=body.model_dump(exclude={'token','operation_id','confirmed'});fingerprint=digest(payload)
    try:
        normalized,kind,ddl,tables=classify(body.sql);conn=console_connect();cur=conn.cursor(dictionary=True)
        cur.execute('SELECT payload_hash,status,result_json FROM '+sql_table()+' WHERE operation_id=%s',(ident,));old=cur.fetchone();conn.rollback()
        if old:
            if old['payload_hash']!=fingerprint:raise HTTPException(409,'相同操作编号不能用于不同SQL')
            if old['status']!='completed':raise HTTPException(409,'该操作已登记为'+old['status']+'，请核对执行记录和数据库，不会自动重复执行')
            import json
            return {**json.loads(old['result_json']),'replayed':True}
        verify(body.token,payload,sql_state(conn,tables,ddl));conn.rollback()
        cur.execute('INSERT INTO '+sql_table()+' (operation_id,payload_hash,business_database,sql_text,reason,status,kind) VALUES(%s,%s,DATABASE(),%s,%s,%s,%s)',(ident,fingerprint,normalized,body.reason.strip(),'executing',kind));conn.commit();claimed=True
        # Re-check after claiming. DDL is explicitly not represented as rollback-safe.
        verify(body.token,payload,sql_state(conn,tables,ddl));conn.rollback()
        conn.start_transaction();cursor=conn.cursor();cursor.execute(normalized)
        result={'status':'completed','operation_id':ident,'kind':kind,'ddl':ddl,'affected_rows':max(cursor.rowcount,0),'columns':[],'rows':[],'truncated':False}
        if cursor.with_rows:
            names=[];seen=set()
            for description in cursor.description:
                name=description[0];label=name;index=2
                while label in seen:label=f'{name} [{index}]';index+=1
                names.append(label);seen.add(label)
            result['columns']=names;rows=cursor.fetchmany(501);result['truncated']=len(rows)>500
            result['rows']=[[e.serial(v) for v in row] for row in rows[:500]]
            # Consume without retaining unbounded data, so the audit statement can run.
            while cursor.fetchmany(500):pass
            result['affected_rows']=None
        cur=conn.cursor();cur.execute('UPDATE '+sql_table()+' SET status=%s,result_json=%s WHERE operation_id=%s',('completed',e.dumps(result),ident));conn.commit();return result
    except Exception as exc:
        if conn:
            try:conn.rollback()
            except Exception:pass
            if claimed:
                # Connector/network or DDL failures can have an uncertain outcome.
                status='uncertain' if ddl or not isinstance(exc,(HTTPException,ValueError,mysql.connector.IntegrityError,mysql.connector.ProgrammingError)) else 'failed'
                try:
                    conn.cursor().execute('UPDATE '+sql_table()+' SET status=%s,result_json=%s WHERE operation_id=%s',(status,e.dumps({'message':'执行未完成，请核对数据库；同一编号禁止重复执行','mysql_errno':getattr(exc,'errno',None)}),ident));conn.commit()
                except Exception:pass
        if isinstance(exc,mysql.connector.Error) and getattr(exc,'errno',None) in {1050,1051,1054,1064,1146,1364,1406}:
            raise HTTPException(422,{'message':str(getattr(exc,'msg','SQL执行失败'))[:500],'mysql_errno':exc.errno,'operation_id':ident,'notice':'请核对执行记录；DDL可能已部分提交，重试需重新预览。'}) from exc
        raise e.failure(exc,ident) from exc
    finally:
        if conn:conn.close()

@router.get('/sql/history')
def sql_history():
    conn=None
    try:
        conn=console_connect();cur=conn.cursor(dictionary=True);cur.execute('SELECT operation_id,sql_text,reason,actor,status,kind,created_at,result_json FROM '+sql_table()+' WHERE business_database=DATABASE() ORDER BY created_at DESC LIMIT 50')
        import json
        records=cur.fetchall()
        for r in records:
            raw=r.pop('result_json');r['result']=json.loads(raw) if raw else None;r['created_at']=r['created_at'].isoformat()+'+00:00'
        return {'records':records}
    except Exception as exc:raise e.failure(exc) from exc
    finally:
        if conn:conn.close()
