"""Conservative lexical guard for explicit mechanism-change assertions.

Quoted material is kept inside its clause and hidden from assertion matching.
Negation and text-only subjects apply to the matched claim, not its whole paragraph.
This is a guard, not a semantic certificate for the answer.
"""
import re

_TOKEN = re.compile(r'“[^”\n]*”|「[^」\n]*」|『[^』\n]*』|"[^"\n]*"|`[^`\n]*`|[。；;，,\n]|(?:但是|然而|不过|但|却)')
_CHANGE = re.compile(r'(?:机制|规则|触发条件|时机|效果).{0,40}?(?:改变|替换|转为|改为|变更为)')
_NEGATION = re.compile(r'(?:不能|无法|不足以|尚不能|未能)(?:(?:单独|直接|据此|仅据此|由此))*'
                       r'(?:证明|认定|确定|确认|判定)(?:发生了?|存在|实现了?|已经|其)?$')
_TEXT_SUBJECT = re.compile(r'(?:原句|原文表述|文字|文本|字句|措辞|句子|文字描述)(?:层面)?(?:中|的)?(?:仅|只)?$')


def _clauses(text):
    start = 0
    for token in _TOKEN.finditer(text):
        if token[0].startswith(('“', '「', '『', '"', '`')):
            continue
        yield text[start:token.start()]
        start = token.end()
    yield text[start:]


def comparison_change_issues(query, report):
    if not re.search(r'逐条比较|两侧原句|基准.*修订|修订.*基准', query):
        return []
    for clause in _clauses(report.replace('**', '')):
        # Match assertions outside quotations; quoted mechanisms remain source
        # data. Outer subjects such as “机制由‘A’改为‘B’” are still checked.
        def quote_data(found):
            value = found[0][1:-1]
            return value if value in ('机制','规则','触发条件','时机','效果') or found[0].startswith('`') else '引文'
        plain = re.sub(r'“[^”\n]*”|「[^」\n]*」|『[^』\n]*』|"[^"\n]*"|`[^`\n]*`', quote_data, clause)
        for match in _CHANGE.finditer(plain):
            before, after = plain[:match.start()].rstrip(), plain[match.end():].lstrip()
            negative_answer = bool(re.search(r'能否(?:单独|直接)?(?:证明|认定|确定|确认)$', before)
                                   and re.match(r'[:：]\s*(?:不能|无法|尚不能|未能|不足以)', after))
            question = bool(re.match(r'(?:机制|规则|触发条件|时机|效果)(?:是否|能否)', match[0])
                            and not re.match(r'[:：]\s*(?:是|可以|能|已确认)', after))
            if (negative_answer or _NEGATION.search(before) or re.search(r'(?:没有|并未|未曾|未)(?:发生|实现|确认)?$', before)
                    or re.search(r'(?:可能|或许|不能排除)$', before) or re.match(r'(?:尚未|未能|无法)(?:确定|确认|认定|证明)', after)
                    or question):
                continue
            # Only the subject directly attached to the change can make it a
            # textual change. Mentioning “文字” elsewhere grants no exemption.
            operator = re.search(r'由|从', match[0])
            if operator:
                subject = plain[:match.start()+operator.start()].rstrip()
                if _TEXT_SUBJECT.search(subject):
                    continue
            if re.match(r'(?:机制|规则|触发条件|时机|效果)(?:的)?(?:原文表述|文字描述|措辞|描述|表述)(?:由|从)', match[0]):
                continue
            return ['文本比较越界：已发现行级文字差异，但未见原文直接支持机制/因果替换关系；应限定为文字变化或保留无法确定。']
    return []
