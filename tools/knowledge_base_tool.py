"""内部知识库检索工具 search_knowledge_base（TDD §7.3）。

对本地 Chroma/BM25 混合召回并重排，按需有限多跳，返回带定位的原文与覆盖状态。
"""
from __future__ import annotations

import asyncio
import os
import json
import threading
import time
import re
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.tools import tool
from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings

from api import context, evidence, source_policy
from tools.document_parser import location_label, PARSER_VERSION
from tools.hybrid_retrieval import LexicalIndex, fuse_results
from tools.adaptive_retrieval import adaptive_search
from tools import knowledge_versions as versions

# 加载项目根目录的 .env
from utils.path_utils import PROJECT_ROOT as _PROJECT_ROOT
load_dotenv(_PROJECT_ROOT / ".env")

# 与 scripts/build_knowledge_base.py 保持一致
_COLLECTION_NAME = "tanyuan_kb"

# 所有工具调用内部的每一跳共同消耗会话/轮次预算，防止嵌套调用绕过限次。
_KNOWLEDGE_SEARCH_LIMIT = int(os.getenv("KNOWLEDGE_SEARCH_LIMIT", "6"))


def _resolve_path(p: str) -> Path:
    """把 .env 里的（可能是相对的）路径解析为绝对路径。"""
    path = Path(p)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path.resolve()


_embedding = None
_EMBEDDING_LOCK = threading.Lock()


class SerializedEmbedding(Embeddings):
    """Protect shared model calls independently of the index publication lock."""
    def __init__(self, model):
        self.model = model

    def embed_documents(self, texts):
        with _EMBEDDING_LOCK:
            return self.model.embed_documents(texts)

    def embed_query(self, text):
        with _EMBEDDING_LOCK:
            return self.model.embed_query(text)


# Chroma 的 PersistentClient 在并发创建/查询时并不线程安全：实测同一事件循环
# 内两个 asyncio.to_thread 同时检索，会出现 'RustBindingsAPI' object has no
# attribute 'bindings'、tenant 连接失败等错误。这里用进程级可重入锁串行化
# 「模型初始化 + Chroma 打开 + 相似度检索」，保证知识库子智能体并行发起的多个
# 检索请求稳定返回。锁只在工作线程内等待，不会阻塞事件循环。
_SEARCH_LOCK = threading.RLock()
_LOCK_STATS = {
    "serialized_operations": 0,
    "wait_seconds": 0.0,
    "max_wait_seconds": 0.0,
    "in_flight": 0,
}


@contextmanager
def _search_guard():
    """所有 Chroma/Embedding 操作的进程级串行区，并记录排队代价。"""
    started = time.perf_counter()
    with _SEARCH_LOCK:
        waited = time.perf_counter() - started
        _LOCK_STATS["serialized_operations"] += 1
        _LOCK_STATS["wait_seconds"] += waited
        _LOCK_STATS["max_wait_seconds"] = max(_LOCK_STATS["max_wait_seconds"], waited)
        _LOCK_STATS["in_flight"] += 1
        try:
            yield
        finally:
            _LOCK_STATS["in_flight"] -= 1


def knowledge_base_lock_stats() -> dict:
    """返回串行锁的可观测指标，便于判断是否需要升级检索后端。"""
    return {**_LOCK_STATS, "serialized": True}


def check_knowledge_base_status() -> dict:
    """欢迎页状态：真实打开 Chroma collection 并读取片段数，不加载 Embedding 模型。"""
    with _search_guard():
        vs_dir = _resolve_path(os.getenv("VECTOR_STORE_DIR", "./data/department/company-vector-store"))
        if not (vs_dir / "chroma.sqlite3").is_file():
            return {"ok": False, "state": "uninitialized", "label": "未初始化", "detail": "未找到向量库"}
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(vs_dir))
            try:
                collection = client.get_collection(_COLLECTION_NAME)
            except Exception:  # noqa: BLE001
                return {"ok": False, "state": "empty", "label": "未初始化", "detail": "知识库 collection 不存在"}
            count = collection.count()
            if count <= 0:
                return {"ok": False, "state": "empty", "label": "无文档", "detail": "知识库为空"}
            return {"ok": True, "state": "ok", "label": "已就绪", "detail": f"{count} 个知识片段"}
        except Exception:  # noqa: BLE001
            return {"ok": False, "state": "error", "label": "检测失败", "detail": "无法打开知识库"}


def _get_embedding() -> HuggingFaceEmbeddings:
    """按 TDD §12 读取 Embedding 模型：优先本地路径，否则用模型名（懒加载 + 缓存）。"""
    global _embedding
    with _search_guard():
        if _embedding is None:
            model_path = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
            from casework.privacy import protected
            if protected() and (not model_path or not _resolve_path(model_path).is_dir()):
                raise PermissionError('内网模式必须配置本机嵌入模型，不允许下载或调用外部嵌入服务')
            model_name = (
                str(_resolve_path(model_path))
                if model_path
                else os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
            )
            from utils.embedding_pool import get_embedding
            _embedding = get_embedding(model_name, local_only=bool(model_path) or protected())
        return _embedding


_LEXICAL_CACHE = {}


def _retrieve_sync(query: str, k: int, vs_dir: Path, scope=None, audit=None, extra_consume=None) -> list[dict]:
    with _search_guard():
        scope = scope or versions.RetrievalScope()
        resolved = scope.resolved()
        if scope.purpose == 'compare' and len(scope.sources) > 1:
            # Each requested version gets its own candidate pool; one long document cannot crowd out the others.
            pools = []
            for number, name in enumerate(scope.sources):
                if number and extra_consume is not None and not extra_consume():
                    if audit is not None:
                        audit.append('版本对比检索预算耗尽，未检索全部指定版本')
                    break
                pools.append(_retrieve_sync(query, k, vs_dir, scope.model_copy(update={'sources': [name]}), audit))
            balanced = []
            for position in range(k):
                for pool in pools:
                    if position < len(pool):
                        record = pool[position]
                        record['applicability']['scope'] = resolved
                        balanced.append(record)
                        if len(balanced) >= k:
                            return balanced
            return balanced
        policy_file = vs_dir / versions.SNAPSHOT
        if policy_file.exists():
            snapshot = json.loads(policy_file.read_text(encoding='utf-8'))
        else:
            sidecar_path = vs_dir / 'tanyuan-chunks.json'
            legacy = json.loads(sidecar_path.read_text(encoding='utf-8')) if sidecar_path.exists() else []
            snapshot = {r['metadata']['source']: {'revision': r['metadata'].get('revision', ''),
                         'info': versions.VersionInfo().model_dump(mode='json')} for r in legacy}
        eligible, checks, notices = versions.select_versions(snapshot, resolved)
        if audit is not None:
            audit.extend(notices)
        if snapshot and not eligible:
            return []
        where = {'source': {'$in': sorted(eligible)}} if snapshot else None
        store = Chroma(persist_directory=str(vs_dir), embedding_function=_get_embedding(),
                       collection_name=_COLLECTION_NAME)
        manifest_path = vs_dir / 'tanyuan-sources.json'
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        from utils.embedding_pool import embedding_identity
        model_changed=manifest.get('embedding_identity') and manifest['embedding_identity']!=embedding_identity()
        if model_changed:
            vector=[]
            if audit is not None:audit.append('嵌入模型已变更，公司文档需更新索引；本次仅使用词法检索')
        elif manifest and manifest.get('schema_version') not in (2,PARSER_VERSION):
            vector = []  # 旧索引未归一化，不能拿新查询距离冒充可比的相关性。
        elif hasattr(store, 'similarity_search_with_score'):
            vector = [{'text': doc.page_content, 'metadata': doc.metadata, 'vector_distance': float(distance)}
                      for doc, distance in store.similarity_search_with_score(query, k=max(k * 3, 12), **({"filter": where} if where else {}))]
        else:  # 兼容不提供距离的检索实现。
            vector = [{'text': doc.page_content, 'metadata': doc.metadata, 'legacy_vector': True}
                      for doc in store.similarity_search(query, k=k, **({"filter": where} if where else {}))]
        sidecar = vs_dir / 'tanyuan-chunks.json'
        version_file = sidecar if sidecar.exists() else vs_dir / 'chroma.sqlite3'
        stat = version_file.stat()
        key = (str(vs_dir), stat.st_ino, stat.st_mtime_ns, stat.st_size)
        if key not in _LEXICAL_CACHE:
            if sidecar.exists():
                records = json.loads(sidecar.read_text(encoding='utf-8'))
            elif hasattr(store, 'get'):
                raw = store.get(include=['documents', 'metadatas'])
                records = [{'text': text, 'metadata': meta or {}} for text, meta in
                           zip(raw.get('documents', []), raw.get('metadatas', []))]
            else:
                records = vector
            # 同一路径只保留已发布版本；检索仍在串行区，更新不能穿插。
            for old in [old for old in _LEXICAL_CACHE if old[0] == str(vs_dir)]:
                del _LEXICAL_CACHE[old]
            _LEXICAL_CACHE[key] = LexicalIndex(records)
        lexical_index = _LEXICAL_CACHE[key]
        if snapshot:
            lexical_index = LexicalIndex([r for r in lexical_index.records if r.get('metadata', {}).get('source') in eligible])
        lexical = lexical_index.search(query, max(k * 3, 12))
        ranked = fuse_results(query, vector, lexical, k,
                            min_overlap=float(os.getenv('KNOWLEDGE_MIN_LEXICAL_OVERLAP', '.08')),
                            max_distance=float(os.getenv('KNOWLEDGE_MAX_VECTOR_DISTANCE', '.8')))
        for record in ranked:
            record['index_version'] = str(key[1:])
            name = record.get('metadata', {}).get('source')
            record['applicability'] = checks.get(name, {'verified': False, 'scope': resolved,
                'warnings': ['旧索引没有适用信息，请更新索引并确认文档版本']})
            record['applicability']['content_revision'] = record.get('metadata', {}).get('revision')
            if audit is not None:
                audit.extend(record['applicability']['warnings'])
        return ranked


def _register(item, query, hop, thread_id):
    meta = item.get('metadata', {})
    return evidence.add(thread_id, {
        'type': 'knowledge', 'title': str(meta.get('source', '未知文件')), 'query': query,
        'snippet': item['text'], 'chunk_id': item['chunk_id'], 'location': location_label(meta),
        'revision': meta.get('revision'), 'metadata': meta, 'hop': hop, 'index_version':item.get('index_version'),
        'retrieval_score': item['score'], 'channels': item['channels'],
        'applicability': item.get('applicability'),
    }) or item['chunk_id']


def _format_evidence(records):
    return '\n\n'.join(
        f"[证据 {r['evidence_id']}] [来源: {r.get('metadata', {}).get('source', '未知文件')}] "
        f"[{location_label(r.get('metadata', {}))}]\n适用核对：{json.dumps(r.get('applicability', {}), ensure_ascii=False)}\n{r['text']}" for r in records)


def _validate_query_scope(query, scope):
    if scope.purpose == 'current' and not scope.as_of and re.search(r'去年|前年|当时|历史|新旧|旧规|版本对比|20\d{2}年', query):
        raise ValueError('问题涉及历史或版本对比，请明确 history 与业务日期，或 compare 与文档路径')


def _search_sync(query: str, k: int, vs_dir: Path, thread_id: str | None = None, scope=None, structured=False) -> str | dict:
    """管理界面检索试用：只检索一次，不调用对话模型。"""
    scope = scope or versions.RetrievalScope()
    _validate_query_scope(query, scope)
    audit = []
    records = _retrieve_sync(query, max(1, min(k, 8)), vs_dir, scope, audit)
    for record in records:
        record['evidence_id'] = _register(record, query, 1, thread_id)
    text = _format_evidence(records) or '知识库中未检索到相关内容'
    text += ('\n待核实：' + '；'.join(dict.fromkeys(audit)) if audit else '')
    if structured:
        return {'result': text, 'warnings': list(dict.fromkeys(audit)), 'passages': [
            {'evidence_id': r['evidence_id'], 'source': r.get('metadata', {}).get('source', '未知文件'),
             'location': location_label(r.get('metadata', {})), 'text': r['text'],
             'revision': r.get('metadata', {}).get('revision'),
             'applicability': r.get('applicability', {})} for r in records]}
    return text


@tool
async def search_knowledge_base(query: str, k: int = 4, mode: str = 'auto',
                                purpose: str = 'current', as_of: str = '', organization: str = '',
                                region: str = '', business: str = '', sources: list[str] | None = None) -> str:
    """检索企业文档原文。auto 按问题及文档关联决定单次/多跳；single 单次；multi 有限追查。

    purpose=current 查现行，history 查历史（必须提供 as_of YYYY-MM-DD），compare 对比（sources 至少两份完整文件路径）。
    organization/region/business 为适用范围的精确名称；sources 可限制文档。日期和范围必须来自用户问题或已核实事实，不能猜。
    对比不表示版本现行有效；待确认、局部修订和并行版本必须说明，不能单凭文件名或上传时间选新版。
    不用于业务数量统计（请使用 SQL）。返回证据编号、定位、检索轨迹、未覆盖事实和停止原因。
    coverage_verified=false 不代表答案一定错误，但不能声称证据链已经完整。
    """
    denial = source_policy.source_denial('knowledge')
    if denial:
        return denial
    vs_dir = _resolve_path(os.getenv('VECTOR_STORE_DIR', './data/department/company-vector-store'))
    if not query.strip():
        return '知识库检索失败：查询不能为空'
    if not (vs_dir / 'chroma.sqlite3').is_file():
        source_policy.record_gap('knowledge', '知识库索引尚未构建，需维护原件与索引', reason='index_unavailable')
        return '知识库索引尚未构建，请在知识库管理中更新索引'
    thread_id = context.get_thread_id()
    try:
        scope = versions.RetrievalScope(purpose=purpose, as_of=as_of or None, organization=organization,
                                       region=region, business=business, sources=sources or [])
        _validate_query_scope(query, scope)
        scope = scope.model_copy(update={'as_of': date.fromisoformat(scope.resolved()['as_of'])})
    except ValueError as exc:
        source_policy.record_gap('knowledge', str(exc), reason='applicability_context_missing')
        return '知识库适用范围待补充：' + str(exc)
    audit = []

    async def retrieve(text, count):
        return await asyncio.to_thread(_retrieve_sync, text, count, vs_dir, scope, audit,
                                       lambda: context.consume_tool_budget('knowledge', _KNOWLEDGE_SEARCH_LIMIT))

    async def notify(step):
        if thread_id:
            from api.monitor import monitor
            await monitor.report(thread_id, 'retrieval_hop', f"知识库第 {step['hop']} 跳检索", step)

    try:
        result = await adaptive_search(
            query, retrieve, lambda item, text, hop: _register(item, text, hop, thread_id),
            lambda: context.consume_tool_budget('knowledge', _KNOWLEDGE_SEARCH_LIMIT),
            mode=mode, k=max(1, min(int(k), 8)),
            max_hops=int(os.getenv('KNOWLEDGE_MAX_HOPS', '3')),
            timeout=float(os.getenv('KNOWLEDGE_REVIEW_TIMEOUT', '20')), notify=notify)
        records = result.pop('evidence')
        if not records and thread_id:
            search_id = evidence.add(thread_id, {
                'type':'knowledge_search','title':'公司内部文档检索范围与空结果',
                'query':query,'retrieval_scope':scope.resolved(),
                'snippet':'本次在已配置的公司内部文档索引中未检索到相关片段；此记录只证明本次检索结果为空，不证明公司不存在现行规定。',
                'result_count':0,'coverage_verified':bool(result.get('coverage_verified')),
                'stop_reason':result.get('stop_reason')})
            result['empty_search_evidence_id']=search_id
        if scope.purpose == 'compare':
            missing_sources = set(scope.sources) - {r.get('metadata', {}).get('source') for r in records}
            audit.extend('未召回指定对比文档：' + name for name in sorted(missing_sources))
        if audit or any(not r.get('applicability', {}).get('verified') for r in records):
            result['coverage_verified'] = False
            result['missing'] = list(dict.fromkeys([*result.get('missing', []), *audit]))
            if result['stop_reason'] == 'evidence_sufficient':
                result['stop_reason'] = 'applicability_unverified'
        if any('检索预算耗尽' in issue for issue in audit):
            result['stop_reason'] = 'budget_exhausted'
        result['retrieval_scope'] = scope.resolved()
        for missing in result.get('missing', []):
            source_policy.record_gap('knowledge', missing,
                evidence_ids=[r['evidence_id'] for r in records], reason=result['stop_reason'])
        result['evidence_count'] = len(records)
        if thread_id:
            from api.monitor import monitor
            await monitor.report(thread_id, 'retrieval_summary', '知识库检索完成', result)
        summary = '检索状态：' + json.dumps(result, ensure_ascii=False)
        if result['stop_reason'] == 'budget_exhausted':
            summary += f'\n本轮内部知识库检索已达到上限（{_KNOWLEDGE_SEARCH_LIMIT} 次），停止检索。'
        return summary + '\n\n' + (_format_evidence(records) or
            ('当前已配置公司文档索引中未检索到相关片段。['+result['empty_search_evidence_id']+']'
             if result.get('empty_search_evidence_id') else '知识库中未检索到相关内容'))
    except Exception as exc:  # 检索失败时不伪造证据。
        source_policy.record_gap("knowledge", "知识库检索失败，未取得请求的内部资料", reason="tool_error")
        return f'知识库检索失败：{exc}'


if __name__ == "__main__":
    import asyncio

    print(
        asyncio.run(
            search_knowledge_base.ainvoke({"query": "企业内部控制 风险管理", "k": 2})
        )
    )


@tool
async def list_knowledge_versions(offset: int = 0, limit: int = 20) -> str:
    """列出已发布知识库文档路径、版本及适用信息，供历史检索或版本对比选择 sources；不读取未发布草稿。"""
    denial = source_policy.source_denial('knowledge')
    if denial:
        return denial
    vs_dir = _resolve_path(os.getenv('VECTOR_STORE_DIR', './data/department/company-vector-store'))
    def read():
        with _search_guard():
            path = vs_dir / versions.SNAPSHOT
            if not path.exists():
                return '索引没有版本目录，请先更新知识库索引并维护版本信息。'
            snapshot = json.loads(path.read_text(encoding='utf-8'))
            start, size = max(0, offset), max(1, min(limit, 50))
            names = sorted(snapshot)[start:start + size]
            return json.dumps({'total': len(snapshot), 'offset': start,
                'documents': {name: snapshot[name] for name in names}}, ensure_ascii=False)
    return await asyncio.to_thread(read)


def _read_published_document(vs_dir: Path, source: str, offset: int, limit: int, scope) -> str:
    """Read only the immutable publication sidecar, under the publication lock."""
    with _search_guard():
        snapshot = json.loads((vs_dir / versions.SNAPSHOT).read_text(encoding='utf-8'))
        eligible, checks, notices = versions.select_versions(snapshot, scope.resolved())
        if source not in eligible:
            return '知识库读取失败：文档未发布或不符合本次适用范围。' + '；'.join(notices)
        records = json.loads((vs_dir / 'tanyuan-chunks.json').read_text(encoding='utf-8'))
        matches = [r for r in records if r.get('metadata', {}).get('source') == source]
        if not matches or any(r['metadata'].get('revision') != snapshot[source]['revision'] for r in matches):
            return '知识库读取失败：已发布片段与版本目录不一致，请更新索引。'
        selected, size = [], 0
        for record in matches[offset:offset + limit]:
            if size + len(record['text']) > 16000: break
            selected.append(record); size += len(record['text'])
        end = offset + len(selected)
        evidence_records = []
        for record in selected:
            item = {**record, 'chunk_id': record['metadata']['chunk_id'], 'score': 1,
                    'channels': ['published_document'], 'applicability': checks[source],
                    'index_version': snapshot[source]['revision']}
            item['evidence_id'] = _register(item, '按文档读取：' + source, 0, context.get_thread_id())
            evidence_records.append(item)
        summary = {'source': source, 'total_published_chunks': len(matches), 'offset': offset,
                   'returned_chunks': len(selected), 'has_more': end < len(matches),
                   'next_offset': end if end < len(matches) else None,
                   'complete_published_text': offset == 0 and end == len(matches),
                   'applicability_verified': checks[source]['verified'],
                   'note': '范围仅指已发布的解析文本；不证明扫描图像、解析遗漏或全库覆盖。读取完整与版本适用确认分开。'}
        return '文档读取范围：' + json.dumps(summary, ensure_ascii=False) + '\n\n' + _format_evidence(evidence_records)


@tool
async def read_knowledge_document(source: str, offset: int = 0, limit: int = 8,
                                  purpose: str = 'current', as_of: str = '',
                                  organization: str = '', region: str = '', business: str = '') -> str:
    """按完整文档路径分页读取已发布知识片段，供命中文档/关联补充记录的核对。

    source 来自检索结果或已发布目录；不会读取磁盘上的未发布修改。offset 从0起，limit为1～10。
    has_more=true 时按 next_offset 继续。complete_published_text 仅说明已发布文本范围，不证明
    文档法律适用、无解析遗漏或已搜遍知识库。历史制度仍须 purpose=history 和用户给定 as_of。
    """
    denial = source_policy.source_denial('knowledge')
    if denial: return denial
    if not source.strip() or not isinstance(offset, int) or offset < 0 or not 1 <= limit <= 10:
        return '知识库读取失败：source不能为空，offset须非负，limit须为1～10。'
    if not context.consume_tool_budget('knowledge', _KNOWLEDGE_SEARCH_LIMIT):
        return '知识库读取失败：本轮知识库预算已耗尽，请记录缺口，不得重复尝试。'
    try:
        scope = versions.RetrievalScope(purpose=purpose, as_of=as_of or None, organization=organization,
                                       region=region, business=business, sources=[source])
        vs_dir = _resolve_path(os.getenv('VECTOR_STORE_DIR', './data/department/company-vector-store'))
        return await asyncio.to_thread(_read_published_document, vs_dir, source, offset, limit, scope)
    except (OSError, ValueError, KeyError):
        return '知识库读取失败：已发布目录或片段不可用，请维护索引。'
