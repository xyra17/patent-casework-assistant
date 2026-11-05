"""Local catalog, immutable parsed revisions, published cases and manual decisions."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from utils.path_utils import PROJECT_ROOT

_READ_CONNECTION=ContextVar('casework_read_connection',default=None)

_INIT_LOCK = threading.RLock()
_INITIALIZED: set[str] = set()
SCHEMA = '''
CREATE TABLE IF NOT EXISTS roots (
 id TEXT PRIMARY KEY, label TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
 classification TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 mapping TEXT NOT NULL DEFAULT '{}', identity TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, generation INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'new', last_scan TEXT, last_error TEXT,
 mirror_state TEXT NOT NULL DEFAULT 'not_published', mirror_generation INTEGER,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
 id TEXT PRIMARY KEY, root_id TEXT NOT NULL, state TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, progress TEXT NOT NULL DEFAULT '{}',
 summary TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS files (
 id TEXT PRIMARY KEY, root_id TEXT NOT NULL, relative_path TEXT NOT NULL,
 case_id TEXT, revision TEXT, parser_key TEXT, kind TEXT, state TEXT NOT NULL,
 error TEXT, size INTEGER NOT NULL DEFAULT 0, mtime REAL,
 active INTEGER NOT NULL DEFAULT 1, generation INTEGER NOT NULL,
 updated_at TEXT NOT NULL, UNIQUE(root_id, relative_path)
);
CREATE INDEX IF NOT EXISTS files_root ON files(root_id, active);
CREATE TABLE IF NOT EXISTS revisions (
 file_id TEXT NOT NULL, revision TEXT NOT NULL, parser_key TEXT NOT NULL,
 payload TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(file_id, revision, parser_key)
);
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT PRIMARY KEY, file_id TEXT NOT NULL, revision TEXT NOT NULL,
 parser_key TEXT NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL,
 location TEXT NOT NULL, tags TEXT NOT NULL, vector TEXT, embedding_key TEXT
);
CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id, revision, parser_key);
CREATE TABLE IF NOT EXISTS cases (
 case_key TEXT PRIMARY KEY, root_id TEXT NOT NULL, case_id TEXT NOT NULL,
 data TEXT NOT NULL, fields TEXT NOT NULL, tags TEXT NOT NULL,
 version TEXT NOT NULL, generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS overrides (
 case_key TEXT NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL,
 source_id TEXT, reason TEXT NOT NULL, base_fingerprint TEXT NOT NULL,
 actor TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(case_key, field)
);
CREATE TABLE IF NOT EXISTS people (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, domains TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
 notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL,
 action TEXT NOT NULL, before_json TEXT, after_json TEXT,
 actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def database_path() -> Path:
    return Path(os.getenv('CASEWORK_DB_PATH', str(PROJECT_ROOT / 'data/department/casework.db'))).expanduser().resolve()


@contextmanager
def db(*, write=False):
    shared=_READ_CONNECTION.get()
    if shared is not None:
        if write:raise RuntimeError('只读案件快照中不能写入')
        yield shared
        return
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA busy_timeout=20000')
    try:
        with _INIT_LOCK:
            if str(path) not in _INITIALIZED:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.executescript(SCHEMA)
                connection.commit()
                _INITIALIZED.add(str(path))
        if write:
            connection.execute('BEGIN IMMEDIATE')
        yield connection
        if write:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def read_snapshot():
    if _READ_CONNECTION.get() is not None:
        yield
        return
    with db() as conn:
        conn.execute('BEGIN')
        conn.execute('SELECT COUNT(*) FROM roots').fetchone()  # establish WAL snapshot
        token=_READ_CONNECTION.set(conn)
        try:yield
        finally:_READ_CONNECTION.reset(token)


def consistent_read(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        with read_snapshot():return function(*args,**kwargs)
    return wrapped


def allowed_roots() -> list[Path]:
    raw = os.getenv('CASEWORK_ALLOWED_ROOTS', '').strip()
    values = json.loads(raw) if raw else [str(PROJECT_ROOT / 'examples/department_shared_drive/source')]
    if not isinstance(values, list) or not 0 <= len(values) <= 20 or not all(isinstance(p, str) for p in values):
        raise ValueError('CASEWORK_ALLOWED_ROOTS须为0–20个本机目录的JSON数组')
    paths = [Path(p).expanduser().resolve() for p in values]
    if any(p == Path(p.anchor) or p == Path.home() for p in paths):
        raise ValueError('共享盘白名单不能设置为磁盘根目录或整个用户目录')
    return paths


def approved_path(value: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink():
        raise ValueError('不接入符号链接目录')
    target = raw.resolve()
    if not any(target.is_relative_to(base) for base in allowed_roots()):
        raise ValueError('目录不在服务端CASEWORK_ALLOWED_ROOTS白名单中')
    for base in allowed_roots():
        if target.is_relative_to(base):
            relative = target.relative_to(base)
            current = base
            for part in relative.parts:
                current /= part
                if current.is_symlink():
                    raise ValueError('目录路径中不允许符号链接')
    return target


def identity(path: Path):
    stat = path.stat()
    if not path.is_dir():
        raise ValueError('接入目标必须是目录')
    return f'{stat.st_dev}:{stat.st_ino}'


def audit(conn, target, action, before, after, actor='本机操作', reason=''):
    conn.execute('INSERT INTO audit(target,action,before_json,after_json,actor,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                 (target, action, dumps(before), dumps(after), actor[:80], reason[:2000], now()))


def roots(*, enabled=False):
    with db() as conn:
        rows = conn.execute('SELECT * FROM roots' + (' WHERE enabled=1' if enabled else '') + ' ORDER BY created_at,id').fetchall()
        return [{**dict(row), 'mapping': json.loads(row['mapping'])} for row in rows]


def get_root(ident):
    with db() as conn:
        row = conn.execute('SELECT * FROM roots WHERE id=?', (ident,)).fetchone()
    if row is None:
        raise KeyError('共享目录不存在')
    return {**dict(row), 'mapping': json.loads(row['mapping'])}


def save_root(label, path, classification='local_only', mapping=None, *, ident=None, expected_version=None, enabled=True, reattach=False):
    target = approved_path(path)
    folder_identity = identity(target)
    if classification not in ('synthetic', 'local_only'):
        raise ValueError('不支持的资料分类')
    if classification == 'synthetic':
        marker = target / '.casework-demo.json'
        if not marker.is_file() or marker.is_symlink() or json.loads(marker.read_text()).get('kind') != 'independently_authored_synthetic':
            raise ValueError('演示目录缺少独立编写模拟资料标记，真实资料必须设为仅限本地')
    mapping = mapping or {}
    if not isinstance(mapping, dict) or len(mapping) > 50 or any(not isinstance(v, list) or len(v) > 20 or not all(isinstance(x,str) and len(x)<=80 for x in v) for v in mapping.values()):
        raise ValueError('字段映射应为字段名到表头别名列表的对象')
    from casework.parser import FIELDS,field_lookup
    if any(k not in FIELDS for k in mapping):raise ValueError('字段映射含未知规范字段')
    field_lookup(mapping)
    with db(write=True) as conn:
        if ident:
            old = conn.execute('SELECT * FROM roots WHERE id=?', (ident,)).fetchone()
            if not old:
                raise KeyError('共享目录不存在')
            if old['version'] != expected_version or old['state'] == 'scanning':
                raise ValueError('目录配置已变化或正在扫描，请刷新后重试')
            if str(target) != old['path']:
                raise ValueError('更换根路径请新增目录，防止历史引用串源')
            if classification != old['classification']:
                raise ValueError('资料分类不能降级或静默改变；请另建明确分类的目录')
            conn.execute("UPDATE roots SET label=?,mapping=?,enabled=?,identity=?,version=version+1,generation=generation+1,state='needs_rescan',mirror_state='pending' WHERE id=?",
                         (label, dumps(mapping), int(enabled), folder_identity if reattach else old['identity'], ident))
            audit(conn, ident, 'root_update', dict(old), {'label':label,'mapping':mapping,'enabled':enabled,'reattach':reattach})
        else:
            # Nested roots would count the same case twice in department statistics.
            for row in conn.execute('SELECT path FROM roots'):
                other = Path(row['path'])
                if target.is_relative_to(other) or other.is_relative_to(target):
                    raise ValueError('接入目录与已有目录重叠，请只保留一个根目录')
            ident = uuid4().hex[:16]
            conn.execute('INSERT INTO roots(id,label,path,classification,mapping,identity,enabled,created_at) VALUES(?,?,?,?,?,?,?,?)',
                         (ident, label, str(target), classification, dumps(mapping), folder_identity, int(enabled), now()))
            if classification == 'local_only':
                conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('private_material_seen','1')")
            audit(conn, ident, 'root_create', None, {'label':label,'path':str(target),'classification':classification})
    return get_root(ident)


def has_private_material():
    path = database_path()
    if not path.is_file():
        return False
    with db() as conn:
        # Disabling a root doesn't remove private revisions or mirrored records.
        return (conn.execute("SELECT 1 FROM roots WHERE classification='local_only' LIMIT 1").fetchone() is not None
                or conn.execute("SELECT 1 FROM settings WHERE key='private_material_seen' AND value='1'").fetchone() is not None)


def list_cases(root_id=None):
    with db() as conn:
        sql = 'SELECT c.* FROM cases c JOIN roots r ON r.id=c.root_id WHERE r.enabled=1'
        args = []
        if root_id:
            sql += ' AND c.root_id=?'; args.append(root_id)
        rows = conn.execute(sql+' ORDER BY c.case_id,c.case_key', args).fetchall()
        return [{**json.loads(row['data']), 'case_key':row['case_key'], 'root_id':row['root_id'],
                 'fields':json.loads(row['fields']), 'tags':json.loads(row['tags']),
                 'version':row['version'], 'generation':row['generation']} for row in rows]


def get_case(case_key):
    return next((c for c in list_cases() if c['case_key']==case_key), None)


def coverage():
    with db() as conn:
        root_list = roots(enabled=True)
        errors = conn.execute("SELECT f.id,f.root_id,f.relative_path,f.state,f.error FROM files f JOIN roots r ON r.id=f.root_id WHERE r.enabled=1 AND f.active=1 AND f.state NOT IN ('parsed','inventory') ORDER BY f.relative_path LIMIT 100").fetchall()
        counts = dict(conn.execute('SELECT f.state,COUNT(*) FROM files f JOIN roots r ON r.id=f.root_id WHERE r.enabled=1 AND f.active=1 GROUP BY f.state').fetchall())
    return {'roots':[{'id':r['id'],'label':r['label'],'state':r['state'],'generation':r['generation'],'last_scan':r['last_scan'],
                      'mirror_state':r['mirror_state'],'mirror_generation':r['mirror_generation']} for r in root_list],
            'file_states':counts,'issues':[dict(row) for row in errors],
            'data_kind':'synthetic' if root_list and all(r['classification']=='synthetic' for r in root_list) else 'local_only',
            'complete_scan':bool(root_list) and all(r['state']=='ready' for r in root_list) and not errors,
            'scope_note':'仅包括已启用目录当前成功解析并发布的材料；未知、冲突和失败项单列，不代表未接入的全部门资料。'}
