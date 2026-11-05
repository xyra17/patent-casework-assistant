"""Activate the independently authored department demo on local MySQL.

Keeps previous databases, copies private .env and app DB to a recovery folder.
Does not read real client folders, upload documents or download embedding models.
"""
from __future__ import annotations
import argparse,json,os,secrets,shutil,sqlite3
from datetime import datetime,timezone
from pathlib import Path
from dotenv import dotenv_values,set_key,load_dotenv

ROOT=Path(__file__).resolve().parents[1]
TARGET='department_workbench'

def activate():
    cfg=dotenv_values(ROOT/'.env')
    if cfg.get('MYSQL_HOST') not in ('localhost','127.0.0.1','::1'):raise RuntimeError('Only local development MySQL is supported')
    import mysql.connector
    from casework.mirror import DDL,TABLES
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S');backup=ROOT/'data/scenario-backups'/stamp;backup.mkdir(parents=True,mode=0o700)
    shutil.copy2(ROOT/'.env',backup/'previous.env');(backup/'previous.env').chmod(0o600)
    oldapp=Path(cfg.get('APP_DB_PATH') or ROOT/'data/app.db')
    if oldapp.is_file():
        with sqlite3.connect(oldapp) as source,sqlite3.connect(backup/'previous-app.db') as dest:source.backup(dest)
    connection=mysql.connector.connect(host=cfg['MYSQL_HOST'],port=int(cfg['MYSQL_PORT']),user='root',password=cfg['MYSQL_DEV_ROOT_PASSWORD'],use_pure=True,autocommit=True)
    cur=connection.cursor();cur.execute('CREATE DATABASE IF NOT EXISTS `department_workbench` CHARACTER SET utf8mb4');cur.execute('USE `department_workbench`')
    for statement in DDL:cur.execute(statement)
    cur.execute('''CREATE TABLE IF NOT EXISTS manual_case_notes (note_id BIGINT PRIMARY KEY AUTO_INCREMENT,case_id VARCHAR(100) NOT NULL,
        note_type VARCHAR(100) NOT NULL DEFAULT '业务补充',note_text TEXT NOT NULL,created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX(case_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
    audit='department_management';cur.execute('CREATE DATABASE IF NOT EXISTS `department_management` CHARACTER SET utf8mb4');cur.execute('USE `department_management`')
    from scripts.setup_business_editor import AUDIT_DDL
    from scripts.setup_business_console import SQL_AUDIT_DDL
    cur.execute(AUDIT_DDL);cur.execute(SQL_AUDIT_DDL)
    values={};accounts={}
    for role,key in [('reader','MYSQL'),('importer','MYSQL_IMPORT'),('editor','MYSQL_EDIT'),('console','MYSQL_CONSOLE'),('sync','MYSQL_CASEWORK_SYNC')]:
        user='dept_'+role+'_'+secrets.token_hex(3);password=secrets.token_urlsafe(32)
        cur.execute('CREATE USER %s@%s IDENTIFIED BY %s',(user,'%',password))
        cur.execute('GRANT SELECT ON `department_workbench`.* TO %s@%s',(user,'%'))
        if role=='sync':
            for table in sorted(TABLES):cur.execute('GRANT INSERT,UPDATE,DELETE ON `department_workbench`.`'+table+'` TO %s@%s',(user,'%'))
        if role in ('importer','editor','console'):
            rights='INSERT' if role=='importer' else 'INSERT,UPDATE,DELETE'
            cur.execute('GRANT '+rights+' ON `department_workbench`.`manual_case_notes` TO %s@%s',(user,'%'))
        if role=='editor':cur.execute('GRANT SELECT,INSERT ON `department_management`.`row_audit` TO %s@%s',(user,'%'))
        if role=='console':cur.execute('GRANT SELECT,INSERT,UPDATE ON `department_management`.`sql_operations` TO %s@%s',(user,'%'))
        accounts[role]=user;values[key+'_USER']=user;values[key+'_PASSWORD']=password
    connection.close()
    runtime=ROOT/'data/department';runtime.mkdir(exist_ok=True)
    knowledge=ROOT/'知识库文件'/'90_公司文档'
    # 公司文档从空目录开始，由用户明确接入；不复制公开演示参考。
    knowledge.mkdir(parents=True, exist_ok=True)
    source=(ROOT/'examples/department_shared_drive/source').resolve()
    values.update({'MYSQL_DATABASE':TARGET,'MYSQL_EDIT_ENABLED':'1','MYSQL_CONSOLE_ENABLED':'1','MYSQL_EDIT_AUDIT_DATABASE':audit,
        'SEMANTIC_MODEL_PATH':str(ROOT/'examples/department_shared_drive/semantic.yaml'),
        'KNOWLEDGE_BASE_DIR':str(knowledge),'VECTOR_STORE_DIR':str(runtime/'company-vector-store'),'APP_DB_PATH':str(runtime/'app.db'),
        'IMPORT_STORAGE_DIR':str(runtime/'imports'),'IMPORT_CONNECTIONS_FILE':'','CASEWORK_DB_PATH':str(runtime/'casework.db'),
        'CASEWORK_ALLOWED_ROOTS':json.dumps([str(source)],ensure_ascii=False),'CASEWORK_MYSQL_MIRROR':'1','CASEWORK_SYNC_INTERVAL':'300',
        'CASEWORK_EMBEDDING':'1','DATA_MODE':'demo'})
    for k,v in values.items():set_key(str(ROOT/'.env'),k,v)
    (ROOT/'.env').chmod(0o600);load_dotenv(ROOT/'.env',override=True)
    from casework import store,sync,analytics
    roots=store.roots();root=next((r for r in roots if r['path']==str(source)),None) or store.save_root('代理部门共享盘（独立模拟）',str(source),'synthetic')
    expected=json.loads((ROOT/'examples/department_shared_drive/expected.json').read_text())
    existing={p['name'] for p in analytics.list_people()}
    for p in expected['people']:
        if p['name'] not in existing:analytics.save_person(**p)
    result=sync.run(root['id'])
    report={'activated_at':datetime.now(timezone.utc).isoformat(),'target_database':TARGET,'accounts':accounts,
       'backup':str(backup.relative_to(ROOT)),'scan_state':result['state'],'summary':result['summary'],
       'case_count':len(store.list_cases()),'mirror_state':store.get_root(root['id'])['mirror_state'],
       'real_model_acceptance_completed':False,'data_kind':'independently_authored_synthetic'}
    (runtime/'activation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--activate',action='store_true',required=True);p.parse_args();activate()
