"""Structure-aware, locally token-counted chunks with original source spans."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from utils.path_utils import PROJECT_ROOT

VERSION = 'structure-token-1'


@lru_cache(maxsize=4)
def _tokenizer(path):
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(path, local_files_only=True)


def tokenizer_path():
    raw = os.getenv('CASEWORK_EMBEDDING_MODEL_PATH', '').strip() or os.getenv('EMBEDDING_MODEL_PATH', '').strip()
    if raw:
        path = Path(raw).expanduser()
        return str((PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve())
    default = PROJECT_ROOT / 'models/Qwen3-Embedding-0.6B'
    return str(default) if (default/'tokenizer_config.json').is_file() else None


def count(text):
    path = tokenizer_path()
    if path:
        return len(_tokenizer(path).encode(text, add_special_tokens=False, truncation=False, verbose=False))
    # Without a local model, conservatively bound UTF-8 bytes; never download a tokenizer.
    return len(text.encode('utf-8'))


def config():
    size = int(os.getenv('CASEWORK_CHUNK_TOKENS', '320'))
    overlap = int(os.getenv('CASEWORK_CHUNK_OVERLAP', '48'))
    strategy = os.getenv('CASEWORK_CHUNK_STRATEGY', 'tokens')
    if strategy not in ('tokens', 'legacy_chars') or not 32 <= size <= 480 or not 0 <= overlap < size:
        raise ValueError('切块参数需为tokens/legacy_chars，32–480 Token，0<=重叠<块长')
    return {'version': VERSION, 'strategy': strategy, 'tokens': size, 'overlap': overlap,
            'tokenizer': tokenizer_path(), 'counting': 'model_tokenizer' if tokenizer_path() else 'utf8_bytes'}


def _prefix_end(text, start, end, limit):
    low, high = start, end
    while low < high:
        middle = (low + high + 1) // 2
        if count(text[start:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    # Token counts can change at word boundaries; verify the actual emitted text.
    while low > start and count(text[start:low]) > limit:
        low -= 1
    return low


def _windows(text, size, overlap):
    start = 0
    while start < len(text):
        end = _prefix_end(text, start, len(text), size)
        if end <= start:
            raise ValueError('单字符超过切块预算')
        if end < len(text):
            # Keep paragraphs, then sentences, together where the budget allows.
            floor = start + (end - start) // 2
            for pattern in (r'\n\s*\n', r'[。！？；!?;]', r'\n', r'[,，、 ]'):
                boundaries = [m.end() for m in re.finditer(pattern, text[start:end]) if start + m.end() >= floor]
                if boundaries:
                    end = start + boundaries[-1]
                    break
        if text[start:end].strip():
            yield start, end
        if end == len(text):
            break
        next_start = end
        if overlap:
            # Find a suffix that fits the overlap token budget.
            lo, hi = start + 1, end
            while lo < hi:
                middle = (lo + hi) // 2
                if count(text[middle:end]) <= overlap:
                    hi = middle
                else:
                    lo = middle + 1
            next_start = lo
            boundaries = list(re.finditer(r'[。！？；!?;]\s*|\n+', text[next_start:end]))
            if boundaries:
                candidate = next_start + boundaries[0].end()
                if candidate < end:
                    next_start = candidate
        start = max(start + 1, next_start)


_HEADING = re.compile(r'(?m)^(?:#{1,6}\s+[^\n]+|摘要|材料信息|技术领域(?:与具体问题)?|背景技术|背景与处理边界|技术方案|具体实施方式|权利要求书[^\n]*|说明书[^\n]*|验证计划[^\n]*|来源说明)\s*$')


def split_units(units, document_format=None):
    settings = config()
    if settings['strategy'] == 'legacy_chars':
        return [{'text': text.strip()[at:at + 800], 'location': f'{location} · 字符{at+1}–{at+len(text.strip()[at:at+800])}',
                 'spans':[{'location':location,'start':at,'end':at+len(text.strip()[at:at+800])}]}
                for text, location in units for at in range(0, len(text.strip()), 720) if text.strip()[at:at + 800].strip()]
    groups = []; pdf_section = None
    for text, location in units:
        text = text.strip()
        if not text:
            continue
        pieces=[(0,len(text),None)]
        if document_format=='.pdf':
            headings=list(_HEADING.finditer(text));pieces=[];begin=0
            for heading in headings:
                if heading.start()>begin:pieces.append((begin,heading.start(),pdf_section))
                begin=heading.start();pdf_section=heading.group().strip()
            pieces.append((begin,len(text),pdf_section))
        for begin,end,section in pieces:
            part=text[begin:end]
            key = location.split(' · 正文块')[0] if '正文块' in location and '表格' not in location else None
            if document_format=='.pdf' and section:key='PDF章节:'+section
            if key is not None and groups and groups[-1]['key'] == key:
                group = groups[-1]
                offset = len(group['text']) + 2
                group['text'] += '\n\n' + part
                group['spans'].append((offset, offset + len(part), location,begin))
            else:
                groups.append({'key': key, 'text': part, 'spans': [(0, len(part), location,begin)]})
    result = []
    for group in groups:
        text = group['text']
        for start, end in _windows(text, settings['tokens'], settings['overlap']):
            spans = [{'location': location, 'start': origin+max(start, a) - a,
                      'end': origin+min(end, b) - a} for a, b, location,origin in group['spans'] if a < end and b > start]
            location = '；'.join(f"{s['location']} · 字符{s['start']+1}–{s['end']}" for s in spans)
            result.append({'text': text[start:end], 'location': location, 'spans': spans,
                           'token_count': count(text[start:end])})
    return result
