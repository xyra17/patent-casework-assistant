"""中文双字/英文词 BM25 + 向量 RRF 融合、确定性重排；不需要额外模型下载。"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter


def tokens(text: str) -> list[str]:
    words = re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+', text.lower())
    result = []
    for word in words:
        if re.fullmatch(r'[\u4e00-\u9fff]+', word):
            result.extend(word[i:i + 2] for i in range(len(word) - 1))
            if len(word) == 1:
                result.append(word)
        else:
            result.append(word)
    return result


def record_id(record: dict) -> str:
    meta = record.get('metadata', {})
    return meta.get('chunk_id') or hashlib.sha256(
        (str(meta.get('source', '')) + '\n' + record['text']).encode()).hexdigest()[:24]


def stable_rank_key(record: dict):
    """Ordering cannot depend on a newly generated root/file database ID."""
    meta=record.get('metadata',{})
    return (str(meta.get('case_id','')),str(meta.get('source','')),str(meta.get('location','')),
            hashlib.sha256(record['text'].encode()).hexdigest())


class LexicalIndex:
    def __init__(self, records: list[dict]):
        self.records = records
        self.counts = [Counter(tokens(str(r.get('metadata', {}).get('source', '')) + ' ' +
                                      str(r.get('metadata', {}).get('section', '')) + ' ' + r['text'])) for r in records]
        self.lengths = [sum(count.values()) for count in self.counts]
        self.average = sum(self.lengths) / max(len(records), 1) or 1
        self.frequency = Counter(term for count in self.counts for term in count)

    def search(self, query: str, limit: int = 16) -> list[dict]:
        terms = set(tokens(query))
        ranked = []
        for record, count, length in zip(self.records, self.counts, self.lengths):
            score = 0.0
            for term in terms:
                tf = count.get(term, 0)
                if tf:
                    idf = math.log(1 + (len(self.records) - self.frequency[term] + .5) / (self.frequency[term] + .5))
                    score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / self.average))
            if score > 0:
                ranked.append({**record, 'lexical_score': score})
        return sorted(ranked, key=lambda r: (-r['lexical_score'], stable_rank_key(r)))[:limit]


def fuse_results(query: str, vector: list[dict], lexical: list[dict], k: int = 4,
                 min_overlap: float = .08, max_distance: float = .8) -> list[dict]:
    """RRF不能当置信度；距离为归一化向量的平方L2，阈值需按模型与语料评估。"""
    records, scores = {}, Counter()
    for channel in (vector, lexical):
        for rank, record in enumerate(channel, 1):
            key = record_id(record)
            records[key] = {**records.get(key, {}), **record}
            scores[key] += 1 / (60 + rank)
    query_terms = set(tokens(query))
    ranked = []
    for key, record in records.items():
        haystack = str(record.get('metadata', {}).get('source', '')) + ' ' + record['text']
        overlap = len(query_terms & set(tokens(haystack))) / max(len(query_terms), 1)
        distance = record.get('vector_distance')
        # 无距离的旧接口/测试替身只作兼容；真实 Chroma 使用带距离接口。
        semantic_match = distance is not None and distance <= max_distance
        if overlap < min_overlap and not semantic_match and not record.get('legacy_vector'):
            continue
        title = str(record.get('metadata', {}).get('source', '')).rsplit('/', 1)[-1].rsplit('.', 1)[0]
        title_boost = .03 if len(title) >= 3 and title in query else 0
        ranked.append({**record, 'chunk_id': key, 'score': round(scores[key] + overlap * .01 + title_boost, 6),
                       'overlap': round(overlap, 4), 'channels': [name for name, flag in
                         [('vector', key in {record_id(r) for r in vector}), ('bm25', key in {record_id(r) for r in lexical})] if flag]})
    return sorted(ranked, key=lambda r: (-r['score'], stable_rank_key(r)))[:k]
