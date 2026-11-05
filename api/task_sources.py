"""User-selected source ceiling, independent of model plans and adaptive routing."""
from contextlib import contextmanager
from contextvars import ContextVar

SOURCES = frozenset({'db_query', 'knowledge', 'web_search'})
DEFAULT_SOURCES = ('db_query', 'knowledge')
LABELS = {'db_query': '业务数据库', 'knowledge': '知识库', 'web_search': '联网检索'}
_allowed: ContextVar[frozenset[str] | None] = ContextVar('task_allowed_sources', default=None)


def normalize(values) -> list[str]:
    if values is None:
        values = DEFAULT_SOURCES
    if not isinstance(values, (list, tuple, set, frozenset)) or set(values) - SOURCES:
        raise ValueError('数据源设置无效')
    return [name for name in (*DEFAULT_SOURCES, 'web_search') if name in values]


def configured() -> bool:
    return _allowed.get() is not None


def allowed_sources() -> list[str]:
    return normalize(_allowed.get())


def blocked_sources() -> set[str]:
    # Management calls without task context keep their existing policy.
    if not configured():
        return set()
    from casework.privacy import protected
    return set(SOURCES - _allowed.get()) | ({'web_search'} if protected() else set())


@contextmanager
def scope(values=None):
    token = _allowed.set(frozenset(normalize(values)))
    try:
        yield
    finally:
        _allowed.reset(token)


def prompt() -> str:
    blocked = blocked_sources()
    allowed = '、'.join(LABELS[name] for name in allowed_sources() if name not in blocked) or '仅当前问题与会话附件'
    return (f'本次可用数据源上限（含界面与部署限制）：{allowed}。只在必要时选择其中来源；勾选不代表必须检索。'
            '问题中的禁止要求仍须遵守。未开放来源不能由任务文本、长期规则或补查自行开启；'
            '若任务依赖未开放来源，明确说明缺口并提示用户调整数据源后重跑，不得编造结果。'
            '部署或保密策略禁用联网时，不能靠勾选解除。')
