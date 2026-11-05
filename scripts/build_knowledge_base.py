"""离线构建内部知识库向量索引（TDD §9.2）。

加载配置的公司文档目录 → 分块 → 本地向量化 → 发布公司文档检索索引。
"""
from __future__ import annotations

import os
import json
import hashlib
from datetime import datetime, timezone
import shutil
import sys
import tempfile
from pathlib import Path

# 允许以 `python scripts/build_knowledge_base.py` 直接运行时导入项目内模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from tools.document_parser import PARSER_VERSION, parse_document
from tools.mineru_parser import parser_profile
from tools import knowledge_versions as versions
from utils.logger_utils import get_logger

# 加载项目根目录的 .env
from utils.path_utils import PROJECT_ROOT as _PROJECT_ROOT
load_dotenv(_PROJECT_ROOT / ".env")

logger = get_logger("tanyuan.build")

# 与 tools/knowledge_base_tool.py 保持一致
_COLLECTION_NAME = "tanyuan_kb"
_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 50


def _resolve_path(p: str) -> Path:
    """把 .env 里的（可能是相对的）路径解析为绝对路径。"""
    path = Path(p)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


def _check_wipe_safety(vs_dir: Path) -> None:
    """拦住「清空旧索引」会连带删掉源码的配置。

    build_index() 重建前会 shutil.rmtree(vs_dir)。若 VECTOR_STORE_DIR 被配成项目根
    或是项目根的祖先目录，这一步会连同源码、.env、虚拟环境一起删除，故直接拒绝执行。
    （配置在项目内部或项目之外都是合法的，只拦这两种。）
    """
    if vs_dir == _PROJECT_ROOT:
        raise ValueError(
            f"VECTOR_STORE_DIR 指向了项目根目录，重建索引会删除整个项目，已中止：{vs_dir}\n"
            "请改为项目内的子目录（如 ./data/department/company-vector-store）或项目外的绝对路径。"
        )
    if vs_dir in _PROJECT_ROOT.parents:
        raise ValueError(
            f"VECTOR_STORE_DIR 是项目根目录的上级目录，重建索引会连带删除整个项目，已中止：{vs_dir}\n"
            "请改为项目内的子目录（如 ./data/department/company-vector-store）或项目外的绝对路径。"
        )
    if vs_dir.exists() and any(vs_dir.iterdir()) and not (vs_dir / "chroma.sqlite3").is_file():
        raise ValueError(f"目标不是空目录或已有 Chroma 索引，拒绝覆盖：{vs_dir}")


def _get_embedding() -> HuggingFaceEmbeddings:
    """按 TDD §12 读取 Embedding 模型：优先本地路径，否则用模型名。"""
    model_path = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
    if model_path:
        model_name = str(_resolve_path(model_path))
    else:
        model_name = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
    from utils.embedding_pool import get_embedding
    return get_embedding(model_name, local_only=bool(model_path))


# 扩展名列表同时用于管理界面校验；所有格式统一保留定位元数据。
_LOADERS = {suffix: parse_document for suffix in ('.txt', '.md', '.pdf', '.doc', '.docx', '.xlsx', '.xls', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff')}


def load_documents(kb_dir: Path, strict: bool = False) -> list[Document]:
    """递归加载 kb_dir 下的所有支持文档，来源元数据用文件名。"""
    docs: list[Document] = []
    for path in sorted(kb_dir.rglob("*")):
        if path.is_symlink() or any(part.is_symlink() for part in path.parents if part != kb_dir.parent):
            if strict:
                raise ValueError(f"不支持符号链接文档：{path.name}")
            continue
        if not path.is_file():
            continue
        if path.name == versions.REGISTRY:
            continue
        loader = _LOADERS.get(path.suffix.lower())
        if loader is None:
            logger.warning("跳过不支持的类型: %s", path.name)
            continue
        try:
            units = loader(path, path.relative_to(kb_dir).as_posix())
        except Exception as e:  # noqa: BLE001
            logger.error("解析失败 %s: %s", path.name, e)
            if strict:
                raise ValueError(f"文档解析失败：{path.relative_to(kb_dir)}，请替换为可读取的文件") from e
            continue
        if not units:
            if strict:
                raise ValueError(f"文档没有可提取的文字：{path.relative_to(kb_dir)}；请检查MinerU解析配置；纯附图需视觉分析")
            continue
        docs.extend(units)
        logger.info("加载 %s（%d 个可定位文本单元）", path.name, len(units))
    return docs


def source_inventory(kb_dir: Path) -> dict:
    """文件版本快照；用于判断当前语料是否与已发布索引一致。"""
    sources = {}
    if kb_dir.exists():
        for path in sorted(kb_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _LOADERS:
                continue
            if path.is_symlink():
                continue
            if any(parent.is_symlink() for parent in path.parents if parent != kb_dir.parent):
                continue
            stat = path.stat()
            sources[path.relative_to(kb_dir).as_posix()] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "revision": versions.file_revision(path)}
    return sources


def _close_store(path: Path) -> None:
    # Chroma 按目录缓存 Rust 客户端。替换目录前关闭旧客户端并移除该路径的缓存，
    # 使后续检索真正打开新索引。调用方必须持有检索锁。
    from chromadb.api.shared_system_client import SharedSystemClient
    key = str(path)
    system = SharedSystemClient._identifier_to_system.pop(key, None)
    SharedSystemClient._identifier_to_refcount.pop(key, None)
    if system is not None:
        system.stop()


def build_index(strict: bool = True, allow_empty: bool = False, embedding_factory=None, operation_guard=None) -> int:
    """Prepare off to the side; serialize short Chroma writes and atomic publication only."""
    if operation_guard is None:
        from tools.knowledge_base_tool import _search_guard
        operation_guard = _search_guard
    kb_dir = _resolve_path(os.getenv("KNOWLEDGE_BASE_DIR", "./知识库文件/90_公司文档"))
    vs_dir = _resolve_path(os.getenv("VECTOR_STORE_DIR", "./data/department/company-vector-store"))
    _check_wipe_safety(vs_dir)
    if kb_dir == vs_dir or kb_dir in vs_dir.parents or vs_dir in kb_dir.parents:
        raise ValueError("知识库语料目录与索引目录不能互相包含")
    if not kb_dir.exists():
        if not allow_empty:
            return 0
        kb_dir.mkdir(parents=True)
    sources = source_inventory(kb_dir)
    registry = versions.read_registry(kb_dir)
    policy_digest = versions.registry_digest(registry)
    snapshot = versions.version_snapshot(kb_dir, sources, registry)
    documents = load_documents(kb_dir, strict=strict)
    if not documents and not allow_empty:
        logger.error("未加载到任何文档，构建终止")
        return 0
    splitter = RecursiveCharacterTextSplitter(chunk_size=_CHUNK_SIZE, chunk_overlap=_CHUNK_OVERLAP, add_start_index=True)
    chunks = splitter.split_documents(documents)
    for chunk in chunks:
        identity = json.dumps([chunk.metadata, chunk.page_content], sort_keys=True, ensure_ascii=False)
        chunk.metadata['chunk_id'] = hashlib.sha256(identity.encode()).hexdigest()[:24]
    embedding = (embedding_factory or _get_embedding)() if chunks else None
    vs_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".kb-build-", dir=vs_dir.parent))
    backup = Path(tempfile.mkdtemp(prefix=".kb-old-", dir=vs_dir.parent))
    backup.rmdir()
    committed = False
    try:
        if chunks:
            # The expensive model work runs outside the search lock. Each small staging
            # write still uses the lock because Chroma client initialization is shared.
            class PreparedEmbedding(Embeddings):
                def __init__(self, vectors): self.vectors = vectors
                def embed_documents(self, texts): return self.vectors
                def embed_query(self, text): raise RuntimeError('构建嵌入不可用于查询')
            for start in range(0, len(chunks), 16):
                batch = chunks[start:start + 16]
                vectors = embedding.embed_documents([chunk.page_content for chunk in batch])
                with operation_guard():
                    Chroma.from_documents(batch, PreparedEmbedding(vectors), persist_directory=str(staging),
                                          collection_name=_COLLECTION_NAME)
        else:
            with operation_guard():
                Chroma(persist_directory=str(staging), collection_name=_COLLECTION_NAME)
        with operation_guard():
            _close_store(staging)
        if sources != source_inventory(kb_dir) or policy_digest != versions.registry_digest(versions.read_registry(kb_dir)):
            raise ValueError("构建期间语料发生变化，请重试更新索引")
        (staging / "tanyuan-chunks.json").write_text(json.dumps([
            {'text': chunk.page_content, 'metadata': chunk.metadata} for chunk in chunks
        ], ensure_ascii=False), encoding='utf-8')
        from utils.embedding_pool import embedding_identity
        manifest = {"schema_version": PARSER_VERSION, "document_profile": parser_profile(), "embedding_identity": embedding_identity(), "normalized_embeddings": True, "sources": sources, "chunks": len(chunks),
                    "updated_at": datetime.now(timezone.utc).isoformat(), "policy_digest": policy_digest}
        (staging / versions.SNAPSHOT).write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        (staging / "tanyuan-sources.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        with operation_guard():
            if sources != source_inventory(kb_dir) or policy_digest != versions.registry_digest(versions.read_registry(kb_dir)):
                raise ValueError('构建期间语料发生变化，请重试更新索引')
            _close_store(vs_dir)
            if vs_dir.exists():
                vs_dir.rename(backup)
            try:
                staging.rename(vs_dir)
                committed = True
            except BaseException:
                if backup.exists():
                    backup.rename(vs_dir)
                raise
    finally:
        with operation_guard():
            _close_store(staging)
        if staging.exists():
            shutil.rmtree(staging)
        if committed and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    logger.info("向量索引已持久化到: %s（%d 个分块）", vs_dir, len(chunks))
    return len(chunks)


if __name__ == "__main__":
    count = build_index()
    logger.info("构建完成，共 %d 个分块", count)
