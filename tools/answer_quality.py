"""Deterministic presentation checks; these do not certify technical reasoning."""
import re


def answer_checks(query, report, evidence):
    checks = []
    limits = re.findall(r'(?:最多|不超过|控制在)\s*(\d+)\s*字|(\d+)\s*字以内', query or '')
    if limits:
        limit = min(int(a or b) for a, b in limits)
        # A host-added notice is not part of the model's requested answer.
        body = re.sub(r'^\*\*专业判断使用边界（系统说明）\*\*[^\n]*\n\n', '', report)
        body = re.sub(r'https?://\S+|\[S\d+\]', '', body)
        count = len(re.findall(r'[\u4e00-\u9fff]|[A-Za-z0-9]+', body))
        checks.append({'code': 'answer_length', 'status': 'failed' if count > limit else 'passed',
                       'detail': f'答复长度{count}字，用户上限{limit}字；汉字逐字、英文及数字连续串按词计，链接与系统提示不计。'})
    by_id = {e.get('id'): e for e in evidence}
    for line in report.splitlines():
        for match in re.finditer(r'字符\s*(\d+)\s*[–—－-]\s*(\d+)', line):
            tail = re.search(r'\[(S\d+)\]', line[match.end():])
            if not tail:
                continue
            sid = tail[1]
            source = by_id.get(sid, {})
            left, right = map(int, match.groups())
            relative = bool(re.search(r'(?:片段|摘录)内\s*$', line[max(0, match.start()-12):match.start()]))
            if relative:
                valid = 0 <= left < right <= len(source.get('snippet') or source.get('content') or '')
                known = bool(source.get('snippet') or source.get('content'))
            else:
                page = re.findall(r'第\s*(\d+)\s*页', line[:match.start()])
                ranges = []
                for span in source.get('spans') or []:
                    if page and not re.search(r'第\s*'+page[-1]+r'\s*页', str(span.get('location', ''))):
                        continue
                    if isinstance(span.get('start'), int) and isinstance(span.get('end'), int):
                        ranges.append((span['start']+1, span['end']))
                if not ranges and (not page or re.search(r'第\s*'+page[-1]+r'\s*页', str(source.get('location') or ''))):
                    loc = re.search(r'字符\s*(\d+)\s*[–—－-]\s*(\d+)', str(source.get('location') or ''))
                    if loc:
                        ranges.append(tuple(map(int, loc.groups())))
                # Unknown locator formats do not establish a character range.
                known = bool(ranges) or bool(page and (source.get('spans') or re.search(r'第\s*\d+\s*页', str(source.get('location') or ''))))
                valid = left >= 1 and any(a <= left <= right <= b for a, b in ranges)
            if known:
                checks.append({'code': 'source_character_location', 'status': 'passed' if valid else 'failed',
                               'evidence_ids': [sid], 'detail': f'[{sid}]字符{left}–{right}'+
                               ('位于来源记录范围内；此项不替代引文核对。' if valid else
                                '超出所引来源的位置范围；片段内偏移不能冒充原页字符位置，请使用工具返回的位置。')})
    return checks


def answer_issues(query, report, evidence):
    return [c['detail'] for c in answer_checks(query, report, evidence) if c['status'] == 'failed']
