"""Render declared, verified source quotations without changing their claims."""
from __future__ import annotations

import re
import unicodedata

_BLOCK = re.compile(r'(?m)^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*')
_REFS = r'(?:[ \t]*\[S[1-9]\d*\])+'


def _span_parts(quote, source):
    """Map ordered literal/elided segments back to exact registered bytes."""
    from tools.answer_contracts import normalize_source
    from tools.evidence_refs import _literal
    if normalize_source(source)[0] != source:
        return None
    chars, positions = [], []
    for offset, char in enumerate(source):
        for normalized in unicodedata.normalize('NFKC', char):
            if not re.match(r'''[\s`*"'“”‘’「」]''', normalized):
                chars.append(normalized); positions.append(offset)
    normalized = ''.join(chars)
    parts = [p for p in re.split(r'…+|\.{3,}', _literal(quote)) if p]
    spans, cursor = [], 0
    for part in parts:
        start = normalized.find(part, cursor)
        if start < 0:
            return None
        end = start + len(part)
        spans.append((positions[start], positions[end-1]+1))
        cursor = end
    return spans or None


def _metadata_matches(annotation, item, previous_identity):
    if not annotation:
        return True
    identity = (item.get('file_id'), item.get('revision'), item.get('parser_key'))
    values = {str(item.get(k)) for k in ('title','source','location','file_id','revision','parser_key') if item.get(k)}
    for part in re.split(r'[，,；;]', annotation[1:-1]):
        part = part.strip()
        if part in values:
            continue
        explicit = re.fullmatch(r'(file_id|revision|parser_key)\s*[:：]\s*(.+)', part)
        if explicit and explicit[2] == item.get(explicit[1]):
            continue
        if part == '同上 file_id' and item.get('file_id') and identity == previous_identity:
            continue
        return False
    return True


def render_source_quotes(report, items):
    """Canonicalize only quotes whose declared IDs and literal spans agree.

    Unknown IDs, altered text, mixed versions and unverified annotations stay
    unchanged so the existing strict validator still rejects them.
    """
    from tools.evidence_refs import report_references, _json_answer, _supported_blockquotes
    if _json_answer(report) is not None:
        return report, []
    by_id = {p.get('id'):p for p in items if p.get('type') in ('knowledge','file','upload','web')
             and p.get('task_scope_compatible') is not False and p.get('current_snapshot') is not False}
    fenced = [(m.start(),m.end()) for m in re.finditer(r'(?ms)^```[^\n]*\n.*?^```[^\n]*', report)]
    already_bound = set(_supported_blockquotes(report,items))
    output, audit, cursor, previous_identity = [], [], 0, None
    for block in _BLOCK.finditer(report):
        if block.start() < cursor or any(a <= block.start() < b for a,b in fenced):
            continue
        end = block.end()
        text = re.sub(r'(?m)^[ \t]*> ?', '', block[0]).strip()
        # A standalone reference immediately after a block is an explicit
        # binding, never a guessed source chosen by lexical similarity.
        adjacent = re.match(r'\n[ \t]*(?:\n[ \t]*)?(?P<refs>'+_REFS+r')[ \t]*(?=\n|$)', report[end:])
        if adjacent:
            text += '\n' + adjacent['refs']; end += adjacent.end()
        tail = re.search(r'(?P<refs>(?:\s*\[S[1-9]\d*\])+)(?P<meta>\s*[（(][^\n]*[）)])?\s*$', text)
        if not tail:
            continue
        refs = report_references(tail['refs'])
        if not refs or not refs <= by_id.keys():
            continue
        selected = [by_id[s] for s in sorted(refs, key=lambda s:int(s[1:]))]
        identities = {(p.get('file_id'),p.get('revision'),p.get('parser_key'),p.get('source'),
                       p.get('url'),None if p.get('file_id') else p.get('title')) for p in selected}
        if len(identities) != 1:
            continue
        annotation = (tail['meta'] or '').strip()
        if not all(_metadata_matches(annotation,p,previous_identity) for p in selected):
            continue
        quote = text[:tail.start()].strip()
        # Embedded application references belong to separate quote units.
        if report_references(quote):
            continue
        spans = [_span_parts(quote,str(p.get('snippet') or '')) for p in selected]
        if not all(spans):
            continue
        item = selected[0]
        if not adjacent and (block.start(),block.end()) in already_bound:
            previous_identity = (item.get('file_id'),item.get('revision'),item.get('parser_key'))
            continue
        citations = ''.join('['+p['id']+']' for p in selected)
        chunks = ['> '+item['snippet'][a:b].replace('\n','\n> ')+' '+citations for a,b in spans[0]]
        rendered = '\n\n……\n\n'.join(chunks)
        if re.match(r'^[“「\"]?(?:…+|\.{3,})', quote):
            rendered = '（前文省略。）\n\n' + rendered
        if re.search(r'(?:…+|\.{3,})[”」\"]?$', quote):
            rendered += '\n\n（后文省略。）'
        if annotation:
            rendered += '\n\n来源：'+str(item.get('source') or item.get('title') or '')+'；位置：'+str(item.get('location') or '')+'。'
        previous_identity = (item.get('file_id'),item.get('revision'),item.get('parser_key'))
        if rendered != report[block.start():end]:
            audit.append({'kind':'source_quote','evidence_ids':[p['id'] for p in selected],
                          'file_id':item.get('file_id'),'revision':item.get('revision'),'parser_key':item.get('parser_key'),
                          'spans':[{'start':a,'end':b} for a,b in spans[0]],'metadata_moved':bool(annotation)})
        output.extend([report[cursor:block.start()],rendered]); cursor=end
    output.append(report[cursor:])
    return ''.join(output), audit
