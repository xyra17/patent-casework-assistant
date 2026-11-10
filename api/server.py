"""REST 路由 + WebSocket 端点 + 启动引导（TDD §4.1 / §8）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from typing import Any, Literal

from pydantic import BaseModel, Field

from dotenv import load_dotenv
from utils.path_utils import PROJECT_ROOT
load_dotenv(PROJECT_ROOT / ".env")

from agent.main_agent import run_deep_agent, TaskDeadlineExceeded
from api import evidence, storage
from api import task_sources, clarification, recycle_bin
from api.query_result import build_query_result, business_review_from_run
from api.knowledge import router as knowledge_router
from api.data_import import router as data_import_router
from api.long_term_rules import router as rules_router
from api.business_database import router as business_database_router
from api.business_edit import router as business_edit_router
from api.business_advanced import router as business_advanced_router
from api.casework import router as casework_router
from rules import engine as rule_engine
from tools.semantic_layer import semantic_layer
from tools.chart_utils import build_chart_items, task_needs_charts
from api.monitor import manager, monitor
from utils import path_utils
from utils.logger_utils import get_logger

app = FastAPI(title="Patent Casework Assistant")
app.include_router(knowledge_router)
app.include_router(data_import_router)
app.include_router(rules_router)
app.include_router(business_database_router)
app.include_router(business_edit_router)
app.include_router(business_advanced_router)
app.include_router(casework_router)
@app.middleware("http")
async def fresh_entry_page(request, call_next):
    thread_id = request.query_params.get('thread_id')
    parts = request.url.path.strip('/').split('/')
    if len(parts) >= 3 and parts[:2] in (['api', 'runs'], ['api', 'task']) and request.method != 'DELETE':
        thread_id = parts[2]
    if thread_id and recycle_bin.entry(thread_id):
        return JSONResponse({'detail': '会话已移入回收站，请先恢复'}, status_code=410)
    response = await call_next(request)
    # 首页引用带哈希的构建资源，每次访问都应取得最新入口。
    if request.url.path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.on_event('shutdown')
async def close_monitor_connections():
    await manager.shutdown()


logger = get_logger("tanyuan.api")
_tasks: dict[str, asyncio.Task] = {}
_starting: set[str] = set()
_uploading: set[str] = set()


@app.on_event('startup')
async def recover_interrupted_tasks():
    # Importing the API for tests/maintenance is not a server restart.
    storage.mark_interrupted()
    app.state.recycle_cleanup = asyncio.create_task(clean_recycle_bin())
    from casework.sync import recover_interrupted
    from api.casework import periodic_sync
    recover_interrupted()
    app.state.casework_sync=asyncio.create_task(periodic_sync())


@app.on_event('shutdown')
async def stop_casework_sync():
    cleanup = getattr(app.state, 'recycle_cleanup', None)
    if cleanup:
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
    task=getattr(app.state,'casework_sync',None)
    if task:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


async def clean_recycle_bin():
    while True:
        try:
            failures = await asyncio.to_thread(recycle_bin.purge_expired)
            if failures:
                logger.warning('回收站清理失败，将在下次重试：%s', failures)
        except Exception:
            logger.exception('回收站定期清理失败')
        await asyncio.sleep(3600)


def _validate_thread(thread_id: str) -> None:
    try:
        path_utils.validate_thread_id(thread_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _finish_task(thread_id: str, task: asyncio.Task) -> None:
    """任务结束回调：即使协程在首次执行前就被取消，也能清理 _tasks。

    只清理仍指向同一个 task 的条目，避免旧任务的回调误删同会话新提交的任务。
    """
    if _tasks.get(thread_id) is task:
        _tasks.pop(thread_id, None)


class RuleSkip(BaseModel):
    id: str = Field(min_length=1,max_length=64)
    revision: int = Field(gt=0,strict=True)


from tools.product_boundary import ProductContext


class TaskRequest(BaseModel):
    query: str = Field(min_length=1)
    task_query: str | None = None
    require_pdf: bool = False
    thread_id: str | None = None
    max_rounds: int = Field(default=3, ge=1, le=6, strict=True)
    round_mode: Literal['auto', 'manual'] = 'manual'
    expected_revision_id: int | None = Field(default=None, gt=0, strict=True)
    skip_rule_versions: list[RuleSkip] = Field(default_factory=list,max_length=500)
    allowed_sources: list[Literal['db_query', 'knowledge', 'web_search']] = Field(
        default_factory=lambda: list(task_sources.DEFAULT_SOURCES), max_length=3)
    clarification_answer: clarification.ClarificationAnswer | None = None
    product_context: ProductContext | None = None


@app.get("/api/health")
def root() -> dict:
    return {"status": "ok", "message": "Patent Casework Assistant 服务已启动"}


@app.get('/api/task-sources')
def task_source_options() -> dict:
    from casework.privacy import protected
    blocked = protected()
    configured = bool(os.getenv('TAVILY_API_KEY', '').strip())
    return {'default_sources': list(task_sources.DEFAULT_SOURCES), 'web_search': {
        'available': not blocked and configured,
        'reason': '保密模式禁止联网检索' if blocked else '' if configured else '未配置联网检索服务',
    }}


_status_cache: dict = {"at": 0.0, "data": None}


def _check_model() -> dict:
    """真实探测 OpenAI 兼容 /models；只判断鉴权和连通性，不消耗生成 token。"""
    from casework.privacy import ensure_model_allowed
    try:ensure_model_allowed()
    except PermissionError as exc:return {'ok':False,'state':'blocked','label':'保密边界已阻止外部模型','detail':str(exc)}
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    model = os.getenv("DEEPSEEK_MODEL", "").strip()
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
    if not key or not model:
        return {"ok": False, "state": "unconfigured", "label": "未配置", "detail": "缺少模型或 API Key"}
    request = urllib.request.Request(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            if response.status == 200:
                return {"ok": True, "state": "reachable", "label": "端点可达",
                        "detail": f"{model}；模型列表可读取，尚未验证生成可用性"}
            return {"ok": False, "state": "error", "label": "连接失败", "detail": f"HTTP {response.status}"}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"ok": False, "state": "auth", "label": "鉴权失败", "detail": f"HTTP {exc.code}"}
        return {"ok": False, "state": "error", "label": "连接失败", "detail": f"HTTP {exc.code}"}
    except Exception:  # noqa: BLE001
        return {"ok": False, "state": "error", "label": "连接失败", "detail": "网络不可达或超时"}


@app.get("/api/status")
def system_status() -> dict:
    """启动页系统状态：数据库/知识库/模型做真实探测，网络检索只报告配置状态。"""
    from casework.privacy import protected
    blocked=protected()
    now = time.monotonic()
    if not blocked and _status_cache["data"] is not None and now - _status_cache["at"] < 30:
        return _status_cache["data"]

    from tools.knowledge_base_tool import check_knowledge_base_status, knowledge_base_lock_stats
    from tools.mysql_tools import check_mysql_connection

    db_ok, db_label = check_mysql_connection()
    from casework.archive import knowledge_status
    internal_knowledge = knowledge_status(check_knowledge_base_status())
    data = {
        "database": {
            "ok": db_ok,
            "state": "ok" if db_ok else "error",
            "label": db_label,
            "detail": "MySQL 业务数据库",
        },
        "knowledge_base": {
            **internal_knowledge,
            "lock": knowledge_base_lock_stats(),
        },
        "model": _check_model(),
        "web_search": {
            "ok": bool(os.getenv("TAVILY_API_KEY", "").strip()),
            "state": "configured" if os.getenv("TAVILY_API_KEY", "").strip() else "unconfigured",
            "label": "已配置" if os.getenv("TAVILY_API_KEY", "").strip() else "未配置",
            "detail": "Tavily 检索密钥",
        },
    }
    if blocked:
        data['web_search']={'ok':False,'state':'blocked','label':'保密模式已阻断','detail':'禁止向外部检索发送问题或案卷内容'}
    _status_cache["at"] = now
    _status_cache["data"] = data
    return data


async def _finish_interrupted_review(thread_id: str, reason: str, max_rounds: int) -> None:
    """Persist unresolved per-item outcomes even if execution stops between rounds."""
    from tools.review_tracking import finish_issues
    events = (storage.get_run(thread_id) or {}).get('events', [])
    if any(event['event'] == 'quality_summary' for event in events):
        return
    last = next((event.get('data', {}) for event in reversed(events) if event['event'] == 'reflection'), {})
    await monitor.report(thread_id, 'quality_summary', '任务停止，保留逐项处理记录', {
        'stop_reason': reason, 'rounds_used': last.get('round', 0),
        'max_rounds': last.get('max_rounds', max_rounds), 'passed': False,
        'issues': list(last.get('hard_issues') or []) + ([last['soft_issue']] if last.get('soft_issue') else []),
        'issue_lifecycle': finish_issues(last.get('issue_lifecycle', []), reason),
    })


async def _run_task(thread_id: str, query: str, max_rounds: int = 3) -> None:
    run = storage.get_run(thread_id) or {}
    from tools import product_boundary
    product = next((e.get('data') for e in reversed(run.get('events') or []) if e.get('event') == 'product_boundary_request'), None)
    with task_sources.scope(run.get('allowed_sources')), clarification.scope(run.get('clarification_response')), product_boundary.scope(product):
        await _execute_task(thread_id, query, max_rounds)


async def _execute_task(thread_id: str, query: str, max_rounds: int = 3) -> None:
    """后台执行任务，结束后推送 task_result / error 事件。"""
    rule_token = rule_engine.configure((storage.get_run(thread_id) or {}).get('rule_context', {}))
    try:
        automatic = (storage.get_run(thread_id) or {}).get('round_mode') == 'auto'
        result = await (run_deep_agent(query, thread_id, None) if automatic else run_deep_agent(query, thread_id) if max_rounds == 3
                        else run_deep_agent(query, thread_id, max_rounds))
        # 反思达到上限时编排层会在结果尾部追加【未达标说明】；单独标记，
        # 前端可以把“已完成但未达标”与真正通过质量校验区分开。
        degraded = "【未达标说明】" in result
        fallback_applied = "## 兜底结果说明" in result
        if fallback_applied:
            result = result.replace("## 兜底结果说明", "## 未完成部分")
        if degraded:
            result = re.sub(r"\n*【未达标说明】\s*$", "", result).rstrip()
        # 用完整任务意图重算图表类型（趋势/占比/排名/关系），避免只靠 SQL 形状。
        from tools.chart_utils import build_chart_spec

        evidence_items = evidence.snapshot(thread_id)
        show_charts = task_needs_charts(query)
        for item in evidence_items:
            if not show_charts:
                item.pop('chart', None)
                continue
            if (
                item.get("sql")
                and item.get("columns")
                and item.get("rows")
                and not str(item.get("title", "")).startswith("表预览")
            ):
                match = re.search(r"\bFROM\s+`?([A-Za-z0-9_]+)`?", str(item["sql"]), re.IGNORECASE)
                table_name = match.group(1) if match else None
                field_meta = semantic_layer.annotate_columns(item["columns"], table_name)
                field_meta.update(item.get("semantic") or {})
                item["semantic"] = field_meta
                item["chart"] = build_chart_spec(
                    item["columns"], item["rows"], "SQL 查询结果",
                    query=query, field_meta=field_meta,
                )
                if item["chart"] is not None:
                    item["chart"]["evidenceId"] = item.get("id")
        direct_result = await build_query_result(query, result, evidence_items, degraded,
            business_review=business_review_from_run(storage.get_run(thread_id) or {}))
        from tools.result_assessment import assess_result
        result_assessment = assess_result({**(storage.get_run(thread_id) or {}),
            'status': 'completed', 'result': result, 'degraded': degraded, 'evidence': evidence_items, 'charts': build_chart_items(evidence_items, query)})
        await monitor.report(
            thread_id,
            "task_result",
            "任务完成" if not degraded else "任务完成（存在未达标项）",
            {
                "result": result,
                "query_result": direct_result,
                "result_assessment": result_assessment,
                "degraded": degraded,
                "fallback": fallback_applied,
                "evidence": evidence_items,
                "charts": build_chart_items(evidence_items, query),
            },
        )
    except clarification.ClarificationRequired as exc:
        await monitor.report(thread_id, 'task_clarification', '等待你确认下一步', {
            'clarification':exc.question,'result':exc.report,'evidence':exc.evidence,'charts':[],
        })
    except asyncio.CancelledError:
        logger.info("任务已终止 thread_id=%s", thread_id)
        if (storage.get_run(thread_id) or {}).get('status') == 'running':
            await _finish_interrupted_review(thread_id, 'user_cancel', max_rounds)
            await monitor.report(thread_id, "task_cancelled", "任务已终止", {"stop_reason":"user_cancel"})
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("任务执行失败 thread_id=%s", thread_id)
        reason = 'timeout' if isinstance(e, TaskDeadlineExceeded) else 'execution_error'
        await _finish_interrupted_review(thread_id, reason, max_rounds)
        await monitor.report(thread_id, "error", f"任务失败：{e}", {"stop_reason": reason})
    finally:
        from api.execution_files import write_manifest
        try:
            run = storage.get_run(thread_id)
            if run: await asyncio.to_thread(write_manifest, path_utils.get_session_dir(thread_id), run['status'])
        except (OSError, ValueError):
            logger.exception('执行产物清单保存失败 thread_id=%s', thread_id)
        rule_engine.reset(rule_token)
        # 正常完成与取消都会走到这里；若协程在首次执行前被取消，
        # 由 add_done_callback 的 _finish_task 兜底清理。
        if _tasks.get(thread_id) is asyncio.current_task():
            _tasks.pop(thread_id, None)
        evidence.clear(thread_id)


@app.post("/api/task")
async def submit_task(body: TaskRequest) -> dict:
    """提交任务：创建会话目录、上报 session_created，异步执行并立即返回 thread_id。"""
    if body.product_context:
        catalog = json.loads((path_utils.RESOURCE_ROOT / 'config/session-examples.json').read_text(encoding='utf-8'))
        manual = next((item for item in catalog.get('manual_workflows', [])
                       if item['template_id'] == body.product_context.profile), None)
        if manual:
            raise HTTPException(410, {'code': 'manual_workflow',
                'message': manual['label'] + '已移出自动业务范围，请人工处理。',
                'scenario_id': manual['scenario_id'], 'manual_steps': manual['manual_steps']})
    from casework.privacy import ensure_model_allowed
    try:ensure_model_allowed()
    except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
    thread_id = body.thread_id or uuid4().hex
    _validate_thread(thread_id)
    if recycle_bin.entry(thread_id):
        raise HTTPException(410, '会话已移入回收站，请先恢复')
    if not body.query.strip():
        raise HTTPException(400, "任务不能为空")
    if thread_id in _tasks or thread_id in _starting or thread_id in _uploading:
        raise HTTPException(409, "该会话已有运行中的任务或正在上传文件")
    session_dir = path_utils.get_session_dir(thread_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        execution_query = storage.preview_execution_query(thread_id, body.query, body.task_query or body.query, body.expected_revision_id)
        previous_run = storage.get_run(thread_id)
        response = clarification.validate_answer(previous_run, body.clarification_answer, body.allowed_sources)
        execution_query = clarification.extend_query(execution_query, response, previous_run)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    # Awaiting review must not leave a starting reservation behind on cancel/conflict.
    _starting.add(thread_id)
    try:
        rule_context = await rule_engine.resolve(execution_query,[s.model_dump() for s in body.skip_rule_versions])
        if rule_context['conflicts']:
            raise HTTPException(409, {'code':'rule_conflict','message':'长期规则需核对，可本次跳过冲突规则后继续。','rule_context':rule_context})
        prepared = storage.create_run(
            thread_id, body.query, body.task_query or body.query,
            body.require_pdf, body.max_rounds, body.expected_revision_id, rule_context,
            round_mode=body.round_mode,
            allowed_sources=body.allowed_sources,
            clarification_answer=body.clarification_answer,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    finally:
        _starting.discard(thread_id)
    from api.execution_files import activate
    try:
        session_dir = activate(thread_id, prepared['execution_id'])
    except OSError as exc:
        storage.append_event(thread_id, {'event':'error','message':'无法创建执行目录','data':{'stop_reason':'workspace_error'}})
        raise HTTPException(500, '无法创建执行目录，请检查磁盘空间与权限') from exc
    evidence.reset(thread_id)
    _starting.add(thread_id)
    try:
        await monitor.report(
            thread_id, "session_created", "会话工作目录已创建", {"path": str(session_dir)}
        )
        if body.product_context:
            from tools.product_boundary import request_contract
            await monitor.report(thread_id, 'product_boundary_request', '已绑定本次产品职责契约',
                request_contract(prepared['execution_query'], body.product_context))
        if rule_context.get('candidate_count'):
            await monitor.report(thread_id,'rules_context','已核对本次长期规则',rule_context)
        task = asyncio.create_task(_run_task(thread_id, prepared['execution_query'], body.max_rounds))
        _tasks[thread_id] = task
        task.add_done_callback(lambda t, tid=thread_id: _finish_task(tid, t))
    finally:
        _starting.discard(thread_id)
    return {"status": "started", "thread_id": thread_id, "applied_intent_id": prepared['applied_intent_id'], "started_at": prepared['started_at'], "execution_id":prepared['execution_id'], 'execution_query':prepared['execution_query'], "rule_context":rule_context, 'allowed_sources': task_sources.normalize(body.allowed_sources)}


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str) -> dict:
    _validate_thread(thread_id)
    task = _tasks.get(thread_id)
    if task is None:
        raise HTTPException(404, "没有运行中的任务")
    run = storage.get_run(thread_id) or {}
    if run.get('status') == 'running':
        # Record intent before yielding: even a never-started coroutine or a concurrent
        # new submission must not archive this execution as still running.
        await monitor.report(thread_id, 'task_cancelled', '任务已终止', {'stop_reason':'user_cancel'})
        task.cancel()
    elif run.get('status') == 'completed':
        return {'status':'completed', 'thread_id':thread_id}
    await asyncio.gather(task, return_exceptions=True)
    if _tasks.get(thread_id) is task:
        _tasks.pop(thread_id, None)
    if run.get('execution_id'):
        from api.execution_files import write_manifest
        workspace = path_utils.get_execution_dir(thread_id, run['execution_id'])
        if workspace.is_dir() and not (workspace.parent / 'manifest.json').exists():
            write_manifest(workspace, 'cancelled')
    return {'status':(storage.get_run(thread_id) or {}).get('status', 'cancelled'), 'thread_id':thread_id}


@app.post("/api/upload")
async def upload(
    thread_id: str = Form(...), files: list[UploadFile] = File(...), replace: str = Form("[]")
) -> dict:
    from api.attachments import save_uploads
    _file_directories(thread_id)
    if recycle_bin.entry(thread_id):
        raise HTTPException(410, '会话已移入回收站，请先恢复')
    _require_idle(thread_id)
    _uploading.add(thread_id)
    try:
        return await save_uploads(path_utils.get_upload_dir(thread_id), files, replace)
    finally:
        _uploading.discard(thread_id)


def _file_directories(thread_id: str):
    """删除和附件管理只接受真实会话目录，禁止符号链接跨会话。"""
    _validate_thread(thread_id)
    directories = [root / f"session_{thread_id}" for root in
                   (path_utils.UPLOAD_DIR, path_utils.OUTPUT_DIR)]
    for directory in directories:
        if directory.is_symlink():
            raise HTTPException(403, "会话目录不能是符号链接")
    return directories


def _require_idle(thread_id: str):
    if thread_id in _tasks or thread_id in _starting:
        raise HTTPException(409, "请先终止运行中的任务")
    if thread_id in _uploading:
        raise HTTPException(409, "文件正在上传，请完成后重试")


@app.get("/api/attachments")
async def list_attachments(thread_id: str):
    upload_dir, _ = _file_directories(thread_id)
    if not upload_dir.exists():
        return {"files": []}
    files = []
    for path in upload_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        files.append({"name": path.name, "size": stat.st_size, "mtime": stat.st_mtime})
    return {"files": sorted(files, key=lambda f: f["mtime"], reverse=True)}


@app.delete("/api/attachments")
async def delete_attachment(thread_id: str, filename: str):
    upload_dir, session_dir = _file_directories(thread_id)
    _require_idle(thread_id)
    if not filename or filename in (".", "..") or any(c in filename for c in ("/", "\\", "\x00")):
        raise HTTPException(400, "非法文件名")
    original = upload_dir / filename
    root = path_utils.get_session_root(thread_id)
    copies = [directory / filename for directory in root.glob('executions/*/work/attachments')]
    # Direct/legacy executions also use a dedicated attachments directory now.
    copies.append(root / 'attachments' / filename)
    legacy = root / filename
    if legacy.is_symlink(): raise HTTPException(403, '附件副本不能是符号链接')
    if legacy.is_file() and not legacy.is_symlink() and original.is_file() and legacy.read_bytes() == original.read_bytes():
        copies.append(legacy)
    for path in (original, *copies):
        if path.is_symlink() or not path_utils.is_within(path, upload_dir if path == original else root) or (path.exists() and not path.is_file()):
            raise HTTPException(403, '附件必须是普通文件')
    if not original.exists():
        raise HTTPException(404, '附件不存在或已删除')
    try:
        for copy in copies: copy.unlink(missing_ok=True)
        original.unlink()
    except OSError as exc:
        logger.exception('附件清理失败 thread_id=%s', thread_id)
        raise HTTPException(500, '附件清理失败，请重试') from exc
    return {"status": "deleted", "filename": filename}


@app.get("/api/files")
async def list_files(path: str, thread_id: str, recursive: bool = False):
    """浏览目录：越界校验后返回目录内容（mtime 倒序，TDD §8.1）。"""
    _validate_thread(thread_id)
    target = path_utils.validate_path(path, path_utils.get_session_root(thread_id))
    if target is None:
        return JSONResponse({"error": "拒绝访问"}, status_code=403)
    if not target.exists() or not target.is_dir():
        return JSONResponse({"error": "路径不存在"}, status_code=404)
    entries = []
    candidates = target.rglob("*") if recursive else target.iterdir()
    candidates = [p for p in candidates if path_utils.is_within(p, target) and p.exists()]
    for p in sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True):
        entries.append(
            {
                "name": p.name,
                "type": "dir" if p.is_dir() else "file",
                "path": str(p),
                "size": p.stat().st_size if p.is_file() else None,
                "mtime": p.stat().st_mtime,
            }
        )
    return {"files": entries}


@app.get("/api/download")
async def download(path: str, thread_id: str):
    """下载文件：越界校验后返回文件流（TDD §8.1）。"""
    _validate_thread(thread_id)
    target = path_utils.validate_path(path, path_utils.get_session_root(thread_id))
    if target is None:
        return JSONResponse({"error": "拒绝访问"}, status_code=403)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    return FileResponse(target, filename=target.name)


@app.get("/api/semantic")
def semantic_catalog(refresh: bool = False) -> dict:
    """语义层目录：表/字段角色、指标、关系、层级与图表能力。"""
    if refresh:
        semantic_layer.reload()
    return semantic_layer.snapshot()


class SemanticQueryRequest(BaseModel):
    metric: str | None = None
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 100


class SemanticDrilldownRequest(BaseModel):
    hierarchy: str = Field(min_length=1)
    field: str = Field(min_length=1)
    value: str
    metric: str = Field(min_length=1)
    limit: int = 100


@app.post("/api/semantic/query")
def semantic_query(body: SemanticQueryRequest) -> dict:
    from tools.semantic_query import engine

    try:
        metric_names = list(body.metrics or [])
        if body.metric:
            metric_names.insert(0, body.metric)
        metric_names = list(dict.fromkeys(metric_names))
        if not metric_names:
            raise ValueError("至少选择一个指标")
        from api.business_database import validate_metric_dimensions
        from tools.mysql_tools import describe_schema
        validate_metric_dimensions(metric_names, body.dimensions, describe_schema())
        if len(metric_names) == 1:
            return engine.query(metric_names[0], body.dimensions, body.filters, limit=body.limit)
        return engine.query_multi(metric_names, body.dimensions, body.filters, limit=body.limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/semantic/drilldown")
def semantic_drilldown(body: SemanticDrilldownRequest) -> dict:
    from tools.semantic_query import engine

    try:
        return engine.drilldown(body.hierarchy, body.field, body.value, body.metric, limit=body.limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/runs")
def list_runs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), tracked: str = Query('', max_length=1_300_000), before: int | None = Query(None, gt=0)) -> dict:
    ids = list(dict.fromkeys(tracked.split(','))) if tracked else []
    if len(ids) > 10000: raise HTTPException(400, '一次最多核对 10000 个已加载会话')
    for ident in ids: _validate_thread(ident)
    return storage.run_page(limit, offset, ids, before)


@app.get("/api/runs/{thread_id}")
def get_run(thread_id: str) -> dict:
    _validate_thread(thread_id)
    run = storage.get_run(thread_id)
    if run is None:
        raise HTTPException(404, "任务不存在")
    query = run.get('execution_query') or run.get('task_query') or run.get('query', '')
    run['charts'] = [] if run['status']=='awaiting_input' else build_chart_items(run.get('evidence') or [], query)
    if run['status']=='awaiting_input' or not task_needs_charts(query):
        for item in run.get('evidence') or []:
            item.pop('chart', None)
    return run


_query_result_locks: dict[str, dict] = {}

@app.post('/api/runs/{thread_id}/query-result')
async def prepare_query_result(thread_id: str, retry: bool = False):
    _validate_thread(thread_id)
    entry=_query_result_locks.setdefault(thread_id,{'lock':asyncio.Lock(),'users':0})
    entry['users']+=1
    try:
        async with entry['lock']:
            run=storage.get_run(thread_id)
            if run is None:raise HTTPException(404,'任务不存在')
            if run['status']!='completed':raise HTTPException(409,'任务尚未完成，请等待最终结果')
            business_review = business_review_from_run(run)
            if (run.get('query_result') is not None
                and (run['query_result'].get('statistical_validation_version') == 1
                     or run['query_result'].get('business_validation_version') == 1
                     or not any(item.get('type') == 'database' for item in run.get('evidence', [])))
                and (business_review is None
                     or (run['query_result'].get('business_validation_version') == 1
                         and run['query_result'].get('business_report_sha256') == hashlib.sha256(run['result'].encode()).hexdigest()))
                and (not retry or run['query_result'].get('status')=='ready')):
                return run['query_result']
            value=await build_query_result(run.get('execution_query') or run['task_query'],run['result'],run['evidence'],run['degraded'],
                business_review=business_review)
            if not storage.save_query_result(thread_id,run['result'],run['created_at'],value):
                raise HTTPException(409,'任务已重跑，请刷新后查看本次结果')
            return value
    finally:
        entry['users']-=1
        if not entry['users']:
            _query_result_locks.pop(thread_id,None)


class IntentRevisionRequest(BaseModel):
    revised_query: str = Field(min_length=1, max_length=10000)
    expected_revision_id: int | None = Field(default=None, gt=0, strict=True)


@app.post("/api/runs/{thread_id}/intent-revisions")
def save_intent_revision(thread_id: str, body: IntentRevisionRequest) -> dict:
    _validate_thread(thread_id)
    text = body.revised_query.strip()
    if not text:
        raise HTTPException(400, "修订后的任务不能为空")
    try:
        saved = storage.save_intent_revision(thread_id, text, body.expected_revision_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    run = storage.get_run(thread_id)
    if run is None:
        raise HTTPException(404, "会话已被删除")
    return {**run, "saved_revision_id": saved['id']}


@app.get('/api/runs/{thread_id}/executions')
def execution_history(thread_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    _validate_thread(thread_id)
    try:
        return storage.list_executions(thread_id, limit, offset)
    except KeyError:
        raise HTTPException(404, '任务不存在')


@app.get('/api/runs/{thread_id}/executions/{execution_id}')
def execution_detail(thread_id: str, execution_id: str):
    from api.execution_files import manifest
    _validate_thread(thread_id)
    _validate_thread(execution_id)
    result = storage.get_execution(thread_id, execution_id)
    if result is None: raise HTTPException(404, '执行记录不存在')
    return {**result, 'manifest': manifest(thread_id, execution_id)}


@app.get("/api/runs/{thread_id}/context")
def get_context_diagnostics(thread_id: str) -> dict:
    """Expose policy decisions and provider usage separately from estimates."""
    _validate_thread(thread_id)
    if storage.get_run(thread_id) is None:
        raise HTTPException(404, "任务不存在")
    decisions = storage.context_decisions(thread_id)
    projections = [d for d in decisions if "before_tokens" in d]
    usages = [d["usage"] for d in decisions if d.get("event") == "model_usage"]
    return {
        "thread_id": thread_id,
        "projection_count": len(projections),
        "estimated_input_before": sum(d["before_tokens"] for d in projections),
        "estimated_input_after": sum(d["after_tokens"] for d in projections),
        "over_budget_count": sum(d["over_budget"] for d in projections),
        "provider_usage_calls": len(usages),
        "provider_input_tokens": sum(u["input_tokens"] for u in usages) if usages else None,
        "provider_output_tokens": sum(u["output_tokens"] for u in usages) if usages else None,
        "decisions": decisions,
    }


@app.get("/api/runs/{thread_id}/drilldown")
def drilldown(
    thread_id: str,
    evidence_id: str,
    field: str,
    value: str,
    field2: str | None = None,
    value2: str | None = None,
) -> dict:
    """图表点击下钻：从证据 SQL 解析源表，按维度值读取明细。"""
    _validate_thread(thread_id)
    run = storage.get_run(thread_id)
    if run is None:
        raise HTTPException(404, "任务不存在")
    item = next((e for e in (run.get("evidence") or []) if e.get("id") == evidence_id), None)
    if item is None:
        raise HTTPException(404, "证据不存在")
    sql = str(item.get("sql") or "")
    match = re.search(r"\bFROM\s+`?([A-Za-z0-9_]+)`?", sql, re.IGNORECASE)
    if not match:
        raise HTTPException(400, "当前图表无法定位源表，不能下钻")
    table = match.group(1)
    columns = [str(c) for c in (item.get("columns") or [])]
    if field not in columns:
        raise HTTPException(400, "筛选字段不在查询结果中")
    filters = {field: value}
    if field2 and value2 is not None:
        if field2 not in columns:
            raise HTTPException(400, "第二个筛选字段不在查询结果中")
        filters[field2] = value2
    try:
        from tools.mysql_tools import fetch_table_rows

        data = fetch_table_rows(table, filters, limit=100)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"明细查询失败：{exc}") from exc
    next_level = None
    try:
        hierarchy = semantic_layer.hierarchy_for(table, field)
        if hierarchy:
            next_level = hierarchy.get("next")
    except Exception:  # noqa: BLE001
        next_level = None
    return {**data, "evidenceId": evidence_id, "source_sql": sql, "nextLevel": next_level}


@app.get("/api/runs/{thread_id}/chart-report")
async def chart_report(thread_id: str):
    """把本次任务的 SQL 图表证据导出为独立 PDF（含图表、SQL 与数据表）。"""
    _validate_thread(thread_id)
    _require_idle(thread_id)
    _starting.add(thread_id)
    try:
        run = storage.get_run(thread_id)
        if run is None:
            raise HTTPException(404, '任务不存在')
        from tools.chart_report import build_chart_report
        from api.execution_files import write_manifest
        directory = path_utils.get_session_dir(thread_id)
        target = directory / '图表报告.pdf'
        try:
            await asyncio.to_thread(build_chart_report, run, target)
            await asyncio.to_thread(write_manifest, directory, run['status'])
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return FileResponse(target, filename='图表报告.pdf', media_type='application/pdf')
    finally:
        _starting.discard(thread_id)


@app.delete("/api/runs/{thread_id}")
async def delete_run(thread_id: str) -> dict:
    _file_directories(thread_id)
    _require_idle(thread_id)
    try:
        item = recycle_bin.move(thread_id)
    except KeyError as exc:
        raise HTTPException(404, '会话不存在') from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    evidence.clear(thread_id)
    return {**item, "status": "trashed", "thread_id": thread_id, "files_cleaned": False}


@app.get('/api/trash')
def trash_list(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return recycle_bin.page(limit, offset)


@app.post('/api/trash/{thread_id}/restore')
async def restore_trashed_run(thread_id: str):
    _file_directories(thread_id)
    _require_idle(thread_id)
    try:
        recycle_bin.restore(thread_id)
    except KeyError as exc:
        raise HTTPException(404, '回收站中没有该会话') from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'status': 'restored', 'thread_id': thread_id}


@app.delete('/api/trash/{thread_id}')
async def purge_trashed_run(thread_id: str):
    _file_directories(thread_id)
    _require_idle(thread_id)
    _starting.add(thread_id)
    try:
        await asyncio.to_thread(recycle_bin.purge, thread_id)
    except KeyError as exc:
        raise HTTPException(404, '回收站中没有该会话') from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        logger.exception('回收站文件清理失败 thread_id=%s', thread_id)
        raise HTTPException(500, '文件未完全清理，记录已保留，请重试；部分文件可能已删除') from exc
    finally:
        _starting.discard(thread_id)
    return {'status': 'deleted', 'thread_id': thread_id, 'files_cleaned': True}


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(ws: WebSocket, thread_id: str) -> None:
    """WebSocket 端点：心跳（pong）+ 事件推送。"""
    if recycle_bin.entry(thread_id):
        await ws.close(code=1008, reason='会话已移入回收站')
        return
    try:
        path_utils.validate_thread_id(thread_id)
    except ValueError:
        await ws.close(code=1008)
        return
    await manager.connect(thread_id, ws)
    try:
        while True:
            data = await ws.receive_text()
            await manager.send_text(
                ws,
                json.dumps(
                    {"type": "pong", "message": f"服务端已收到: {data}"},
                    ensure_ascii=True,
                ),
            )
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(thread_id, ws)


# 构建后的前端与 API 同源部署；开发时仍使用 Vite 代理。
_frontend_dist = path_utils.RESOURCE_ROOT / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")


def start_server() -> None:
    """启动服务（默认监听 0.0.0.0:8000，TDD §16.2）。"""
    import uvicorn

    from utils.logger_utils import get_logger

    get_logger("tanyuan").info("正在启动 Patent Casework Assistant 服务：http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
