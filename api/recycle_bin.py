"""Thirty-day soft deletion; preserve files in place until explicit/expired purge."""
import shutil
from datetime import datetime, timedelta, timezone

from api import storage, evidence
from utils import path_utils

RETENTION_DAYS = 30


def now():
    return datetime.now(timezone.utc)


def entry(thread_id):
    with storage._LOCK, storage._connect() as conn:
        row = conn.execute('SELECT thread_id,query,status,deleted_at,purge_started_at FROM runs WHERE thread_id=? AND deleted_at IS NOT NULL', (thread_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['expires_at'] = (datetime.fromisoformat(row['deleted_at']) + timedelta(days=RETENTION_DAYS)).isoformat()
        return result


def page(limit=50, offset=0):
    with storage._LOCK, storage._connect() as conn:
        rows = conn.execute('SELECT thread_id FROM runs WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC,thread_id LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        total = conn.execute('SELECT COUNT(*) FROM runs WHERE deleted_at IS NOT NULL').fetchone()[0]
        return {'runs': [entry(row['thread_id']) for row in rows], 'total': total, 'retention_days': RETENTION_DAYS}


def move(thread_id):
    with storage._LOCK, storage._connect() as conn:
        row = conn.execute('SELECT status,deleted_at FROM runs WHERE thread_id=?', (thread_id,)).fetchone()
        if row is None:
            raise KeyError(thread_id)
        if row['status'] == 'running':
            raise ValueError('请先终止运行中的任务')
        conn.execute('UPDATE runs SET deleted_at=? WHERE thread_id=? AND deleted_at IS NULL', (now().isoformat(), thread_id))
    return entry(thread_id)


def restore(thread_id):
    with storage._LOCK:
        item = entry(thread_id)
        if item is None:
            raise KeyError(thread_id)
        if item['purge_started_at']:
            raise ValueError('会话已开始永久清理，无法保证文件完整，请重试永久删除')
        if datetime.fromisoformat(item['expires_at']) <= now():
            raise ValueError('会话已超过30天保留期，无法恢复')
        with storage._connect() as conn:
            conn.execute('UPDATE runs SET deleted_at=NULL WHERE thread_id=?', (thread_id,))


def purge(thread_id):
    with storage._LOCK:
        if entry(thread_id) is None:
            raise KeyError(thread_id)
        # Validate both directories before deleting either. Never follow session symlinks.
        path_utils.validate_thread_id(thread_id)
        directories = [root / f'session_{thread_id}' for root in (path_utils.UPLOAD_DIR, path_utils.OUTPUT_DIR)]
        if any(directory.is_symlink() for directory in directories):
            raise ValueError('会话目录不能是符号链接')
        with storage._connect() as conn:
            conn.execute('UPDATE runs SET purge_started_at=COALESCE(purge_started_at,?) WHERE thread_id=?', (now().isoformat(), thread_id))
        for directory in directories:
            if directory.exists():
                shutil.rmtree(directory)
        # On filesystem errors retain the DB record for a retry; never report full cleanup.
        storage.delete_run(thread_id)
        evidence.clear(thread_id)


def purge_expired():
    cutoff = (now() - timedelta(days=RETENTION_DAYS)).isoformat()
    with storage._LOCK, storage._connect() as conn:
        ids = [row[0] for row in conn.execute('SELECT thread_id FROM runs WHERE deleted_at IS NOT NULL AND (deleted_at<=? OR purge_started_at IS NOT NULL)', (cutoff,))]
    failures = []
    for thread_id in ids:
        try:
            # Recheck under the lock: a user may have restored it since the scan.
            with storage._LOCK:
                item = entry(thread_id)
                if item and (item['purge_started_at'] or datetime.fromisoformat(item['expires_at']) <= now()):
                    purge(thread_id)
        except (OSError, ValueError, KeyError):
            failures.append(thread_id)
    return failures
