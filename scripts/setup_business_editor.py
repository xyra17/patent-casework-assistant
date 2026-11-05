"""Provision the local editor and a separate audit schema, preserving reader/importer grants."""
import os
import re
import secrets
from pathlib import Path
import mysql.connector
from dotenv import dotenv_values,set_key

ROOT=Path(__file__).resolve().parent.parent
AUDIT_DDL='''CREATE TABLE IF NOT EXISTS row_audit (
 operation_id CHAR(36) PRIMARY KEY,payload_hash CHAR(64) NOT NULL,
 business_database VARCHAR(64) NOT NULL,table_name VARCHAR(64) NOT NULL,row_key TEXT NOT NULL,
 operation VARCHAR(12) NOT NULL,actor VARCHAR(80) NOT NULL,reason VARCHAR(500) NOT NULL,
 before_json LONGTEXT NOT NULL,after_json LONGTEXT,result_json LONGTEXT NOT NULL,
 created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
 INDEX table_history(business_database,table_name,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'''


def provision():
    cfg=dotenv_values(ROOT/'.env');host=cfg.get('MYSQL_HOST','localhost')
    if host not in ('localhost','127.0.0.1','::1'):raise RuntimeError('此脚本仅用于本机开发库')
    database=cfg.get('MYSQL_DATABASE','');audit=cfg.get('MYSQL_EDIT_AUDIT_DATABASE','tanyuan_management')
    user=cfg.get('MYSQL_EDIT_USER') or 'tanyuan_editor';password=cfg.get('MYSQL_EDIT_PASSWORD') or secrets.token_urlsafe(32)
    if audit==database or user in {cfg.get('MYSQL_USER'),cfg.get('MYSQL_IMPORT_USER'),'root'}:raise RuntimeError('维护账号、查询/导入账号和审计库必须独立')
    if not all(re.fullmatch(r'[A-Za-z0-9_]{1,64}',v) for v in (database,audit,user)):raise RuntimeError('数据库或用户名格式不正确')
    root_password=cfg.get('MYSQL_DEV_ROOT_PASSWORD') or os.getenv('MYSQL_DEV_ROOT_PASSWORD')
    if not root_password:raise RuntimeError('请在本地.env配置MYSQL_DEV_ROOT_PASSWORD后运行，不会输出密钥')
    conn=mysql.connector.connect(host=host,port=int(cfg.get('MYSQL_PORT',3306)),user='root',password=root_password,autocommit=True,use_pure=True)
    try:
        cur=conn.cursor();cur.execute(f'CREATE DATABASE IF NOT EXISTS `{audit}` CHARACTER SET utf8mb4');cur.execute(f'USE `{audit}`');cur.execute(AUDIT_DDL)
        cur.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='row_audit'",(audit,))
        if cur.fetchone()[0]!='InnoDB':raise RuntimeError('审计表必须使用InnoDB，不能保证原子提交时不启用维护')
        cur.execute('SELECT COUNT(*) FROM mysql.user WHERE User=%s AND Host=%s',(user,'%'));exists=cur.fetchone()[0]
        if exists and not cfg.get('MYSQL_EDIT_PASSWORD'):raise RuntimeError('账号已存在但本地没有配置其密码，不能擅自覆盖')
        cur.execute('CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s',(user,'%',password))
        cur.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON `{database}`.* TO %s@%s',(user,'%'))
        cur.execute(f'GRANT SELECT,INSERT ON `{audit}`.`row_audit` TO %s@%s',(user,'%'))
    finally:conn.close()
    check=mysql.connector.connect(host=host,port=int(cfg.get('MYSQL_PORT',3306)),user=user,password=password,database=database,use_pure=True)
    check.close()
    for key,value in {'MYSQL_EDIT_ENABLED':'1','MYSQL_EDIT_USER':user,'MYSQL_EDIT_PASSWORD':password,'MYSQL_EDIT_AUDIT_DATABASE':audit}.items():set_key(str(ROOT/'.env'),key,value)
    (ROOT/'.env').chmod(0o600)
    print('本机维护账号和事务审计表已配置；未更改查询/导入账号权限；未修改业务记录。')

if __name__=='__main__':provision()
