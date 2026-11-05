"""会话上下文（TDD §4.1）：ContextVar 会话隔离。

用 ContextVar 保存会话工作目录、线程标识与当前反思轮次，任务生命周期内
set/reset，避免 asyncio 多协程下全局变量串台。

另提供「每轮工具调用预算」：网络检索等工具需要按轮次限次（PRD BR-03），
预算用模块级字典按 ``(thread_id, round)`` 保存，并用线程锁保护，确保同一轮
内多个并发工具调用共享同一份计数。
"""
from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import Optional

_thread_id: ContextVar[Optional[str]] = ContextVar("thread_id", default=None)
_session_dir: ContextVar[Optional[str]] = ContextVar("session_dir", default=None)
_internal_before_web: ContextVar[bool] = ContextVar("internal_before_web", default=False)
_round: ContextVar[Optional[int]] = ContextVar("round_num", default=None)

# 工具预算：key=(thread_id, round)，value={tool_kind: used_count}
_budget_lock = threading.Lock()
_tool_budgets: dict[tuple[str, int], dict[str, int]] = {}


def set_session(thread_id: str, session_dir: str) -> None:
    """设置当前协程的会话上下文。"""
    if _thread_id.get() != thread_id:
        from api import source_policy
        source_policy.reset_policy()
    _internal_before_web.set(False)
    _thread_id.set(thread_id)
    _session_dir.set(session_dir)


def get_thread_id() -> Optional[str]:
    """获取当前会话标识。"""
    return _thread_id.get()


def get_session_dir() -> Optional[str]:
    """获取当前会话工作目录（绝对路径）。"""
    return _session_dir.get()


def set_round(round_num: int) -> None:
    """设置当前反思轮次（1 起），供工具限次与事件打点使用。"""
    _round.set(round_num)


def get_round() -> Optional[int]:
    """获取当前反思轮次。"""
    return _round.get()


def reset_session() -> None:
    """重置当前会话上下文。"""
    from api import source_policy
    from api import clarification
    clarification.clear(_thread_id.get())
    source_policy.clear_session(_thread_id.get())
    _internal_before_web.set(False)
    _thread_id.set(None)
    _session_dir.set(None)
    _round.set(None)


def reset_tool_budget() -> None:
    """清空当前会话、当前轮次的工具预算（每轮开始时调用）。"""
    thread_id = _thread_id.get()
    round_num = _round.get()
    if thread_id is None or round_num is None:
        return
    with _budget_lock:
        _tool_budgets[(thread_id, round_num)] = {}


def consume_tool_budget(kind: str, limit: int) -> bool:
    """尝试消耗一次工具预算。

    返回 ``True`` 表示本次调用在限额内；``False`` 表示本轮该工具已达上限。
    没有会话/轮次上下文时（例如脚本直接调用工具）不做限制，返回 ``True``，
    避免离线评估、单元测试被限次逻辑影响。
    """
    thread_id = _thread_id.get()
    round_num = _round.get()
    if thread_id is None or round_num is None:
        return True
    with _budget_lock:
        counts = _tool_budgets.setdefault((thread_id, round_num), {})
        used = counts.get(kind, 0)
        if used >= limit:
            return False
        counts[kind] = used + 1
        return True


def clear_tool_budgets(thread_id: str) -> None:
    """任务结束时清理该会话的所有轮次预算，避免长期运行内存增长。"""
    with _budget_lock:
        for key in [key for key in _tool_budgets if key[0] == thread_id]:
            _tool_budgets.pop(key, None)


def note_no_progress() -> None:
    thread_id, round_num = get_thread_id(), get_round()
    if thread_id is not None and round_num is not None:
        with _budget_lock:
            counts = _tool_budgets.setdefault((thread_id, round_num), {})
            counts['no_progress'] = counts.get('no_progress', 0) + 1


def finish_with_evidence() -> bool:
    with _budget_lock:
        return _tool_budgets.get((get_thread_id(), get_round()), {}).get('no_progress', 0) >= 3


def configure_comparison_dependency(required: bool) -> None:
    """Only the host sets this from the original task and selected sources."""
    _internal_before_web.set(required)


def internal_before_web() -> bool:
    return _internal_before_web.get()
