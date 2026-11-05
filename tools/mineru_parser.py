"""Local MinerU adapter with full-page coverage and revision-bound parse caches."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
from html.parser import HTMLParser
from pathlib import Path

from langchain_core.documents import Document
from utils.path_utils import PROJECT_ROOT

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
ADAPTER_VERSION = 1
_lock = threading.Lock()


class NoExtractedText(ValueError):
    """A full local parse finished without textual blocks (e.g. a pure drawing)."""


def _python() -> Path:
    configured = os.getenv('MINERU_PYTHON', '').strip()
    path = Path(configured) if configured else PROJECT_ROOT / '.venv-mineru' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.is_file():
        raise ValueError('MinerU运行环境不可用，请执行 scripts/setup_mineru.sh 安装本地解析器')
    return path


def parser_profile() -> dict:
    return {'adapter': ADAPTER_VERSION, 'runtime_version': '4.1.7',
            'pdf_mode': os.getenv('DOCUMENT_PDF_PARSER', 'auto'),
            'tier': os.getenv('MINERU_TIER', 'basic')}


def configuration() -> dict:
    return {**parser_profile(), 'python': str(_python())}


class _TableText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('tr', 'p', 'br'): self.parts.append('\n')
        elif tag in ('td', 'th'): self.parts.append(' | ')

    def handle_data(self, data):
        self.parts.append(data)


def _text(value) -> str:
    if isinstance(value, str):
        if '<table' in value.lower():
            parser = _TableText(); parser.feed(value)
            return '\n'.join(line.strip(' |') for line in ''.join(parser.parts).splitlines() if line.strip(' |'))
        return value
    if isinstance(value, list):
        return '\n'.join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        return _text(value.get('content', value.get('text', '')))
    return ''


def documents(payload: dict, source: str, revision: str, parser_version: int, expected_pages: int) -> list[Document]:
    pages = payload.get('pages')
    if payload.get('is_full_document') is not True or not isinstance(pages, list):
        raise ValueError('MinerU未返回完整文档，未将部分解析视为成功')
    indices = [page.get('page_idx') for page in pages]
    if len(indices) != expected_pages or set(indices) != set(range(expected_pages)):
        raise ValueError('MinerU返回页码不完整或重复，未发布解析结果')
    producer = payload.get('metadata', {}).get('producer', {})
    settings = payload.get('extensions', {}).get('mineru', {})
    units = []
    def visit(block, page, fallback):
        text = _text(block.get('content', ''))
        block_id = block.get('index') if block.get('index') is not None else fallback
        if text.strip():
            metadata = {'source': source, 'revision': revision, 'parser_version': parser_version,
                        'parser': 'mineru', 'mineru_version': str(producer.get('version', 'unknown')),
                        'parse_tier': str(settings.get('tier', 'unknown')), 'page': page + 1,
                        'parse_mode': str(settings.get('parse_mode', 'unknown')),
                        'block': str(block_id), 'content_type': str(block.get('type', 'text')),
                        'full_document': True, 'page_count': expected_pages}
            if block.get('bbox') is not None:
                metadata['bbox'] = json.dumps(block['bbox'])
            units.append(Document(page_content=text.strip(), metadata=metadata))
        for number, child in enumerate(block.get('blocks') or []):
            visit(child, page, f'{block_id}.{number}')
        for kind in ('captions', 'footnotes'):
            for number, child in enumerate(block.get(kind) or []):
                visit({**child, 'type': kind}, page, f'{block_id}.{kind}.{number}')
    for page in pages:
        for number, block in enumerate(page.get('blocks') or []):
            visit(block, page['page_idx'], number)
    if not units:
        raise NoExtractedText('MinerU未识别出可用文字；纯附图请调用analyze_visual_document理解图形，不代表OCR成功')
    return units


def parse_with_mineru(path: Path, source: str, revision: str, parser_version: int, expected_pages: int) -> list[Document]:
    config = configuration()
    if config['tier'] not in ('flash', 'basic', 'standard', 'advanced'):
        raise ValueError('MINERU_TIER须为flash/basic/standard/advanced')
    key = hashlib.sha256(json.dumps([revision, path.suffix.lower(), config], sort_keys=True).encode()).hexdigest()
    cache = PROJECT_ROOT / 'data' / 'document-parse-cache'
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / (key + '.json')
    with _lock:
        if cached.is_file():
            try:
                return documents(json.loads(cached.read_text(encoding='utf-8')), source, revision, parser_version, expected_pages)
            except (ValueError, TypeError, KeyError):
                cached.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix='mineru-local-') as temporary:
            output = Path(temporary) / 'result.json'
            env = os.environ.copy()
            env.setdefault('MINERU_MODEL_SOURCE', 'modelscope')
            # The worker only calls the local SDK; never inherits a remote parsing URL.
            env.pop('MINERU_API_URL', None)
            command = [config['python'], str(PROJECT_ROOT / 'scripts/mineru_worker.py'), str(path.resolve()), str(output), config['tier']]
            try:
                completed = subprocess.run(command, timeout=float(os.getenv('MINERU_TIMEOUT', '300')),
                                           capture_output=True, env=env, cwd=PROJECT_ROOT)
            except subprocess.TimeoutExpired as exc:
                raise ValueError('MinerU本地解析超时，未发布部分结果；请拆分文档或调整MINERU_TIMEOUT') from exc
            if completed.returncode or not output.is_file():
                (cache / (key + '.error.log')).write_bytes(completed.stderr[-100_000:])
                # Logs can contain document text; keep them off user-facing tool responses.
                raise ValueError('MinerU本地解析失败，请检查本地模型安装与运行配置；未将空解析当成成功')
            payload = json.loads(output.read_text(encoding='utf-8'))
            units = documents(payload, source, revision, parser_version, expected_pages)
            with tempfile.NamedTemporaryFile(dir=cache, mode='w', encoding='utf-8', delete=False) as pending:
                json.dump(payload, pending, ensure_ascii=False)
                pending_path = Path(pending.name)
            os.replace(pending_path, cached)
            return units
