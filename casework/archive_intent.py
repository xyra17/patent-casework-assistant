"""Evidence-bound search scope; ambiguity is a browse mode, never a filter."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SearchScope = Literal['ambiguous', 'mention', 'topic', 'mechanism']
SCOPE_LABELS = {'ambiguous': '未明确口径，按关联层次展示', 'mention': '提及该概念',
                'topic': '以该概念为核心主题', 'mechanism': '方案采用所问机制'}
RELATION_LABELS = {'topic': '核心主题', 'mechanism': '方案中使用', 'mention': '仅提及',
                   'unverified': '关联程度待核实', 'unrelated': '未发现直接关联'}


class ArchiveIntent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: SearchScope = 'ambiguous'
    quote: str = Field(default='', max_length=500, description='查询口径的用户逐字原话；仅说相关时必须ambiguous')
    purpose: Literal['browse', 'count', 'history', 'recommend'] = 'browse'
    purpose_quote: str = Field(default='', max_length=500)


def explicit_scope(query):
    """Only unambiguous surface forms; other phrasing is resolved semantically."""
    confirmed=re.findall(r'本次用户确认的案件查询口径：(提及该概念|以该概念为核心主题|方案采用所问机制)。',query)
    if confirmed:
        label=confirmed[-1]
        return next((m,label) for m,l in SCOPE_LABELS.items() if l==label)
    clauses = [c.strip() for c in re.split(r'[。；;\n]', query) if c.strip()]
    found = []
    patterns = {
        'mention': r'所有提及|(?:提到|提及|出现过).{0,60}(?:案件|材料|文件)|背景.{0,12}(?:也算|也要|包括)|只要.{0,30}(?:提到|提及|出现)',
        'topic': r'以.{1,60}为(?:核心|主要|主题)|主要(?:研究|讨论|讲)|围绕.{1,60}(?:展开|设计)',
        'mechanism': r'(?:采用|运用|使用).{1,60}(?:的案件|的方案|机制)|(?:满足|同时满足).{0,30}(?:技术条件|技术要点)|目标(?:技术)?机制|技术条件为',
    }
    for clause in clauses:
        # Do not classify a rejected search criterion as the requested scope.
        positive = '，'.join(c for c in re.split(r'[，,]', clause)
                            if not re.match(r'\s*(?:不要|不是|并非|不找|不查|不要求|不需要|不能|不应|别)', c))
        for mode, pattern in patterns.items():
            match = re.search(pattern, positive)
            if match: found.append((mode, match.group()))
    modes = {m for m, _ in found}
    return found[0] if len(modes) == 1 else ('ambiguous', '')


def bind_intent(query, intent=None):
    """Keep the original task separate from the shortened retrieval query."""
    result = (intent or ArchiveIntent()).model_copy(deep=True)
    if result.mode != 'ambiguous' and (not result.quote or result.quote not in query):
        raise ValueError('查询口径须绑定用户原话；仅“相关”不能推定主题或机制')
    if result.mode != 'ambiguous':
        from casework.routing import affirmative_quote
        if not affirmative_quote(query,result.quote):raise ValueError('被否定的查询口径不能成为筛选条件')
    if result.purpose != 'browse' and (not result.purpose_quote or result.purpose_quote not in query):
        raise ValueError('计数、经历或推荐用途须绑定用户原话')
    mode, quote = explicit_scope(query)
    if mode != 'ambiguous':
        if result.mode not in ('ambiguous', mode) and '本次用户确认的案件查询口径：' not in query:
            raise ValueError('查询口径与用户明确要求不一致')
        result.mode, result.quote = mode, quote
    elif result.mode != 'ambiguous' and re.fullmatch(r'(?:找|查|检索)?\s*.+?相关(?:的)?(?:案件|材料)?[。？?]?', result.quote):
        raise ValueError('“相关”没有明确提及、主题或机制口径')
    if result.purpose == 'browse':
        match = re.search(r'统计.{0,30}(?:多少|数量|案件)|(?:多少|数量).{0,15}(?:相关|案件)|(?:判断|证明|核实).{0,20}(?:经历|经验)|推荐.{0,20}(?:代理师|承办人|接案)', query)
        if match:
            result.purpose = 'count' if re.search(r'统计|多少|数量', match.group()) else 'recommend' if '推荐' in match.group() else 'history'
            result.purpose_quote = match.group()
    return result


def require_scope(intent, query, request):
    """Pause only when an ambiguous scope changes a downstream decision."""
    if intent.mode != 'ambiguous' or intent.purpose == 'browse':return
    from api.clarification import ClarificationRequired, search_scope_question, question_report
    question = search_scope_question(query, request)
    raise ClarificationRequired(question, question_report(question), [])
