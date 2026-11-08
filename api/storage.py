"""轻量持久化：SQLite 保存任务、事件和最终结果，支持刷新恢复与回放。

选择 SQLite 而不是业务 MySQL：它属于应用自身状态，不能写入只读业务库；
单进程演示足够，部署时可平滑替换为 Postgres（接口保持相同）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from utils import path_utils

_LOCK = threading.RLock()
_DB_PATH = Path(os.getenv("APP_DB_PATH", str(path_utils.PROJECT_ROOT / "data" / "app.db")))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


@contextmanager
def _connect():
    conn = sqlite3.connect(str(_db_path()), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
              thread_id TEXT PRIMARY KEY,
              query TEXT NOT NULL,
              task_query TEXT NOT NULL,
              require_pdf INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL,
              degraded INTEGER NOT NULL DEFAULT 0,
              result TEXT NOT NULL DEFAULT '',
              plan_json TEXT,
              evidence_json TEXT,
              session_dir TEXT,
              error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS history_sequence (
              id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS execution_history (
              thread_id TEXT NOT NULL, execution_id TEXT NOT NULL, snapshot_json TEXT NOT NULL,
              created_at TEXT NOT NULL, PRIMARY KEY(thread_id, execution_id)
            );
            CREATE TABLE IF NOT EXISTS intent_revisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              thread_id TEXT NOT NULL,
              revised_query TEXT NOT NULL,
              plan_json TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_intent_thread ON intent_revisions(thread_id, id);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              thread_id TEXT NOT NULL,
              seq INTEGER NOT NULL,
              event TEXT NOT NULL,
              message TEXT,
              data_json TEXT,
              timestamp TEXT NOT NULL,
              UNIQUE(thread_id, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_events_thread_seq ON events(thread_id, seq);
            CREATE INDEX IF NOT EXISTS idx_runs_updated ON runs(updated_at DESC);
            CREATE TABLE IF NOT EXISTS evidence_records (
              thread_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
              PRIMARY KEY(thread_id, evidence_id), UNIQUE(thread_id, fingerprint)
            );
            CREATE TABLE IF NOT EXISTS context_artifacts (
              thread_id TEXT NOT NULL, ref TEXT NOT NULL, content TEXT NOT NULL,
              PRIMARY KEY(thread_id, ref)
            );
            CREATE TABLE IF NOT EXISTS context_decisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL,
              agent TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )

        conn.execute('INSERT OR IGNORE INTO history_sequence(thread_id) SELECT thread_id FROM runs ORDER BY rowid')
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if 'execution_id' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN execution_id TEXT")
        if 'stop_reason' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN stop_reason TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE runs SET status='cancelled', stop_reason='user_cancel' WHERE status='failed' AND error='任务已终止'")
        if 'query_result_json' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN query_result_json TEXT")
        if 'rule_context_json' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN rule_context_json TEXT")
        if 'execution_query' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN execution_query TEXT")
        if 'applied_intent_id' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN applied_intent_id INTEGER")
        if 'max_rounds' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN max_rounds INTEGER NOT NULL DEFAULT 3")
        if 'deleted_at' not in columns:
            conn.execute('ALTER TABLE runs ADD COLUMN deleted_at TEXT')
        if 'purge_started_at' not in columns:
            conn.execute('ALTER TABLE runs ADD COLUMN purge_started_at TEXT')
        if 'round_mode' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN round_mode TEXT NOT NULL DEFAULT 'manual'")
        if 'allowed_sources_json' not in columns:
            conn.execute("ALTER TABLE runs ADD COLUMN allowed_sources_json TEXT")
        for column in ('clarification_json', 'clarification_response_json'):
            if column not in columns:
                conn.execute(f'ALTER TABLE runs ADD COLUMN {column} TEXT')


def mark_interrupted() -> None:
    """服务重启后，旧进程里 running 的任务已经不可能继续执行。"""
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE runs SET status='failed', error=?, updated_at=? WHERE status='running'",
            ("服务重启，任务已中断", _now()),
        )


def create_run(thread_id: str, query: str, task_query: str, require_pdf: bool, max_rounds: int = 3, expected_revision_id: int | None = None, rule_context: dict | None = None, *, round_mode: str = 'manual', allowed_sources=None, clarification_answer=None) -> dict:
    from api.task_sources import normalize
    allowed_sources = normalize(allowed_sources)
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM runs WHERE thread_id=?", (thread_id,)).fetchone()
        if existing and existing['deleted_at']:
            raise ValueError('会话已在回收站，请先恢复')
        if existing:
            query, task_query = existing['query'], existing['task_query']
        revision = conn.execute("SELECT id, revised_query FROM intent_revisions WHERE thread_id=? ORDER BY id DESC LIMIT 1", (thread_id,)).fetchone()
        if (revision['id'] if revision else None) != expected_revision_id:
            raise ValueError('任务修订已更新，请刷新会话后重新执行')
        from api.clarification import validate_answer, extend_query
        previous_run = _run_dict(existing) if existing else None
        response = validate_answer(previous_run, clarification_answer, allowed_sources)
        if response and existing['applied_intent_id'] != expected_revision_id:
            raise ValueError('任务修订已变化，请按最新修订重跑后再处理缺口')
        effective_query = task_query
        if revision:
            # A revision is a complete user-authored replacement, not appended to stale restrictions.
            suffix = task_query[len(query):] if task_query.startswith(query) else ''
            effective_query = revision['revised_query'] + suffix
        effective_query = extend_query(effective_query, response, previous_run)
        if existing:
            previous = _run_dict(existing)
            previous['execution_id'] = existing['execution_id'] or 'legacy-' + uuid4().hex
            previous['events'] = [dict(event=ev['event'], message=ev['message'], data=_loads(ev['data_json'], {}), timestamp=ev['timestamp'])
                                  for ev in conn.execute('SELECT * FROM events WHERE thread_id=? ORDER BY seq', (thread_id,))]
            previous['intent_revisions'] = [dict(id=r['id'], revised_query=r['revised_query'], created_at=r['created_at'], plan=_loads(r['plan_json'], None))
                                            for r in conn.execute('SELECT * FROM intent_revisions WHERE thread_id=? ORDER BY id DESC', (thread_id,))]
            conn.execute('INSERT OR IGNORE INTO execution_history VALUES(?,?,?,?)',
                         (thread_id, previous['execution_id'], _dumps(previous), existing['created_at']))
        execution_id = uuid4().hex
        conn.execute('INSERT OR IGNORE INTO history_sequence(thread_id) VALUES(?)', (thread_id,))
        conn.execute(
            """
            INSERT INTO runs(thread_id, query, task_query, require_pdf, max_rounds, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 'running', ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
              query=excluded.query,
              task_query=excluded.task_query,
              require_pdf=excluded.require_pdf,
              max_rounds=excluded.max_rounds,
              status='running',
              degraded=0,
              result='',
              query_result_json=NULL,
              plan_json=NULL,
              evidence_json=NULL,
              session_dir=NULL,
              error='',
              stop_reason='',
              created_at=excluded.created_at,
              updated_at=excluded.updated_at
            """,
            (thread_id, query, task_query, int(require_pdf), max_rounds, now, now),
        )
        conn.execute("UPDATE runs SET applied_intent_id=?, execution_query=?, rule_context_json=?, execution_id=? WHERE thread_id=?", (revision['id'] if revision else None, effective_query, _dumps(rule_context or {}), execution_id, thread_id))
        conn.execute('UPDATE runs SET round_mode=? WHERE thread_id=?', (round_mode, thread_id))
        conn.execute('UPDATE runs SET allowed_sources_json=? WHERE thread_id=?', (_dumps(allowed_sources), thread_id))
        conn.execute('UPDATE runs SET clarification_json=NULL,clarification_response_json=? WHERE thread_id=?', (_dumps(response), thread_id))
        conn.execute("DELETE FROM events WHERE thread_id=?", (thread_id,))
        for table in ("evidence_records", "context_artifacts", "context_decisions"):
            conn.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))
        return {"execution_query": effective_query, "applied_intent_id": revision['id'] if revision else None, "started_at": now, "execution_id": execution_id}


def preview_execution_query(thread_id: str, query: str, task_query: str, expected_revision_id: int | None) -> str:
    """只读预览；任务创建事务仍会复核修订版本。"""
    run=get_run(thread_id)
    if run is None:
        if expected_revision_id is not None: raise ValueError('来源修订已不存在，请刷新会话')
        return task_query
    revisions=run['intent_revisions']
    latest=revisions[0] if revisions else None
    if (latest['id'] if latest else None)!=expected_revision_id:
        raise ValueError('任务修订已更新，请刷新会话后重新执行')
    if not latest: return run['task_query']
    suffix=run['task_query'][len(run['query']):] if run['task_query'].startswith(run['query']) else ''
    return latest['revised_query']+suffix


def save_intent_revision(thread_id: str, revised_query: str, expected_revision_id: int | None) -> dict:
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT plan_json FROM runs WHERE thread_id=?", (thread_id,)).fetchone()
        if run is None:
            raise KeyError(thread_id)
        latest = conn.execute("SELECT id FROM intent_revisions WHERE thread_id=? ORDER BY id DESC LIMIT 1", (thread_id,)).fetchone()
        if (latest['id'] if latest else None) != expected_revision_id:
            raise ValueError("修订记录已更新，请重新加载后再保存")
        cursor = conn.execute("INSERT INTO intent_revisions(thread_id,revised_query,plan_json,created_at) VALUES(?,?,?,?)",
                             (thread_id, revised_query, run['plan_json'], _now()))
        return {"id": cursor.lastrowid, "revised_query": revised_query}


def append_event(thread_id: str, event: dict) -> None:
    """持久化监控事件，并同步 runs 摘要状态。在 DB 锁内分配 seq。"""
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT thread_id, status FROM runs WHERE thread_id=?", (thread_id,)).fetchone()
        if row is None:
            return
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE thread_id=?", (thread_id,)
        ).fetchone()[0]
        name = str(event.get("event", ""))
        if name in ('task_cancelled', 'task_result', 'task_clarification', 'error') and row['status'] != 'running':
            return
        data = event.get("data") or {}
        conn.execute(
            "INSERT INTO events(thread_id, seq, event, message, data_json, timestamp) VALUES(?,?,?,?,?,?)",
            (
                thread_id,
                seq,
                name,
                event.get("message", ""),
                _dumps(data),
                event.get("timestamp", _now()),
            ),
        )
        updates: dict[str, Any] = {"updated_at": _now()}
        if name == "session_created":
            updates["session_dir"] = str(data.get("path", "") or "")
        elif name in ("plan_created", "plan_updated"):
            updates["plan_json"] = _dumps(data)
            rounds = data.get('max_rounds')
            if type(rounds) is int and 1 <= rounds <= 6:
                updates['max_rounds'] = rounds
        elif name == "task_result":
            updates["status"] = "completed"
            updates["degraded"] = int(bool(data.get("degraded")))
            updates["result"] = str(data.get("result", "") or "")
            updates["query_result_json"] = _dumps(data.get("query_result"))
            if data.get("evidence") is not None:
                updates["evidence_json"] = _dumps(data.get("evidence"))
        elif name == 'task_cancelled':
            updates['status'] = 'cancelled'
            updates['stop_reason'] = 'user_cancel'
            updates['error'] = ''
        elif name == 'task_clarification':
            updates.update(status='awaiting_input', stop_reason='awaiting_input', degraded=0,
                           clarification_json=_dumps(data['clarification']), result=data['result'],
                           evidence_json=_dumps(data.get('evidence', [])), query_result_json=None)
        elif name == "error":
            updates['stop_reason'] = data.get('stop_reason', 'execution_error')
            updates["status"] = "failed"
            updates["error"] = str(event.get("message", "") or "任务失败")
        if updates:
            assignments = ", ".join(f"{key}=?" for key in updates)
            conn.execute(
                f"UPDATE runs SET {assignments} WHERE thread_id=?",
                (*updates.values(), thread_id),
            )


def _session_dir(thread_id: str) -> str:
    """返回会话工作目录的当前有效路径。

    DB 里记录的是创建时的绝对路径，项目目录改名或临时目录被清理后即失效；
    而会话目录由 ``thread_id`` 唯一确定（``output/session_{thread_id}``），
    因此统一按 ``thread_id`` 重新推导。目录不存在时返回空串，
    前端据此跳过文件请求，避免越界校验返回 403「拒绝访问」。
    """
    try:
        canonical = path_utils.get_session_dir(thread_id)
    except ValueError:
        return ""
    return str(canonical) if canonical.is_dir() else ""


def _run_dict(row: sqlite3.Row, events: list[dict] | None = None) -> dict:
    run = {
        "thread_id": row["thread_id"],
        "execution_id": row["execution_id"],
        "query": row["query"],
        "task_query": row["task_query"],
        "execution_query": row["execution_query"] or row["task_query"],
        "rule_context": _loads(row['rule_context_json'], {}),
        "require_pdf": bool(row["require_pdf"]),
        "max_rounds": row["max_rounds"],
        "round_mode": row['round_mode'],
        "allowed_sources": _loads(row['allowed_sources_json'], None),
        "clarification": _loads(row['clarification_json'], None),
        "clarification_response": _loads(row['clarification_response_json'], None),
        "applied_intent_id": row["applied_intent_id"],
        "status": row["status"],
        "degraded": bool(row["degraded"]),
        "result": row["result"] or "",
        "query_result": _loads(row["query_result_json"], None),
        "plan": _loads(row["plan_json"], None),
        "evidence": _loads(row["evidence_json"], []),
        "session_dir": _session_dir(row["thread_id"]),
        "error": row["error"] or "",
        "stop_reason": row["stop_reason"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "events": events or [],
    }
    from tools.result_assessment import assess_result
    run['result_assessment'] = assess_result(run)
    return run


def list_runs(limit: int = 50, offset: int = 0) -> list[dict]:
    """Small list records; full result/evidence is loaded only from get_run."""
    with _LOCK, _connect() as conn:
        return _list_runs(conn, limit, offset)


def _list_runs(conn, limit, offset, before=None):
    rows = conn.execute(
        """SELECT h.id AS history_cursor,r.thread_id,r.query,r.require_pdf,r.max_rounds,r.round_mode,r.status,r.degraded,
                  r.created_at,r.updated_at,r.stop_reason,
                  (SELECT COUNT(*) FROM events e WHERE e.thread_id=r.thread_id) AS event_count
           FROM runs r JOIN history_sequence h ON h.thread_id=r.thread_id WHERE r.deleted_at IS NULL AND (? IS NULL OR h.id < ?) ORDER BY h.id DESC LIMIT ? OFFSET ?""",
        (before, before, max(1, min(limit, 200)), max(0, offset)),
    ).fetchall()
    return [dict(row) for row in rows]


def run_page(limit: int = 50, offset: int = 0, tracked_ids: list[str] | None = None, before: int | None = None) -> dict:
    with _LOCK, _connect() as conn:
        conn.execute('BEGIN')
        runs = _list_runs(conn, limit, offset, before)
        stats = dict.fromkeys(('running','completed','degraded','failed','cancelled','awaiting_input'), 0)
        for row in conn.execute('SELECT status,degraded,COUNT(*) AS n FROM runs WHERE deleted_at IS NULL GROUP BY status,degraded'):
            key = 'degraded' if row['status'] == 'completed' and row['degraded'] else row['status']
            if key in stats: stats[key] += row['n']
        total = sum(stats.values())
        ids = list(dict.fromkeys([*(tracked_ids or []), *(run['thread_id'] for run in runs)]))
        tracked = {}
        for start in range(0, len(ids), 500):
            batch = ids[start:start+500]
            for row in conn.execute('SELECT thread_id,status,degraded FROM runs WHERE deleted_at IS NULL AND thread_id IN (' + ','.join('?' for _ in batch) + ')', batch):
                tracked[row['thread_id']] = 'degraded' if row['status'] == 'completed' and row['degraded'] else row['status']
        more = bool(runs) and conn.execute('SELECT 1 FROM runs r JOIN history_sequence h ON h.thread_id=r.thread_id WHERE r.deleted_at IS NULL AND h.id < ? LIMIT 1', (runs[-1]['history_cursor'],)).fetchone() is not None
        return {'next_cursor': runs[-1]['history_cursor'] if more else None, 'tracked': tracked, 'runs': runs, 'total': total, 'stats': stats, 'offset': offset,
                'next_offset': offset + len(runs) if offset + len(runs) < total else None}


def get_run(thread_id: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE thread_id=? AND deleted_at IS NULL", (thread_id,)).fetchone()
        if row is None:
            return None
        events = conn.execute(
            "SELECT event, message, data_json, timestamp FROM events WHERE thread_id=? ORDER BY seq",
            (thread_id,),
        ).fetchall()
        hydrated = [
            {
                "event": ev["event"],
                "message": ev["message"] or "",
                "data": _loads(ev["data_json"], {}),
                "timestamp": ev["timestamp"],
            }
            for ev in events
        ]
        result = _run_dict(row, hydrated)
        result['intent_revisions'] = [
            {"id": r['id'], "revised_query": r['revised_query'], "plan": _loads(r['plan_json'], None), "created_at": r['created_at']}
            for r in conn.execute("SELECT * FROM intent_revisions WHERE thread_id=? ORDER BY id DESC", (thread_id,))
        ]
        return result


def delete_run(thread_id: str) -> None:
    with _LOCK, _connect() as conn:
        for table in ("evidence_records", "context_artifacts", "context_decisions", "intent_revisions", "execution_history", "history_sequence"):
            conn.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))
        conn.execute("DELETE FROM events WHERE thread_id=?", (thread_id,))
        conn.execute("DELETE FROM runs WHERE thread_id=?", (thread_id,))


def save_evidence(thread_id: str, fingerprint: str, item: dict) -> dict:
    """Allocate stable evidence IDs in the same transaction as the durable write."""
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload FROM evidence_records WHERE thread_id=? AND fingerprint=?",
            (thread_id, fingerprint),
        ).fetchone()
        if row:
            return json.loads(row[0])
        count = conn.execute(
            "SELECT COUNT(*) FROM evidence_records WHERE thread_id=?", (thread_id,)
        ).fetchone()[0]
        entry = {**item, "id": f"S{count + 1}"}
        entry.setdefault("created_at", _now())
        conn.execute("INSERT INTO evidence_records VALUES(?, ?, ?, ?)",
                     (thread_id, entry["id"], fingerprint, _dumps(entry)))
        return entry


def load_evidence(thread_id: str, evidence_id: str | None = None) -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT payload FROM evidence_records WHERE thread_id=? "
            "AND (? IS NULL OR evidence_id=?) ORDER BY rowid",
            (thread_id, evidence_id, evidence_id),
        ).fetchall()
        return [json.loads(row[0]) for row in rows]


def reset_evidence(thread_id: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM evidence_records WHERE thread_id=?", (thread_id,))


def save_context_artifact(thread_id: str, ref: str, content: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO context_artifacts VALUES(?, ?, ?)",
                     (thread_id, ref, content))


def load_context_artifact(thread_id: str, ref: str) -> str | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT content FROM context_artifacts WHERE thread_id=? AND ref=?",
                           (thread_id, ref)).fetchone()
        return row[0] if row else None


def record_context_decision(thread_id: str, agent: str, decision: dict) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("INSERT INTO context_decisions(thread_id, agent, payload, created_at) "
                     "VALUES(?, ?, ?, ?)", (thread_id, agent, _dumps(decision), _now()))


def context_decisions(thread_id: str) -> list[dict]:
    with _LOCK, _connect() as conn:
        return [{"agent": row[0], **json.loads(row[1])} for row in conn.execute(
            "SELECT agent, payload FROM context_decisions WHERE thread_id=? ORDER BY id",
            (thread_id,),
        )]


init_db()


def save_query_result(thread_id: str, report: str, started_at: str, value: dict) -> bool:
    """Do not attach an old projection to a rerun, even if its report text happens to match."""
    with _LOCK, _connect() as conn:
        cursor=conn.execute("UPDATE runs SET query_result_json=? WHERE thread_id=? AND result=? AND created_at=? AND status='completed'",(_dumps(value),thread_id,report,started_at))
        return cursor.rowcount==1


def list_executions(thread_id: str, limit=50, offset=0) -> dict:
    current = get_run(thread_id)
    if current is None: raise KeyError(thread_id)
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT execution_id,created_at,json_extract(snapshot_json,'$.status') AS status,json_extract(snapshot_json,'$.degraded') AS degraded FROM execution_history WHERE thread_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (thread_id, limit, offset)).fetchall()
        total = conn.execute('SELECT COUNT(*) FROM execution_history WHERE thread_id=?', (thread_id,)).fetchone()[0]
    return {'current': {'execution_id':current['execution_id'], 'created_at':current['created_at'], 'status':current['status']},
            'executions':[dict(r) for r in rows], 'next_offset': offset + len(rows) if offset + len(rows) < total else None}


def get_execution(thread_id: str, execution_id: str) -> dict | None:
    current = get_run(thread_id)
    if current is not None and current['execution_id'] == execution_id: return current
    with _LOCK, _connect() as conn:
        row = conn.execute('SELECT snapshot_json FROM execution_history WHERE thread_id=? AND execution_id=?', (thread_id, execution_id)).fetchone()
    if row is None: return None
    result = _loads(row[0], {})
    result['session_dir'] = str(path_utils.get_execution_dir(thread_id, execution_id))
    from tools.result_assessment import assess_result
    result['result_assessment'] = assess_result(result)
    return result
