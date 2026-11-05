"""任务内数据源权限与证据缺口；不替代用户身份/数据库授权。"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock

SOURCES = frozenset({'db_query', 'knowledge', 'web_search'})


@dataclass(frozen=True)
class SourcePolicy:
    active: frozenset[str]
    forbidden: frozenset[str]


_policy: ContextVar[SourcePolicy | None] = ContextVar('source_policy', default=None)
_lock = RLock()
_gaps: dict[str, list[dict]] = {}


def configure(active, forbidden=()) -> SourcePolicy:
    from api.task_sources import blocked_sources
    blocked = (frozenset(forbidden) | blocked_sources()) & SOURCES
    current = SourcePolicy(frozenset(active) & (SOURCES - blocked), blocked)
    _policy.set(current)
    return current


def reset_policy() -> None:
    _policy.set(None)


def current_policy() -> SourcePolicy | None:
    return _policy.get()


def record_gap(source: str, missing: str, *, requested_source: str | None = None,
               evidence_ids=(), reason='reported') -> dict | None:
    from api import context
    thread_id = context.get_thread_id()
    if not thread_id or not missing.strip():
        return None
    entry = {'round': context.get_round(), 'source': source,
             'missing': missing.strip()[:1600], 'requested_source': requested_source,
             'evidence_ids': list(dict.fromkeys(evidence_ids))[:24], 'reason': reason}
    with _lock:
        entries = _gaps.setdefault(thread_id, [])
        for old in entries:
            if all(old.get(key) == value for key, value in entry.items()):
                return dict(old)
        # 有界存储；重复与拒绝调用不能造成无限缺口堆积。
        if sum(g['round'] == entry['round'] for g in entries) >= 64:
            return None
        entry['id'] = f'G{len(entries) + 1}'
        entries.append(entry)
    return dict(entry)


def gaps_for_round() -> list[dict]:
    from api import context
    with _lock:
        return [dict(g) for g in _gaps.get(context.get_thread_id(), [])
                if g['round'] == context.get_round()]


def task_gaps() -> list[dict]:
    """供审稿核对的历史观察记录；有新证据不自动等同于缺口已解决。"""
    from api import context
    with _lock:
        return [dict(g) for g in _gaps.get(context.get_thread_id(), [])[-32:]]


def source_denial(source: str) -> str | None:
    from api import context
    if not context.get_thread_id():
        return None  # 无任务上下文的管理接口、健康检查、离线脚本保持原有行为。
    policy = current_policy()
    if policy is not None and source in policy.active:
        return None
    forbidden = policy is not None and source in policy.forbidden
    missing = '用户限制禁止该来源' if forbidden else '当前任务计划未开放该来源'
    record_gap(source, missing, requested_source=source, reason='source_denied')
    return f'禁止执行：{missing}（{source}）。记录缺口交给任务规划层；不得自行换来源或重复尝试。'


def require_source(source: str) -> None:
    denial = source_denial(source)
    if denial:
        raise PermissionError(denial)


def clear_session(thread_id: str | None) -> None:
    _policy.set(None)
    if thread_id:
        with _lock:
            _gaps.pop(thread_id, None)
