"""Resolve exact role-specific aliases without dropping or broadening a filter."""
import re
import unicodedata
from casework.domains import canonical as canonical_domain


def normalize(value):
    value = unicodedata.normalize('NFKC',value)
    return re.sub(r'\s+|\(模拟\)','',value).casefold()


def aliases(value, field):
    full=normalize(value)
    result={full}
    if re.search(r'[（(]模拟[）)]',value) and full.startswith('测试'):
        result.add(full[2:])
    for alias in list(result):
        if field=='client' and alias.startswith('客户'):result.add(alias[2:])
        if field=='ipr':
            if alias.startswith('ipr'):result.update({alias[3:],alias[3:]+'ipr'})
            if alias.endswith('ipr'):result.update({alias[:-3],'ipr'+alias[:-3]})
    return result


def bind(value, choices, field):
    if value is None:return None
    wanted=normalize(value)
    hits=[]
    for choice in choices:
        candidates=aliases(choice,field)
        if field=='domain':
            if canonical_domain(value)==canonical_domain(choice):candidates.add(wanted)
        if wanted in candidates:hits.append(choice)
    if len(hits)!=1:
        raise ValueError(field+'筛选未能唯一绑定，请使用目录规范值：'+'、'.join(sorted(choices)[:20]))
    return hits[0]


def bind_filters(params):
    from casework import store,catalog
    from casework.conversation_scope import scope
    # Identity resolution uses the published directory; task results remain filtered.
    with scope(None):
        cases=store.list_cases()
        iprs={p for m in catalog.meetings() for p in m['iprs']}
    choices={field:{c[key] for c in cases if c.get(key)} for field,key in [
        ('client','client_alias'),('owner','owner_name'),('domain','technical_domain')]}
    choices['ipr']=iprs
    result=dict(params)
    for field in choices:
        if params.get(field):result[field]=bind(params[field],choices[field],field)
    if params.get('clients'):result['clients']=[bind(v,choices['client'],'client') for v in params['clients']]
    return result
