"""Require the task's actual business scope, never an answer-shaped test key."""
import re
import json


def metric_client(metric):
    return next((f.get('value') for f in metric.get('filters', [])
                 if f.get('field') in ('client', 'client_alias')), None)


def requested_client(query, evidence):
    """Resolve a named client from recorded query filters; do not guess one."""
    from casework.entities import aliases, normalize
    names = set()
    for item in evidence:
        if item.get('casework'):
            try:
                params = json.loads(item.get('snippet') or '{}')
            except (ValueError, TypeError):
                params = None
            if isinstance(params, dict) and params.get('client'):
                names.add(params['client'])
        for metric in (item.get('statistics') or {}).get('metrics', []):
            if metric_client(metric):
                names.add(metric_client(metric))
        client = (item.get('answer_contract') or {}).get('client')
        if client:
            names.add(client)
    text = normalize(query)
    matched = [name for name in names if any(re.search(re.escape(alias)
                + (r'(?!\d)' if alias[-1:].isdigit() else ''), text)
                for alias in aliases(name, 'client') if alias)]
    if not matched and '客户' in query and any(item.get('casework') for item in evidence):
        # Reuse the published entity catalogue used by routing. A first broad
        # query cannot erase an explicitly named client from the original task.
        from casework.routing import known_entities
        matched = known_entities(query).get('client', [])
    return matched[0] if len(matched) == 1 else None


def task_evidence_issues(query, evidence, *, internal_casework=False):
    from casework.analytics import month_bounds
    from casework.routing import affirmative, months
    if not internal_casework and not any(item.get('casework')
            or re.search(r'\bcw_(?:cases|meetings)\b', str(item.get('sql') or ''), re.I)
            for item in evidence):
        return []
    text = affirmative(query)
    client = requested_client(query, evidence)
    issues = []
    monthly_period = re.search(r'每月|各月|月报|月度|月份|20\d{2}年\d{1,2}月(?!\d{1,2}日)|(?<!\d)20\d{2}-\d{2}(?![-\d])', text)
    if monthly_period and re.search(r'统计|每月|各月|月报|数量', text) and re.search(r'收案|交局', text):
        requested_months = months(text)
        fields = [('received_date', '实际收案')] if '收案' in text else []
        if '交局' in text:
            fields.append(('actual_filed_date', '已核实实际交局'))
        missing = []
        for month in requested_months:
            start, end = month_bounds(month)
            for field, label in fields:
                verified = any(metric.get('definition_verified')
                    and item.get('task_scope_compatible') is not False
                    and (not client or metric_client(metric) == client)
                    and metric.get('time', {}).get('field') == field
                    and metric['time'].get('start') == start.isoformat()
                    and metric['time'].get('end') == end.isoformat()
                    for item in evidence for metric in (item.get('statistics') or {}).get('metrics', []))
                if not verified:
                    missing.append(month + label)
        if missing:
            issues.append('月度数量缺少对应指标来源：' + '、'.join(missing)
                + '；请调用query_department_cases(operation=monthly,start_month=原问题起月,end_month=原问题止月,client=原问题客户)，'
                  '使用返回的逐月实际数量与完整事实表；原始案件行或案件总数不能替代月度指标依据，'
                  '不得删除用户要求的数字或写“以来源表为准”。')
    if re.search(r'初稿', text) and re.search(r'案号|所编号', text) and re.search(r'逾期|两周|14天', text):
        from tools.answer_contracts import _complete_due_sources
        sources = _complete_due_sources(evidence, query)
        requested = [name for name, pattern in [('overdue', '逾期'), ('next14', r'两周|14天'),
                    ('unknown', r'期限未知|未知案号|未填|期限为空')] if re.search(pattern, text)]
        if any(name not in sources for name in requested):
            issues.append('期限分组缺少完整明细依据：请用query_department_cases(operation=cases)读取原问题承办人、'
                '基准日及初稿期限条件下的完整案件明细；补齐逾期、未来14天和所要求的未知组。'
                '仅workload计数或表格预览不能代替分组案号；完整空组可说明无，不能以“有／见来源表”代替数量。')
    return issues
