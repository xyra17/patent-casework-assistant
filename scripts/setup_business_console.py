"""Provision a schema-scoped SQL console; never grant these rights to agent readers."""
import os
import re
import secrets
import mysql.connector
from dotenv import dotenv_values,set_key
from scripts.setup_business_editor import ROOT

SQL_AUDIT_DDL='''CREATE TABLE IF NOT EXISTS sql_operations (
 operation_id CHAR(36) PRIMARY KEY, payload_hash CHAR(64) NOT NULL,
 business_database VARCHAR(64) NOT NULL, sql_text LONGTEXT NOT NULL,
 reason VARCHAR(500) NOT NULL, actor VARCHAR(80) NOT NULL DEFAULT 'local_shared', status VARCHAR(16) NOT NULL, kind VARCHAR(20) NOT NULL,
 result_json LONGTEXT, created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 INDEX history(business_database,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''

def provision():
    cfg=dotenv_values(ROOT/'.env');host=cfg.get('MYSQL_HOST','localhost')
    if host not in ('localhost','127.0.0.1','::1'):raise RuntimeError('此脚本仅适用于本机开发库')
    db=cfg['MYSQL_DATABASE'];audit=cfg.get('MYSQL_EDIT_AUDIT_DATABASE','tanyuan_management')
    user=cfg.get('MYSQL_CONSOLE_USER','tanyuan_console');password=cfg.get('MYSQL_CONSOLE_PASSWORD') or secrets.token_urlsafe(32)
    if db==audit or user in {'root',cfg.get('MYSQL_USER'),cfg.get('MYSQL_EDIT_USER'),cfg.get('MYSQL_IMPORT_USER')}:raise RuntimeError('SQL控制台必须使用独立账号和审计库')
    if not all(re.fullmatch('[A-Za-z0-9_]{1,64}',v) for v in (db,audit,user)):raise RuntimeError('名称格式不正确')
    conn=mysql.connector.connect(host=host,port=int(cfg.get('MYSQL_PORT',3306)),user='root',password=cfg.get('MYSQL_DEV_ROOT_PASSWORD') or os.environ['MYSQL_DEV_ROOT_PASSWORD'],autocommit=True,use_pure=True)
    try:
        cur=conn.cursor();cur.execute(f'CREATE DATABASE IF NOT EXISTS `{audit}`');cur.execute(f'USE `{audit}`');cur.execute(SQL_AUDIT_DDL)
        cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='sql_operations' AND COLUMN_NAME='actor'",(audit,))
        if not cur.fetchone()[0]:cur.execute("ALTER TABLE sql_operations ADD COLUMN actor VARCHAR(80) NOT NULL DEFAULT 'local_shared'")
        cur.execute('SELECT COUNT(*) FROM mysql.user WHERE User=%s AND Host=%s',(user,'%'))
        if cur.fetchone()[0] and not cfg.get('MYSQL_CONSOLE_PASSWORD'):raise RuntimeError('账号已存在但密码未配置')
        cur.execute('CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s',(user,'%',password))
        cur.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,DROP,INDEX,REFERENCES ON `{db}`.* TO %s@%s',(user,'%'))
        cur.execute(f'GRANT SELECT,INSERT,UPDATE ON `{audit}`.`sql_operations` TO %s@%s',(user,'%'))
    finally:conn.close()
    for k,v in {'MYSQL_CONSOLE_ENABLED':'1','MYSQL_CONSOLE_USER':user,'MYSQL_CONSOLE_PASSWORD':password}.items():set_key(str(ROOT/'.env'),k,v)
    (ROOT/'.env').chmod(0o600)
    print('本机SQL控制台已配置；权限限定当前业务库，查询/导入/编辑账号权限保持不变。')

if __name__=='__main__':provision()
