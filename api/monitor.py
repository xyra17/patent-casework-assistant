"""事件上报 + 连接管理（TDD §4.1 / §8.2）。"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from langchain_core.callbacks import AsyncCallbackHandler

from api import context, evidence, storage
from tools.chart_utils import build_chart_spec


def build_event(event: str, message: str, data: dict[str, Any] | None = None) -> dict:
    """按 TDD §8.2 组装统一推送结构。"""
    return {
        "type": "monitor_event",
        "event": event,
        "message": message,
        "data": data or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class ConnectionManager:
    """Persist once, then enqueue independently for each bounded WebSocket sender."""

    def __init__(self, queue_size: int = 256, send_timeout: float = 5.0) -> None:
        self._connections = {}
        self._queues = {}
        self._workers = {}
        self._queue_size = queue_size
        self._send_timeout = send_timeout

    async def connect(self, thread_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.setdefault(thread_id, []).append(ws)
        self._queues[ws] = asyncio.Queue(maxsize=self._queue_size)
        self._workers[ws] = asyncio.create_task(self._sender(thread_id, ws, self._queues[ws]))

    def disconnect(self, thread_id: str, ws: WebSocket) -> None:
        conns = self._connections.get(thread_id, [])
        if ws in conns:
            conns.remove(ws)
        if not conns:
            self._connections.pop(thread_id, None)
        self._queues.pop(ws, None)
        worker = self._workers.pop(ws, None)
        if worker and worker is not asyncio.current_task():
            worker.cancel()

    async def _close(self, ws):
        try:
            await asyncio.wait_for(ws.close(code=1013), timeout=self._send_timeout)
        except Exception:
            pass

    async def _sender(self, thread_id, ws, queue):
        try:
            while True:
                text = await queue.get()
                try:
                    await asyncio.wait_for(ws.send_text(text), timeout=self._send_timeout)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.disconnect(thread_id, ws)
            await self._close(ws)

    async def send_text(self, ws: WebSocket, text: str) -> None:
        queue = self._queues.get(ws)
        if queue is None:
            return
        try:
            queue.put_nowait(text)
        except asyncio.QueueFull:
            # Closing with 1013 triggers the existing REST reconciliation on reconnect.
            for thread_id, conns in list(self._connections.items()):
                if ws in conns:
                    self.disconnect(thread_id, ws)
                    break
            asyncio.create_task(self._close(ws))

    async def drain(self):
        """Wait for healthy senders in tests/shutdown, never on the task producer path."""
        await asyncio.wait_for(asyncio.gather(*(q.join() for q in self._queues.values())),
                               timeout=self._send_timeout * 2)

    async def shutdown(self):
        workers = list(self._workers.values())
        sockets = list(self._queues)
        for thread_id, conns in list(self._connections.items()):
            for ws in list(conns):
                self.disconnect(thread_id, ws)
        await asyncio.gather(*workers, return_exceptions=True)
        await asyncio.gather(*(self._close(ws) for ws in sockets))

    async def send_event(self, thread_id: str, event: str, message: str, data: dict | None = None) -> None:
        payload = build_event(event, message, data)
        storage.append_event(thread_id, payload)
        text = json.dumps(payload, ensure_ascii=True)
        for ws in list(self._connections.get(thread_id, [])):
            await self.send_text(ws, text)


class ToolMonitor:
    """事件上报单例（TDD §4.1）。"""

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager

    async def report(
        self, thread_id: str, event: str, message: str, data: dict | None = None
    ) -> None:
        await self._manager.send_event(thread_id, event, message, data)


# 模块级单例：全项目共享同一份连接管理与事件上报
manager = ConnectionManager()
monitor = ToolMonitor(manager)


def _coerce(value: str):
    text = value.strip()
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _record_tool_evidence(thread_id: str, tool_name: str, args: dict, text: str) -> None:
    """兜底解析工具输出：同步工具可能在线程池中丢失 ContextVar，无法直接登记。"""
    if not thread_id or not text or "[证据 S" in text[:200] or (tool_name == "search_knowledge_base" and "[证据 S" in text):
        return
    if any(marker in text for marker in ("连接数据库失败", "数据库查询失败", "语义查询失败", "语义下钻失败", "网络检索失败", "知识库检索失败", "禁止执行", "不支持的文件类型：", "文件读取失败：", "错误：文件不存在")):
        return
    if tool_name in ("execute_sql_query", "get_table_data"):
        try:
            if text.startswith('结果范围：'):
                lines = text.splitlines()
                meta = json.loads(lines[0].removeprefix('结果范围：'))
                values = [json.loads(line) for line in lines[1:] if line.startswith('[')]
                if not values:
                    return
                columns, data = values[0], values[1:]
                meta.update(evidence_rows=len(data), evidence_truncated=len(data) < meta.get('returned_rows', len(data)),
                            evidence_cells_truncated=meta.get('model_cells_truncated', False))
                evidence.add(thread_id, {'type': 'database', 'title': '查询输出回收（仅模型可见部分）',
                    'columns': columns, 'rows': data, 'rowCount': meta.get('returned_rows'),
                    'completeness': meta, 'sql': args.get('sql', ''), 'snippet': text[:1200]})
                return
            reader = csv.reader(io.StringIO(text))
            rows = [row for row in reader if row]
            if len(rows) < 2:
                return
            columns, data = rows[0], rows[1:]
            normalized = [[_coerce(cell) if cell.strip() != "" else None for cell in row] for row in data]
            chart = (
                build_chart_spec(columns, normalized, "SQL 查询结果")
                if tool_name == "execute_sql_query" else None
            )
            title = "SQL 查询结果" if tool_name == "execute_sql_query" else f"表预览：{args.get('table_name', '')}"
            evidence.add(thread_id, {
                "type": "database",
                "title": title,
                "sql": args.get("sql", "") if tool_name == "execute_sql_query" else "",
                "columns": columns,
                "rows": [[str(c) if not isinstance(c, (int, float)) else c for c in row] for row in normalized[:20]],
                "rowCount": len(data),
                "snippet": text[:1200],
                "chart": chart,
            })
        except Exception:  # noqa: BLE001
            return
    elif tool_name == "list_sql_tables":
        evidence.add(thread_id, {
            "type": "database",
            "title": "数据库表清单",
            "snippet": text[:1200],
        })
    elif tool_name == "internet_search":
        for block in re.split(r"\n\s*\n", text):
            match = re.search(r"\[(\d+)\]\s*(.*?)\r?\nURL:\s*(\S+)\r?\n内容:\s*(.*)", block, re.S)
            if match:
                evidence.add(thread_id, {
                    "type": "web",
                    "title": match.group(2).strip() or f"网页结果 {match.group(1)}",
                    "url": match.group(3).strip(),
                    "snippet": match.group(4).strip()[:1200],
                })
    elif tool_name == "search_knowledge_base":
        for block in re.split(r"\n\s*\n", text):
            match = re.search(r"\[来源:\s*(.*?)\]\s*\n(.*)", block, re.S)
            if match:
                evidence.add(thread_id, {
                    "type": "knowledge",
                    "title": match.group(1).strip() or "知识库片段",
                    "snippet": match.group(2).strip()[:1200],
                })
    elif tool_name == "read_file_content":
        evidence.add(thread_id, {
            "type": "file",
            "title": str(args.get("filename", "上传文件")),
            "snippet": text[:1200],
        })


class MonitorCallbackHandler(AsyncCallbackHandler):
    """把主智能体的工具调用事件实时上报（TDD §8.2）。

    - 工具名为 ``task`` → 判定为子智能体调度（assistant_call）
    - 其他工具 → 普通工具调用（tool_start）
    """

    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id
        self.called_sources: set[str] = set()
        self._source_runs: dict[str, str] = {}
        self._tool_names: dict[str, str] = {}
        self._tool_args: dict[str, dict] = {}
        self._parents: dict[str, str | None] = {}
        self._delegations: dict[str, str] = {}
        self._owners: dict[str, dict] = {}
        self._models: dict[str, dict] = {}
        self._round = context.get_round()

    def _link(self, run_id, parent_run_id) -> None:
        self._parents[str(run_id)] = str(parent_run_id) if parent_run_id is not None else None

    def _owner(self, parent_run_id) -> dict:
        cursor = str(parent_run_id) if parent_run_id is not None else None
        seen = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            if cursor in self._delegations:
                return {'agent_name': self._delegations[cursor], 'agent_run_id': cursor,
                        'round': self._round}
            cursor = self._parents.get(cursor)
        return {'agent_name': '主智能体', 'agent_run_id': f'main:{self._thread_id}:{self._round}',
                'round': self._round}

    async def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        # 仅保留运行父链，不把图框架的内部节点逐个写入用户日志。
        self._link(run_id, parent_run_id)

    async def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self._link(run_id, parent_run_id)
        owner = self._owner(parent_run_id)
        self._models[str(run_id)] = owner
        await monitor.report(self._thread_id, 'agent_activity', '智能体开始处理', {
            **owner, 'run_id': str(run_id), 'parent_run_id': str(parent_run_id) if parent_run_id else None,
            'phase': 'start',
        })

    async def on_llm_end(self, response, *, run_id, **kwargs):
        owner = self._models.pop(str(run_id), None)
        if owner:
            await monitor.report(self._thread_id, 'agent_activity', '智能体本次处理结束', {
                **owner, 'run_id': str(run_id), 'phase': 'end',
            })

    async def on_llm_error(self, error, *, run_id, **kwargs):
        owner = self._models.pop(str(run_id), None)
        if owner:
            await monitor.report(self._thread_id, 'agent_activity', '智能体本次处理失败', {
                **owner, 'run_id': str(run_id), 'phase': 'error', 'error': str(error)[:500],
            })

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._link(run_id, parent_run_id)
        owner = self._owner(parent_run_id)
        self._owners[str(run_id)] = owner
        tool_name = (serialized or {}).get("name", "")
        self._tool_names[str(run_id)] = tool_name
        source = {"internet_search": "web_search", "list_sql_tables": "db_query",
                  "get_table_data": "db_query", "execute_sql_query": "db_query",
                  "semantic_query": "db_query", "semantic_drilldown": "db_query",
                  "search_knowledge_base": "knowledge", "read_knowledge_document": "knowledge",
                  "query_department_cases":"db_query","search_case_archive":"knowledge","read_case_archive_document":"knowledge"}.get(tool_name)
        if source:
            self._source_runs[str(run_id)] = source
        args = inputs if isinstance(inputs, dict) else {}
        if not args and input_str:
            try:
                parsed = json.loads(input_str)
                if isinstance(parsed, dict):
                    args = parsed
            except (TypeError, ValueError):
                pass
        self._tool_args[str(run_id)] = args if isinstance(args, dict) else {}
        if tool_name == "task":
            subtype = args.get("subagent_type", "子智能体")
            self._delegations[str(run_id)] = subtype
            await monitor.report(
                self._thread_id,
                "assistant_call",
                f"调度子智能体：{subtype}",
                {
                    **owner,
                    "parent_run_id": str(parent_run_id) if parent_run_id else None,
                    "assistant_name": subtype,
                    "args": args.get("description", ""),
                    "run_id": str(run_id),
                },
            )
        else:
            await monitor.report(
                self._thread_id,
                "tool_start",
                f"调用工具：{tool_name}",
                {**owner, "parent_run_id": str(parent_run_id) if parent_run_id else None,
                 "tool_name": tool_name, "args": args, "run_id": str(run_id)},
            )

    async def on_tool_end(self, output: Any, *, run_id: Any = None, **kwargs: Any) -> None:
        run_key = str(run_id)
        tool_name = self._tool_names.pop(run_key, "")
        owner = self._owners.pop(run_key, {})
        source = self._source_runs.pop(run_key, None)
        tool_args = self._tool_args.pop(run_key, {})
        text = str(getattr(output, "content", output))
        if tool_name == "task":
            # DeepAgents 的 task 工具返回 Command(update={"messages": [ToolMessage]}),
            # 直接 str() 会把 Python repr 暴露到前端；这里提取子智能体的最终文本。
            update = getattr(output, "update", None)
            messages = update.get("messages") if isinstance(update, dict) else None
            if messages:
                content = getattr(messages[-1], "content", "")
                if isinstance(content, str) and content.strip():
                    text = content
        failures = ("连接数据库失败", "数据库查询失败", "语义查询失败", "语义下钻失败", "网络检索失败", "知识库检索失败", "索引尚未构建", "禁止执行", "知识库中未检索到相关内容", "未检索到相关内容", "本轮网络检索已达到上限",'查询未完成','检索未完成','原文读取未完成','共享预算已耗尽')
        if source and not any(marker in text for marker in failures):
            self.called_sources.add(source)
            if tool_name == 'query_department_cases' and tool_args.get('operation') == 'recommend':
                try:
                    if json.loads(text).get('source_usage') == ['db_query','knowledge']:
                        self.called_sources.add('knowledge')
                except (ValueError,AttributeError):pass
        if tool_name and tool_name != "task":
            _record_tool_evidence(self._thread_id, tool_name, tool_args, text)
        await monitor.report(self._thread_id, "tool_end", "工具执行结束",
                             {**owner, "run_id": run_key, "tool_name": tool_name,
                              "assistant_name": self._delegations.get(run_key), "summary": text[:1000]})

    async def on_tool_error(self, error: BaseException, *, run_id: Any = None, **kwargs: Any) -> None:
        run_key = str(run_id)
        self._source_runs.pop(run_key, None)
        tool_name = self._tool_names.pop(run_key, None)
        self._tool_args.pop(run_key, None)
        owner = self._owners.pop(run_key, {})
        await monitor.report(self._thread_id, "tool_error", str(error), {**owner, "run_id": run_key,
            "tool_name": tool_name, "assistant_name": self._delegations.get(run_key)})
