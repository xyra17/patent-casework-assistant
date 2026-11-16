"""Literal definition receipts and narrow contradictions about acquired content.

A receipt proves only that the selected source contains this definition sentence.
It does not establish defaults, measurements, implementation or full-page coverage.
"""
import re

_IDENTIFIER = r'[A-Za-z_][A-Za-z0-9_.-]{0,60}'
_DEFINITION = re.compile(r'`('+_IDENTIFIER+r')`\s+(?:is|means|refers to)\s+[^\n]+|`('+_IDENTIFIER+r')`\s*(?:表示|是指|指的是|为|定义为)[^\n]+', re.I)
_LIST = r'(?:`?'+_IDENTIFIER+r'`?)(?:\s*(?:、|，|,|及|和|与|/)\s*`?'+_IDENTIFIER+r'`?)*'
_FACET = r'\s*(?:等\s*)?(?:参数|术语|字段|指标)?\s*的?\s*(?:完整)?(?:条目)?(?:定义|含义|条目|说明|原文|正文|内容)(?:原文)?'
_ABSENT = r'(?:均|都|仍|尚|还|本次)*\s*(?:未获取|未取得|未拿到|未读取|没有取得|没有获取|未获得)'
_POST = re.compile('(?P<terms>'+_LIST+')'+_FACET+r'(?:和该页其余段落|及其余段落)?\s*'+_ABSENT)
_PRE = re.compile(_ABSENT+r'\s*(?P<terms>'+_LIST+')'+_FACET)


def defined_terms(text, limit=12):
    rows, seen = [], set()
    for match in _DEFINITION.finditer(text or ''):
        term = match[1] or match[2]
        if term in seen:
            continue
        quote = match[0]
        if len(quote) > 500:
            continue
        seen.add(term)
        rows.append({'term': term, 'quote': quote, 'start': match.start(), 'end': match.end(),
                     'offset_scope': 'evidence_snippet_zero_based_half_open'})
        if len(rows) == limit:
            break
    return rows


def acquired_content_contradictions(report, items):
    sources = [e for e in items if e.get('type') == 'web' and e.get('snippet')
               and e.get('task_scope_compatible') is not False]
    issues = []
    for line in report.splitlines():
        # A source quotation isn't the assistant's acquisition claim.
        if line.lstrip().startswith('>'):
            continue
        text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', '', line.replace('**', ''))
        for match in list(_POST.finditer(text)) + list(_PRE.finditer(text)):
            prefix = text[:match.start()].rstrip()
            if re.search(r'(?:不能|不应|不得|不要|并非|不是)(?:声称|说|称为|写成)?\s*$', prefix):
                continue
            # Explicit refs restrict the source object; don't merge a term from
            # another page. Without refs, only a single-page context is definite.
            refs = set(re.findall(r'\[(S\d+)\]', line))
            selected = [e for e in sources if e.get('id') in refs] if refs else sources
            if not refs and len({e.get('url') for e in sources}) != 1:
                continue
            names = re.findall(_IDENTIFIER, match['terms'].replace('`', ''))
            for source in selected:
                for row in defined_terms(source['snippet']):
                    if row['term'] in names:
                        issues.append({'code': 'acquired_definition_denied', 'evidence_id': source.get('id'),
                            'term': row['term'], 'claim': match[0], 'answer_quote':line, 'quote': row['quote'],
                            'detail': f"已取得内容被误报为缺失：[{source.get('id')}]已有{row['term']}的定义原文“{row['quote']}”；不能称该定义或条目未取得。已取得定义不代表默认值、实测结果或完整页面已取得。"})
    return issues


def acquired_content_issues(report, items):
    return list(dict.fromkeys(row['detail'] for row in acquired_content_contradictions(report, items)))


REVIEW_INSTRUCTION = ('缺口说明也属于事实陈述，必须对照实际已取得的来源逐项核对。'
    '未选作引文、未要求解释、没有单列、没有取得整页均不等于对应已见段落未取得；'
    '将已见定义说成未获取是实际矛盾，须判failed并修订，不能以“过度保守”“不影响两条主结论”放行。'
    '反之，看到术语或取得定义不证明默认值、取值范围、实现或实测效果，真实缺口仍须保留。')
