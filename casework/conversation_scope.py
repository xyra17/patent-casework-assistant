"""Host-bound case selection for a follow-up; never a model supplied permission."""
from contextlib import contextmanager
from contextvars import ContextVar

_cases = ContextVar('conversation_case_scope', default=None)
_conditions = ContextVar('conversation_conditions', default=None)


def current():
    return _cases.get()


def allows(case_id):
    selected = current()
    return selected is None or str(case_id).upper() in selected


def conditions():
    return _conditions.get() or {}


def restricted():
    return current() is not None or any(conditions().get(key) for key in (
        'owner','client','stage','domain','due_field','due_status','missing_date_field','as_of'))


def allows_case(case):
    """Apply confirmed entity/stage/deadline filters even when a model omits a tool slot."""
    if not allows(case.get('case_id')):
        return False
    values = conditions()
    from casework.entities import normalize, aliases
    from casework.domains import canonical
    for field, key in (('owner','owner_name'), ('client','client_alias'), ('domain','technical_domain')):
        wanted = values.get(field)
        if wanted:
            actual = case.get(key) or ''
            if field == 'domain':
                if canonical(wanted) != canonical(actual): return False
            elif normalize(wanted) not in aliases(actual, field):
                return False
    from casework import analytics
    base = analytics.as_date(values.get('as_of'))
    if values.get('stage') and not analytics.stage_matches(case, values['stage'], base):
        return False
    if values.get('missing_date_field') and not analytics.actual_date_unknown(case,values['missing_date_field']):
        return False
    if values.get('due_status'):
        from casework.requirements import due_group
        if values.get('due_field') not in analytics.DUE_FIELDS:
            raise ValueError('继承的期限条件缺少有效期限节点，请明确初稿、送审或向局提交期限')
        if not analytics.active(case,base) or due_group(case.get(values['due_field']),base.isoformat()) != values['due_status']:
            return False
    return True


@contextmanager
def scope(case_ids, confirmed_conditions=None):
    token = _cases.set(None if case_ids is None else frozenset(c.upper() for c in case_ids))
    condition_token = _conditions.set(confirmed_conditions or {})
    try:
        yield
    finally:
        _conditions.reset(condition_token)
        _cases.reset(token)
