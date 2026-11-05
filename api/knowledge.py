"""单实例知识库管理：语料文件与已发布检索索引分开维护。"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from scripts import build_knowledge_base as builder
from tools import knowledge_base_tool as kb
from tools import knowledge_versions as kv
from tools.mineru_parser import parser_profile
from utils.logger_utils import get_logger

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
logger = get_logger("tanyuan.knowledge")
_lock = threading.RLock()
_busy = False
_job = {"state": "idle", "message": "", "finished_at": None}
_build_task: asyncio.Task | None = None
_MAX_BYTES = 20 * 1024 * 1024


def _roots() -> tuple[Path, Path]:
    source = builder._resolve_path(os.getenv("KNOWLEDGE_BASE_DIR", "./知识库文件/90_公司文档"))
    index = builder._resolve_path(os.getenv("VECTOR_STORE_DIR", "./data/department/company-vector-store"))
    if source == builder._PROJECT_ROOT or source in builder._PROJECT_ROOT.parents:
        raise HTTPException(400, "知识库语料目录不能设为项目根目录或其上级目录")
    if source == index or source in index.parents or index in source.parents:
        raise HTTPException(400, "知识库语料目录与索引目录不能互相包含")
    return source, index


def _document(root: Path, name: str) -> Path:
    if not name or "\\" in name or "\x00" in name:
        raise HTTPException(400, "非法文档路径")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(p in (".", "..") for p in name.split("/")):
        raise HTTPException(400, "非法文档路径")
    path = root.joinpath(*relative.parts)
    if path.suffix.lower() not in builder._LOADERS:
        raise HTTPException(400, "不支持的格式，请使用文本、PDF、Word、Excel或图片")
    for current in (path, *path.parents):
        if current == root:
            break
        if current.is_symlink():
            raise HTTPException(403, "不支持符号链接文档")
    if not path.resolve().is_relative_to(root):
        raise HTTPException(403, "文档路径越界")
    return path


def _begin_mutation() -> None:
    global _busy
    with _lock:
        if _busy:
            raise HTTPException(409, "知识库正在更新，请完成后重试")
        _busy = True


def _end_mutation() -> None:
    global _busy
    with _lock:
        _busy = False


def catalog() -> dict:
    source, index = _roots()
    try:
        manifest = json.loads((index / "tanyuan-sources.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    versions = builder.source_inventory(source)
    indexed = manifest.get("sources", {})
    registry = kv.read_registry(source)
    snapshot = kv.version_snapshot(source, versions, registry)
    policy_synced = manifest.get("policy_digest") == kv.registry_digest(registry)
    parser_synced = manifest.get('schema_version') == builder.PARSER_VERSION and manifest.get('document_profile') == parser_profile()
    documents = []
    for name, version in versions.items():
        documents.append({"name": name, "size": version["size"],
                          "mtime": version["mtime_ns"] / 1e9, "revision": snapshot[name]["revision"],
                          "version_info": snapshot[name]["info"], "metadata_token": kv.registry_digest(registry.get(name, {})),
                          "indexed": policy_synced and (index / "chroma.sqlite3").is_file() and indexed.get(name) == version and parser_synced})
    with _lock:
        job, busy = dict(_job), _busy
    exists = (index / "chroma.sqlite3").is_file()
    return {"documents": documents, "busy": busy, "job": job,
            "index_exists": exists, "version_known": bool(manifest), "needs_update": not policy_synced or not exists or not manifest or indexed != versions or not parser_synced,
            "removed_count": len(set(indexed) - set(versions)),
            "chunks": manifest.get("chunks") if exists else None,
            "updated_at": manifest.get("updated_at") if exists else None,
            "supported_extensions": list(builder._LOADERS), "max_file_bytes": _MAX_BYTES}


@router.get("")
def get_catalog():
    return catalog()


@router.post("/documents")
async def upload_documents(files: list[UploadFile] = File(...), replace: str = Form("")):
    source, _ = _roots()
    _begin_mutation()
    try:
        if not files or len(files) > 20:
            raise HTTPException(400, "每次请选择 1–20 个文档")
        if replace and len(files) != 1:
            raise HTTPException(400, "替换时只能选择一个文档")
        targets = []
        for file in files:
            if not file.filename or "/" in file.filename or "\\" in file.filename:
                raise HTTPException(400, "非法文件名")
            _document(source, file.filename)
            target = _document(source, replace or file.filename)
            if replace:
                saved = kv.read_registry(source).get(replace, {})
                if saved.get("info", {}).get("kind") == "regulation" and saved.get("info", {}).get("confirmed"):
                    raise HTTPException(409, "已确认制度请以新文件名添加新版，保留旧版用于历史查询和对比")
                if not target.is_file():
                    raise HTTPException(404, "要替换的文档不存在，请刷新列表")
                if target.suffix.lower() != Path(file.filename).suffix.lower():
                    raise HTTPException(400, "替换文件须与原文档格式一致")
            elif target.exists():
                raise HTTPException(409, f"文档已存在：{file.filename}，请使用列表中的「替换」")
            if target in targets:
                raise HTTPException(400, "本次选择包含重名文件")
            targets.append(target)
        source.mkdir(parents=True, exist_ok=True)
        # 全部文件接收成功后再替换正式语料，避免超限上传损坏原文档。
        with tempfile.TemporaryDirectory(prefix=".kb-upload-", dir=source) as temp:
            staged = []
            for number, file in enumerate(files):
                temporary = Path(temp) / str(number)
                size = 0
                with temporary.open("wb") as out:
                    while chunk := await file.read(1024 * 1024):
                        size += len(chunk)
                        if size > _MAX_BYTES:
                            raise HTTPException(413, "单个文档不能超过 20 MB")
                        out.write(chunk)
                if size == 0:
                    raise HTTPException(400, "不能上传空文件")
                staged.append(temporary)
            for temporary, target in zip(staged, targets):
                os.replace(temporary, target)
        return {"status": "saved", "needs_update": True}
    except OSError as exc:
        logger.exception("知识库文档保存失败")
        raise HTTPException(500, "文档保存失败，请重试") from exc
    finally:
        _end_mutation()


@router.get("/documents/download")
def download_document(name: str):
    source, _ = _roots()
    path = _document(source, name)
    if not path.is_file():
        raise HTTPException(404, "文档不存在")
    return FileResponse(path, filename=path.name)


@router.delete("/documents")
async def delete_document(name: str):
    source, _ = _roots()
    _begin_mutation()
    try:
        path = _document(source, name)
        if not path.is_file():
            raise HTTPException(404, "文档不存在或已删除")
        registry = kv.read_registry(source)
        registry.pop(name, None)
        kv.write_registry(source, registry)
        path.unlink()
        return {"status": "deleted", "needs_update": True}
    except OSError as exc:
        logger.exception("知识库文档删除失败")
        raise HTTPException(500, "文档删除失败，请重试") from exc
    finally:
        _end_mutation()


def _rebuild_sync():
    return builder.build_index(strict=True, allow_empty=True, embedding_factory=kb._get_embedding, operation_guard=kb._search_guard)


async def _rebuild():
    global _job
    try:
        count = await asyncio.to_thread(_rebuild_sync)
        with _lock:
            _job = {"state": "succeeded", "message": f"索引已更新，共 {count} 个知识片段",
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        logger.exception("知识库索引更新失败，保留旧索引")
        with _lock:
            _job = {"state": "failed", "message": str(exc) if isinstance(exc, ValueError)
                    else "索引更新失败，请检查本地模型与服务日志后重试；旧索引仍保留",
                    "finished_at": datetime.now(timezone.utc).isoformat()}
    finally:
        _end_mutation()


@router.post("/rebuild", status_code=202)
async def rebuild_index():
    global _job, _build_task
    _roots()
    _begin_mutation()
    with _lock:
        _job = {"state": "running", "message": "正在构建新索引；可继续检索旧版，仅分批写入和发布切换时短暂等待",
                "finished_at": None}
    _build_task = asyncio.create_task(_rebuild())
    return {"status": "started"}


class VersionRequest(BaseModel):
    expected_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    metadata_token: str
    info: kv.VersionInfo


@router.put("/documents/version")
def save_version(name: str, body: VersionRequest):
    source, _ = _roots()
    _begin_mutation()
    try:
        path = _document(source, name)
        if not path.is_file():
            raise HTTPException(404, "文档不存在")
        registry = kv.read_registry(source)
        if body.expected_revision != kv.file_revision(path) or body.metadata_token != kv.registry_digest(registry.get(name, {})):
            raise HTTPException(409, "文档或版本信息已变化，请刷新后重试")
        info = body.info.model_dump(mode='json')
        text = None
        for rel in body.info.relations:
            target = _document(source, rel.target)
            if rel.target == name or not target.is_file() or kv.file_revision(target) != rel.target_revision:
                raise HTTPException(409, "关联文档不存在、指向自身或版本已变化，请重新选择")
            if body.info.confirmed:
                if text is None:
                    text = ''.join(d.page_content for d in builder.parse_document(path, name))
                if ''.join(rel.basis.split()) not in ''.join(text.split()):
                    raise HTTPException(400, "关系依据须摘录当前文档原文，不能只填写解释")
        registry[name] = {'revision': body.expected_revision, 'info': info}
        kv.write_registry(source, registry)
        return {"status": "saved", "needs_update": True}
    finally:
        _end_mutation()


class SearchRequest(kv.RetrievalScope):
    query: str = Field(min_length=1, max_length=1000)


@router.post("/search")
async def search(body: SearchRequest):
    _, index = _roots()
    if not body.query.strip():
        raise HTTPException(400, "请输入检索问题")
    if not (index / "chroma.sqlite3").is_file():
        raise HTTPException(409, "请先更新索引")
    try:
        scope = kv.RetrievalScope.model_validate(body.model_dump(exclude={'query'}))
        return await asyncio.to_thread(kb._search_sync, body.query.strip(), 4, index, None, scope, structured=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("知识库试用检索失败")
        raise HTTPException(500, "检索失败，请检查索引和本地模型后重试") from exc
