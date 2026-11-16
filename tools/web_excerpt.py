"""Select literal topical passages from the text actually returned by a provider."""
import re


def focus_excerpt(text, focus, budget=3600):
    budget = max(0, int(budget))
    terms = list(dict.fromkeys(re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}', focus)))[:12]
    hits = list(re.finditer('|'.join(re.escape(t) for t in terms), text, re.I)) if terms else []
    selected = [(0, min(len(text), budget))] if text and budget else []
    selection = 'prefix_no_focus_match' if terms else 'prefix'
    if hits and budget:
        windows = []
        # Keep a matching Markdown section together, including parameter lists
        # whose individual lines may not repeat the search term.
        headings = list(re.finditer(r'(?m)^(#{1,6})[ \t]+[^\n]+', text))
        for index, heading in enumerate(headings):
            if not any(re.search(re.escape(t), heading[0], re.I) for t in terms):
                continue
            end = next((h.start() for h in headings[index+1:] if len(h[1]) <= len(heading[1])), len(text))
            a, b = heading.start(), min(end, heading.start()+budget)
            passage = text[a:b]
            windows.append((100+sum(len(re.findall(re.escape(t), passage, re.I)) for t in terms), a, b))
        for hit in hits[:100]:
            a = max(0, text.rfind('\n\n', 0, hit.start()))
            if hit.start()-a > 350:
                a = max(hit.start()-250, text.rfind('\n', max(0, hit.start()-250), hit.start()))
            b = text.find('\n\n', hit.end()+450)
            b = min(len(text) if b < 0 else b, a+min(1200, budget))
            if hit.end() > b:
                a = max(0, hit.start()-min(200, budget//4))
                b = min(len(text), a+budget, hit.end()+800)
            passage = text[a:b]
            score = sum(len(re.findall(re.escape(t), passage, re.I)) for t in terms)-passage.count('](')*2
            windows.append((score, a, b))
        selected, used = [], 0
        for _, a, b in sorted(windows, reverse=True):
            if a >= b or any(a < y and b > x for x, y in selected) or used+b-a > budget:
                continue
            selected.append((a, b))
            used += b-a
            if len(selected) == 3:
                break
        selected.sort()
        selection = 'literal_focus_windows'
    excerpt = '\n\n[…原文中间部分未展示…]\n\n'.join(text[a:b] for a, b in selected)
    return excerpt, {'selection': selection, 'focus_found': bool(hits) if terms else None,
        'focus_in_excerpt': any(re.search(re.escape(t), excerpt, re.I) for t in terms) if terms else None,
        'truncated': sum(b-a for a, b in selected) < len(text),
        'offset_scope': 'provider_text_zero_based_half_open',
        'source_spans': [{'start': a, 'end': b} for a, b in selected]}
