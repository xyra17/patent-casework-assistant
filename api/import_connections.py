"""Server-owned import connection allowlist; only safe labels/targets reach the client."""
import json
import os
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
import mysql.connector
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field
from utils.path_utils import PROJECT_ROOT
from api import source_policy

_current=ContextVar('import_connection',default=None)

class Profile(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,48}$')
    label:str=Field(min_length=1,max_length=80)
    host:str=Field(min_length=1,max_length=255)
    port:int=Field(default=3306,ge=1,le=65535)
    database:str=Field(pattern=r'^[A-Za-z0-9_]{1,64}$')
    read_user_env:str=Field(pattern=r'^[A-Z][A-Z0-9_]{0,79}$')
    read_password_env:str=Field(pattern=r'^[A-Z][A-Z0-9_]{0,79}$')
    write_user_env:str=Field(pattern=r'^[A-Z][A-Z0-9_]{0,79}$')
    write_password_env:str=Field(pattern=r'^[A-Z][A-Z0-9_]{0,79}$')


def profiles():
    default={'id':'default','label':'本地业务数据库','host':os.getenv('MYSQL_HOST','localhost'),'port':int(os.getenv('MYSQL_PORT','3306')),
             'database':os.getenv('MYSQL_DATABASE','department_workbench'),'read_user_env':'MYSQL_USER','read_password_env':'MYSQL_PASSWORD','write_user_env':'MYSQL_IMPORT_USER','write_password_env':'MYSQL_IMPORT_PASSWORD'}
    result=[default];raw=os.getenv('IMPORT_CONNECTIONS_FILE','').strip()
    if raw:
        try:
            path=Path(raw).expanduser();path=path if path.is_absolute() else PROJECT_ROOT/path
            entries=json.loads(path.read_text())
            if not isinstance(entries,list) or len(entries)>20:raise ValueError('invalid list')
            for entry in entries:
                p=Profile.model_validate(entry).model_dump()
                if p['id'] in {item['id'] for item in result}:raise ValueError('duplicate connection')
                result.append(p)
        except Exception as exc:raise HTTPException(503,'导入连接配置无效，请在服务端检查 IMPORT_CONNECTIONS_FILE') from exc
    return result


def public_profiles():
    return [{k:p[k] for k in ('id','label','host','port','database')} for p in profiles()]


def current():return _current.get()


@contextmanager
def scope(ident='default'):
    profile=next((p for p in profiles() if p['id']==ident),None)
    if not profile:raise HTTPException(404,'所选导入连接不存在或已停用，请重新选择')
    token=_current.set(profile)
    try:yield profile
    finally:_current.reset(token)


def configured():
    p=current()
    return bool(os.getenv(p['write_user_env'],'').strip() and os.getenv(p['write_password_env'],'')) if p else bool(os.getenv('MYSQL_IMPORT_USER','').strip() and os.getenv('MYSQL_IMPORT_PASSWORD',''))


def destination():
    p=current()
    return {k:p[k] for k in ('host','port','database')} if p else {'host':os.getenv('MYSQL_HOST','localhost'),'port':int(os.getenv('MYSQL_PORT','3306')),'database':os.getenv('MYSQL_DATABASE','department_workbench')}


def connect(write=False):
    if not write:
        source_policy.require_source('db_query')
    p=current()
    if not p:raise HTTPException(409,'尚未选择导入连接')
    user=p['write_user_env' if write else 'read_user_env'];password=p['write_password_env' if write else 'read_password_env']
    if not os.getenv(user):raise HTTPException(409,'所选连接缺少服务端账号配置')
    return mysql.connector.connect(host=p['host'],port=p['port'],database=p['database'],user=os.environ[user],password=os.getenv(password,''),charset='utf8mb4',autocommit=not write,use_pure=True,connection_timeout=15,read_timeout=30,write_timeout=30)
