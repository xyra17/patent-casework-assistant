"""SQLite 规则版本；来源会话删除不会级联删除已确认的项目规则。"""
import json
from uuid import uuid4
from api import storage


def init_db():
    with storage._LOCK, storage._connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS long_term_rules (
          id TEXT PRIMARY KEY, revision INTEGER NOT NULL, active INTEGER NOT NULL,
          payload TEXT NOT NULL, source_thread_id TEXT, source_revision_id INTEGER,
          source_snapshot TEXT, source_query TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS long_term_rule_versions (
          rule_id TEXT NOT NULL, revision INTEGER NOT NULL, operation TEXT NOT NULL,
          payload TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(rule_id, revision)
        );
        ''')
        columns={r[1] for r in conn.execute('PRAGMA table_info(long_term_rules)')}
        if 'source_query' not in columns: conn.execute('ALTER TABLE long_term_rules ADD COLUMN source_query TEXT')
        if 'source_turn_id' not in columns: conn.execute('ALTER TABLE long_term_rules ADD COLUMN source_turn_id TEXT')


def _item(row):
    return {**json.loads(row['payload']), 'id':row['id'], 'revision':row['revision'],
            'active':bool(row['active']), 'source_thread_id':row['source_thread_id'],
            'source_turn_id':row['source_turn_id'],
            'source_revision_id':row['source_revision_id'], 'source_snapshot':row['source_snapshot'], 'source_query':row['source_query'],
            'created_at':row['created_at'], 'updated_at':row['updated_at']}


def list_rules(active_only=False):
    init_db()
    with storage._LOCK, storage._connect() as conn:
        return [_item(row) for row in conn.execute('SELECT * FROM long_term_rules '+
          ('WHERE active=1 ' if active_only else '')+'ORDER BY updated_at DESC,id')]


def get_rule(ident):
    init_db()
    with storage._LOCK, storage._connect() as conn:
        row=conn.execute('SELECT * FROM long_term_rules WHERE id=?',(ident,)).fetchone()
        return _item(row) if row else None


def save_rule(payload, *, ident=None, expected_revision=None, source_thread_id=None, source_revision_id=None, source_turn_id=None):
    init_db()
    with storage._LOCK, storage._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        now=storage._now()
        if ident:
            old=conn.execute('SELECT * FROM long_term_rules WHERE id=?',(ident,)).fetchone()
            if old is None: raise KeyError(ident)
            if old['revision'] != expected_revision: raise ValueError('规则版本已更新，请刷新后核对')
            revision=old['revision']+1
            conn.execute('UPDATE long_term_rules SET revision=?,active=?,payload=?,updated_at=? WHERE id=?',
                         (revision,int(payload['active']),storage._dumps(payload),now,ident))
            operation='update'
        else:
            if conn.execute('SELECT COUNT(*) FROM long_term_rules').fetchone()[0] >= 500:
                raise ValueError('项目规则最多500条，请先清理不再使用的规则')
            ident=uuid4().hex;revision=1;operation='create';snapshot=None;source_query=None
            if source_thread_id:
                if source_turn_id:
                    row=conn.execute("SELECT user_text AS revised_query FROM conversation_turns WHERE thread_id=? AND turn_id=? AND status!='running'",
                                     (source_thread_id,source_turn_id)).fetchone()
                else:
                    row=conn.execute('SELECT revised_query FROM intent_revisions WHERE thread_id=? AND id=?',
                                     (source_thread_id,source_revision_id)).fetchone()
                if row is None: raise KeyError('来源修订不存在')
                snapshot=row['revised_query']
                origin=conn.execute('SELECT query FROM runs WHERE thread_id=?',(source_thread_id,)).fetchone()
                source_query=origin['query'] if origin else None
            conn.execute('INSERT INTO long_term_rules(id,revision,active,payload,source_thread_id,source_revision_id,source_snapshot,source_query,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (ident,revision,int(payload['active']),storage._dumps(payload),source_thread_id,source_revision_id,snapshot,source_query,now,now))
            conn.execute('UPDATE long_term_rules SET source_turn_id=? WHERE id=?', (source_turn_id,ident))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='rule_vectors'").fetchone():
            conn.execute('DELETE FROM rule_vectors WHERE rule_id=?',(ident,))
        conn.execute('INSERT INTO long_term_rule_versions VALUES(?,?,?,?,?)',
                     (ident,revision,operation,storage._dumps(payload),now))
        row=conn.execute('SELECT * FROM long_term_rules WHERE id=?',(ident,)).fetchone()
        return _item(row)


def delete_rule(ident, expected_revision):
    init_db()
    with storage._LOCK, storage._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT revision FROM long_term_rules WHERE id=?',(ident,)).fetchone()
        if not row: raise KeyError(ident)
        if row['revision']!=expected_revision: raise ValueError('规则版本已更新，请刷新后核对')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='rule_vectors'").fetchone():
            conn.execute('DELETE FROM rule_vectors WHERE rule_id=?',(ident,))
        conn.execute('DELETE FROM long_term_rule_versions WHERE rule_id=?',(ident,))
        conn.execute('DELETE FROM long_term_rules WHERE id=?',(ident,))


def versions(ident):
    init_db()
    with storage._LOCK, storage._connect() as conn:
        return [{'revision':r['revision'],'operation':r['operation'],'payload':json.loads(r['payload']),
                 'created_at':r['created_at']} for r in conn.execute(
                    'SELECT * FROM long_term_rule_versions WHERE rule_id=? ORDER BY revision DESC',(ident,))]
