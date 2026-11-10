"""Bounded reading of adjacent published chunks, after retrieval and ranking.

Only contiguous, verifiable source spans in the same parsing unit are joined.
No new embeddings, inferred text, relevance scores or adoption claims are made.
"""
from __future__ import annotations

import os
import re

from casework.chunking import _HEADING, count

MAX_WINDOW_TOKENS = 960
MAX_ADDED_TOKENS = 1536
MAX_EXPANSION_DEPTH = 2
_QUESTION = re.compile(r'是否|是不是|一直|始终|何时|什么时候|条件|例外|限制|适用|前提|何种|什么情况|为何|为什么|\b(?:when|always|unless|conditions?|exceptions?)\b', re.I)
_REFERENCE = re.compile(r'上述|前述|所述|该(?:装置|方法|步骤|方案|过程|模块|参数)|其中|因此|否则|\b(?:this|these|above|otherwise)\b', re.I)
_QUALIFIER = re.compile(r'仅|只在|只有|不适用|不启用|不采用|不执行|除非|但是|但在|然而|否则|例外|前提|\b(?:only|unless|however|except)\b', re.I)
_STRUCTURED = re.compile(r'表格|工作表|形状|区域|CSV|JSON', re.I)
_END = re.compile(r'[。！？；.!?;][”’"\')）\]】]*\s*$')


def _identity(meta):
    keys = ('root_id', 'case_id', 'file_id', 'revision', 'parser_key')
    return tuple(meta.get(key) for key in keys)


def _span(record):
    spans = record['metadata'].get('spans', [])
    if len(spans) != 1:
        return None
    span = spans[0]
    start, end = span.get('start'), span.get('end')
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        return None
    if end - start != len(record['text']) or not span.get('location'):
        return None
    if _STRUCTURED.search(span['location']):
        return None
    return span


def _join(left, right):
    """Join in source order; an overlap must agree byte-for-byte as Python text."""
    a, b = _span(left), _span(right)
    if not a or not b or a['location'] != b['location']:
        return None
    if not a['start'] < b['start'] <= a['end'] or b['end'] <= a['end']:
        return None
    overlap = a['end'] - b['start']
    if overlap and left['text'][-overlap:] != right['text'][:overlap]:
        return None
    # A heading introduced after the left-hand start denotes a section boundary.
    if any(m.start() >= overlap for m in _HEADING.finditer(right['text'])):
        return None
    span = {**a, 'end': b['end']}
    return {'text': left['text'] + right['text'][overlap:],
            'metadata': {**left['metadata'], 'spans': [span]}}


def _reasons(query, record, neighbors):
    reasons = []
    text = record['text']
    if _QUESTION.search(query):
        reasons.append('question_conditions')
    if _REFERENCE.search(text):
        reasons.append('context_reference')
    if not _END.search(text):
        reasons.append('unfinished_sentence')
    # Read a nearby restriction even if the question did not explicitly ask for it.
    if any(_QUALIFIER.search(n['text'][:120]) for n in neighbors):
        reasons.append('neighbor_qualification')
    return reasons


# These are coverage signals, not a semantic judge or an absence proof.
_FACETS = {
    'conditions': (re.compile(r'一直|始终|全程|总是|何时|什么时候|条件|限制|适用|前提|什么情况|是否.{0,12}(?:启用|停用|开启|关闭|执行|运行|工作|生效)|\b(?:when|always|conditions?)\b', re.I),
                   re.compile(r'仅|只在|只有|当.{0,40}时|在.{0,30}阶段|如果|若|前提|适用条件|启用条件|不启用|不适用|一直|始终|全程|\b(?:only|unless|when|if|always)\b', re.I)),
    'exceptions': (re.compile(r'例外|除外|除非|\bexceptions?\b', re.I),
                   re.compile(r'例外|除外|除非|否则|不(?:启用|适用|执行|采用)|\b(?:except|unless|otherwise)\b', re.I)),
    'procedure': (re.compile(r'如何|怎么|步骤|流程|\b(?:how|steps?)\b', re.I),
                  re.compile(r'步骤|先|然后|再|依次|流程|\bstep\b', re.I)),
    'reason': (re.compile(r'为何|为什么|原因|\bwhy\b', re.I),
               re.compile(r'因为|由于|原因|导致|因此|\bbecause\b', re.I)),
}


def _requirements(query):
    from casework.parser import CASE_RE
    from casework.subject_support import subjects
    # Identifiers are metadata selectors, not concepts every body must repeat.
    scaffolding = {'例外', '限制', '流程', '步骤', '前提', '原因', '条件', '依据', '原文',
                   'condition', 'conditions', 'exception', 'exceptions', 'step', 'steps', 'why', 'when', 'how'}
    terms = tuple(t for t in subjects(CASE_RE.sub('', query)) if t not in scaffolding)
    from casework.evidence_scope import attributes
    return {'facets': [name for name, (asked, _) in _FACETS.items() if asked.search(query)],
            'terms': terms, **attributes(query)}


def _assess(requirements, window):
    from casework.evidence_scope import assess
    return assess(requirements, window, _FACETS, _END, _REFERENCE)


def _value(requirements, assessment, neighbor, direction, depth):
    from casework.subject_support import contains
    text = neighbor['text']
    value = 0
    for facet in assessment['missing']:
        if facet in _FACETS and _FACETS[facet][1].search(text):
            value += 60
    newly_named = sum(contains(text, term) for term in assessment['missing_terms'])
    value += 35 * newly_named
    value += min(8, 2 * sum(contains(text, term) for term in requirements['terms']))
    if 'leading_reference' in assessment['missing'] and direction < 0:
        value += 30
    if 'unfinished_sentence' in assessment['missing'] and direction > 0:
        value += 30
    if direction > 0 and set(assessment['missing']).intersection({'conditions', 'exceptions', 'procedure'}):
        value += 8
    if _QUALIFIER.search(text):
        value += 12
    if re.search(r'背景技术|现有技术|背景介绍|背景说明', text):
        value -= 15
    return value - 5 * (depth - 1)


def _covered(proof, record):
    meta = record['metadata']
    if any(proof.get(k) != meta.get(k) for k in ('file_id', 'revision', 'parser_key')):
        return False
    a, b = _span({'text': proof['excerpt'], 'metadata': proof}), _span(record)
    return bool(a and b and a['location'] == b['location'] and a['start'] <= b['start']
                and a['end'] >= b['end']
                and proof['excerpt'][b['start']-a['start']:b['end']-a['start']] == record['text'])


def expand_results(query, groups, records):
    """Schedule bounded adjacent reads by evidence value, preserving result order."""
    summary = {'enabled': os.getenv('CASEWORK_CONTEXT_EXPANSION', '1') != '0',
               'expanded_sources': 0, 'added_tokens': 0, 'deduplicated_sources': 0,
               'max_window_tokens': MAX_WINDOW_TOKENS, 'max_added_tokens': MAX_ADDED_TOKENS,
               'max_expansion_depth': MAX_EXPANSION_DEPTH, 'unresolved_sources': [],
               'scope': '默认一层，仍有缺口时每个方向最多两层；完整性仅为规则信号，不证明全文完整或技术采用'}
    if not summary['enabled'] or not groups:
        return summary
    wanted = {p['file_id'] for g in groups for p in g['sources']}
    by_id, files = {}, {}
    for record in records:
        meta = record['metadata']
        if meta['file_id'] in wanted:
            by_id[meta['chunk_id']] = record
            files.setdefault(_identity(meta), {})[meta['ordinal']] = record
    requirements = _requirements(query)
    states = []
    for gi, group in enumerate(groups):
        for pi, proof in enumerate(group['sources']):
            record = by_id.get(proof.get('chunk_id'))
            if record is None:
                continue
            meta = record['metadata']
            siblings = files[_identity(meta)]
            neighbors = [siblings[n] for n in (meta['ordinal'] - 1, meta['ordinal'] + 1) if n in siblings]
            assessment = _assess(requirements, record)
            reasons = _reasons(query, record, neighbors)
            if assessment['missing']:
                reasons.append('coverage_gap')
            detail = {'status': 'not_needed', 'reasons': reasons, 'added_chunk_ids': [],
                      'retrieved_chunk_ids': [meta['chunk_id']], 'blocked': [], 'directions': [],
                      'steps': [], 'completeness': assessment['status'], 'assessment': assessment,
                      'stop_reason': 'not_triggered', 'max_depth_reached': 0}
            proof['context_expansion'] = detail
            state = {'group': gi, 'index': pi, 'proof': proof, 'record': record, 'window': record,
                     'siblings': siblings, 'depths': {-1: 0, 1: 0}, 'closed': set(),
                     'assessment': assessment, 'detail': detail, 'spent': 0, 'active': bool(reasons),
                     'tokens': None, 'cache': {}, 'covered_by': None}
            states.append(state)
            if not reasons:
                continue
            if assessment['status'] == 'covered_by_checks' and reasons == ['question_conditions']:
                detail['stop_reason'] = 'coverage_checks_satisfied'
                state['active'] = False
                continue
            if meta['kind'] == 'drawing' or meta.get('document_format') in ('.csv', '.json', '.xls', '.xlsx') or not _span(record):
                detail.update(status='unsupported_boundary', stop_reason='unsupported_boundary')
                state['active'] = False
            else:
                state['tokens'] = count(record['text'])
    # Reconsider the whole frontier after every admitted read. Source order only
    # breaks ties; it cannot reserve the budget for an entire case in advance.
    while True:
        choices = []
        for si, state in enumerate(states):
            if not state['active']:
                continue
            covering = next((other for other in states if other is not state
                and other['group'] == state['group'] and other['covered_by'] is None
                and other['detail']['steps'] and _covered(
                    {**other['window']['metadata'], 'excerpt': other['window']['text']}, state['window'])), None)
            if covering:
                state.update(active=False, covered_by=covering)
                summary['added_tokens'] -= state['spent']
                state['spent'] = 0
                summary['deduplicated_sources'] += 1
                continue
            detail = state['detail']
            if detail['steps'] and not state['assessment']['missing']:
                detail['stop_reason'] = ('coverage_checks_satisfied' if state['assessment']['status'] == 'covered_by_checks'
                                         else 'no_observed_gap')
                state['active'] = False
                continue
            record = state['record']
            meta = record['metadata']
            for direction in (-1, 1):
                if direction in state['closed']:
                    continue
                depth = state['depths'][direction] + 1
                if depth > MAX_EXPANSION_DEPTH:
                    state['closed'].add(direction)
                    detail['blocked'].append({'direction': direction, 'depth': depth, 'reason': 'depth_limit'})
                    continue
                neighbor = state['siblings'].get(meta['ordinal'] + direction * depth)
                if neighbor is None:
                    state['closed'].add(direction)
                    detail['blocked'].append({'direction': direction, 'depth': depth, 'reason': 'no_neighbor'})
                    continue
                key = (state['depths'][-1], state['depths'][1], direction)
                if key not in state['cache']:
                    joined = _join(neighbor, state['window']) if direction < 0 else _join(state['window'], neighbor)
                    tokens = count(joined['text']) if joined is not None else None
                    state['cache'][key] = (joined, tokens)
                joined, tokens = state['cache'][key]
                reason = 'source_boundary' if joined is None else None
                delta = max(0, tokens - state['tokens']) if joined is not None else 0
                if reason is None and (tokens > MAX_WINDOW_TOKENS or summary['added_tokens'] + delta > MAX_ADDED_TOKENS):
                    reason = 'token_budget'
                if reason:
                    state['closed'].add(direction)
                    detail['blocked'].append({'direction': direction, 'depth': depth, 'reason': reason})
                    continue
                value = _value(requirements, state['assessment'], neighbor, direction, depth)
                # Value per admitted token, then absolute value, depth and source
                # order. We never compare logits from different retrieval models.
                rank = (-max(value, 0) / max(delta, 1), -value, delta, depth,
                        state['group'], state['index'], -direction)
                choices.append((rank, si, direction, depth, neighbor, joined, tokens, delta, value))
        if not choices:
            break
        _, si, direction, depth, neighbor, joined, tokens, delta, value = min(choices, key=lambda item: item[0])
        state = states[si]
        detail = state['detail']
        state.update(window=joined, tokens=tokens, spent=state['spent'] + delta)
        state['depths'][direction] = depth
        state['assessment'] = _assess(requirements, joined)
        summary['added_tokens'] += delta
        detail['added_chunk_ids'].append(neighbor['metadata']['chunk_id'])
        detail['directions'].append(direction)
        detail['max_depth_reached'] = max(detail['max_depth_reached'], depth)
        detail['steps'].append({'chunk_id': neighbor['metadata']['chunk_id'], 'direction': direction,
                                'depth': depth, 'added_tokens': delta, 'value': value})
    for state in states:
        proof, detail = state['proof'], state['detail']
        if state['covered_by'] is not None:
            # Resolve an inclusion chain after the scheduler has finished.
            parent = state['covered_by']
            while parent['covered_by'] is not None:
                parent = parent['covered_by']
            parent['detail']['retrieved_chunk_ids'].extend(detail['retrieved_chunk_ids'])
            continue
        detail['assessment'] = state['assessment']
        detail['completeness'] = state['assessment']['status']
        if detail['steps']:
            span = _span(state['window'])
            proof.update(retrieved_excerpt=proof['excerpt'], retrieved_location=proof['location'],
                         excerpt=state['window']['text'], spans=[span],
                         location=f"{span['location']} · 字符{span['start']+1}–{span['end']}")
            detail.update(status='expanded', token_count=state['tokens'],
                          original_token_count=count(state['record']['text']))
        elif detail['status'] != 'unsupported_boundary' and detail['reasons']:
            detail['status'] = 'bounded' if any(b['reason'] != 'no_neighbor' for b in detail['blocked']) else 'no_neighbor'
        if detail['reasons'] and detail['stop_reason'] == 'not_triggered':
            detail['stop_reason'] = ('coverage_checks_satisfied' if detail['steps'] and state['assessment']['status'] == 'covered_by_checks'
                                     else 'no_observed_gap' if detail['steps'] and not state['assessment']['missing']
                                     else 'bounded_evidence_incomplete')
        if state['assessment']['missing'] or detail['status'] == 'unsupported_boundary':
            detail['follow_up'] = {'tool': 'read_case_archive_document', 'file_id': proof['file_id'],
                                   'revision': proof['revision'], 'parser_key': proof['parser_key'],
                                   'structured_read_required': detail['status'] == 'unsupported_boundary'}
    # Keep the externally visible case/source order. Deduplication is based on
    # source coordinates and exact text, never on semantic similarity.
    suppressed = {id(s['proof']) for s in states if s['covered_by'] is not None}
    for group in groups:
        kept = []
        for proof in group['sources']:
            if id(proof) in suppressed:
                continue
            if 'context_expansion' not in proof:
                kept.append(proof)
                continue
            window = {'text': proof['excerpt'], 'metadata': proof}
            covering = next((p for p in kept if p.get('context_expansion', {}).get('status') == 'expanded'
                             and _covered(p, window)), None)
            if covering:
                covering['context_expansion']['retrieved_chunk_ids'].extend(proof['context_expansion']['retrieved_chunk_ids'])
                summary['deduplicated_sources'] += 1
                state = next(s for s in states if s['proof'] is proof)
                summary['added_tokens'] -= state['spent']
            else:
                kept.append(proof)
        group['sources'] = kept
    summary['unresolved_sources'] = [
        {'chunk_id': p['chunk_id'], 'file_id': p['file_id'],
         'missing': p['context_expansion']['assessment']['missing'],
         'stop_reason': p['context_expansion']['stop_reason'], 'blocked': p['context_expansion']['blocked']}
        for g in groups for p in g['sources'] if 'context_expansion' in p
        and (p['context_expansion']['assessment']['missing'] or p['context_expansion']['status'] == 'unsupported_boundary')]
    summary['expanded_sources'] = sum(p.get('context_expansion', {}).get('status') == 'expanded'
                                     for g in groups for p in g['sources'])
    return summary
