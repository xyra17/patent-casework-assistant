"""任务级证据收集：SQL 行、网页、知识库片段、上传文件。

工具在真实执行时把来源登记到这里，并在返回证据 ID 前同步持久化；
任务结束时随 ``task_result`` 一起提供给前端。
前端“证据”页签据此展示可追溯来源。使用进程级 store（按 thread_id 隔离）
而不是 ContextVar，因为子智能体工具可能在线程池/不同协程中执行。
"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from typing import Any

from api import storage

_LOCK = threading.RLock()
_EVIDENCE: dict[str, list[dict[str, Any]]] = {}
_SEEN: dict[str, dict[str, str]] = {}


def reset(thread_id: str) -> None:
    with _LOCK:
        storage.reset_evidence(thread_id)
        _EVIDENCE[thread_id] = []
        _SEEN[thread_id] = {}


def add(thread_id: str | None, item: dict[str, Any]) -> str:
    """登记一条证据并返回编号（S1、S2...）；无会话上下文时返回空字符串。"""
    if not thread_id:
        return ""
    with _LOCK:
        items = _EVIDENCE.setdefault(thread_id, [])
        seen = _SEEN.setdefault(thread_id, {})
        fingerprint = json.dumps(
            {
                "type": item.get("type"),
                "chunk_id": item.get("chunk_id"),
                "revision": item.get("revision"),
                "applicability": item.get("applicability"),
                "title": item.get("title"),
                "sql": item.get("sql"),
                "params": item.get("params"),
                "completeness": item.get("completeness"),
                "statistics": item.get("statistics"),
                "answer_contract": item.get("answer_contract"),
                "date_completeness": item.get("date_completeness"),
                "table_scope": {key: item.get(key) for key in ("total_rows", "matched_rows", "result_rows", "group_by", "offset", "limit", "truncated")},
                "effective_bindings": item.get("effective_bindings"),
                "url": item.get("url"),
                "snippet": item.get("snippet"),
                "content": item.get("content"),
                "columns": item.get("columns"),
                "rows": item.get("rows"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if fingerprint in seen:
            return seen[fingerprint]
        entry = storage.save_evidence(thread_id, fingerprint, deepcopy(item))
        evidence_id = entry["id"]
        items.append(entry)
        seen[fingerprint] = evidence_id
        return evidence_id


def snapshot(thread_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        return deepcopy(_EVIDENCE.get(thread_id, []))


def clear(thread_id: str) -> None:
    with _LOCK:
        _EVIDENCE.pop(thread_id, None)
        _SEEN.pop(thread_id, None)
