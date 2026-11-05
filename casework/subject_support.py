"""Conservative subject support in published text, before query expansion.

This lexical admission check cannot establish adoption or semantic equivalence.
It keeps unknown objects from being replaced with the nearest catalog concept.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

VERSION = 'subject-support-3'
# Request/output scaffolding, never a domain allowlist or a list of test terms.
SCAFFOLD = frozenset('问题 方案 方法 技术 材料 资料 案件 案子 新案 设计 步骤 结果 内容 信息 系统 实验 条件 参数 说明 文档 流程 结构 处理 版本 范围 要求 场景 对象 方式 部分 地方 时候 时间 当时 前提 标明 原文 正文 表里 答案 结论 经验 历史 同事 同一个 这种 那种 本次 类型 过程 方面 情况 例子 参考 文字 产生'.split())
ENGLISH_SCAFFOLD = frozenset('a an the is are how what which find show for with of in on to and or method design system'.split())


def normalize(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).casefold())


@lru_cache(maxsize=512)
def subjects(query):
    import jieba
    import jieba.posseg
    jieba.setLogLevel(40)
    terms = []
    for item in jieba.posseg.cut(unicodedata.normalize('NFKC', query), HMM=False):
        word = item.word.casefold()
        if (item.flag.startswith('n') or item.flag == 'eng') and len(word) >= 2 and word not in SCAFFOLD | ENGLISH_SCAFFOLD:
            if word not in terms:
                terms.append(word)
    for word in re.findall(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_+-]{2,}(?![A-Za-z0-9])', query):
        word = word.casefold()
        if word not in terms and word not in ENGLISH_SCAFFOLD:
            terms.append(word)
    return tuple(terms)


def contains(text, term):
    text = unicodedata.normalize('NFKC', text).casefold()
    if re.fullmatch(r'[a-z0-9_+\-]+', term):
        return bool(re.search(r'(?<![a-z0-9_+\-])' + re.escape(term) + r'(?![a-z0-9_+\-])', text))
    return normalize(term) in normalize(text)


def check(terms, text, *, minimum=.5):
    body = unicodedata.normalize('NFKC', text).casefold()
    matched = [word for word in terms if contains(body, word)]
    missing = [word for word in terms if word not in matched]
    fraction = len(matched) / len(terms) if terms else 1.0
    return {'version': VERSION, 'terms': list(terms), 'matched': matched, 'missing': missing,
            'coverage': fraction, 'minimum': minimum, 'supported': fraction >= minimum}


def assess(query, text):
    terms = subjects(query)
    result = check(terms, text)
    body = unicodedata.normalize('NFKC', text).casefold()
    # Only a noun phrase at the query's beginning is a literal subject anchor.
    # Do not treat the first noun anywhere in a sentence as the subject: Chinese
    # segmentation can split verbs into nouns (e.g. 先生成), or return locations.
    phrase = re.sub(r'^(?:(?:请|帮我|查找|查询|搜索|检索|找|查|有关|关于)\s*)+', '', query.strip())
    anchor = terms[0] if terms and normalize(phrase).startswith(normalize(terms[0])) else None
    result['anchor'] = anchor
    anchors = [anchor] if anchor else []
    # Explicit application targets remain required even if many familiar
    # mechanism words surround them (e.g. retrieval/indexing applied to X).
    for target in re.findall(r'(?:用于|用在|针对|面向|应用于|适用于)([^，。；！？,;!?]+)', query):
        target_terms = subjects(target)
        if target_terms and target_terms[0] not in anchors:
            anchors.append(target_terms[0])
    result['anchors'] = anchors
    result['anchor_supported'] = all(contains(body, word) for word in anchors)
    acronyms = [w.casefold() for w in re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Z0-9_-]{2,}(?![A-Za-z0-9])', query)]
    result['missing_acronyms'] = [w for w in acronyms if not contains(body, w)]
    result['supported'] &= result['anchor_supported'] and not result['missing_acronyms']
    result['status'] = 'checked' if terms else 'not_required'
    return result
