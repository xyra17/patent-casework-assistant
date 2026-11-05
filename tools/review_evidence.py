"""Budgeted review context that reserves space for every cited source."""
import json
import re


def verified_statistics_context(result):
    """Expose only cells actually checked, with exact source metric definitions."""
    checked = result.get('statistics_check') or {}
    if checked.get('status') != 'passed':
        return {}
    evidence = {item.get('id'): item for item in result.get('evidence', [])}
    cells = []
    for claim in checked.get('checks', []):
        for binding in claim.get('sources', []):
            source = evidence.get(binding.get('evidence_id'), {})
            metric = next((m for m in (source.get('statistics') or {}).get('metrics', [])
                           if m.get('column') == binding.get('column') and m.get('definition_verified')), None)
            if not metric:
                continue
            cells.append({'metric_text': claim.get('metric_text'), 'value_text': claim.get('value_text'),
                          'value_span': claim.get('value_span'), 'unit_id': claim.get('unit_id'),
                          'source': binding, 'source_metric': metric})
    return {'checked_cells': cells, 'scope': '仅证明这些原句数值与来源单元格、单位、时间及维度一致；'
            '不证明查询定义正确、资料全量或任务完成。不要要求用户未要求的明细、原因说明作为这些数值成立的附加条件。'}


def project_case_review_evidence(result, checked):
    """Join review rows to every provided technical candidate, not just matches.

    The original evidence and calculation are unchanged. Unrelated ledger rows
    cannot establish a technical mechanism. Extra fields on candidate rows are
    retained so the full reviewer can still assess client/owner/date filters.
    """
    items = result.get('evidence', [])
    if checked.get('status') != 'passed':
        return items
    from tools.case_records import object_array
    calc = checked['calculation']
    lookup = {item['id']: item for item in items}
    documents = lookup.get(calc.get('documents_evidence_id'))
    ledger = lookup.get(calc.get('ledger_evidence_id'))
    if not documents or not ledger:
        return items
    ids = {row['case_id'] for row in object_array(documents)}
    rows = object_array(ledger)
    included = [row for row in rows if row['case_id'] in ids]
    metadata = {'kind':'technical_candidate_ledger_join', 'technical_case_ids':sorted(ids),
                'original_ledger_rows':len(rows), 'included_rows':len(included),
                'missing_ledger_case_ids':sorted(ids - {r['case_id'] for r in included}),
                'scope':'仅为审查投影，原案件信息未修改；覆盖全部所提供技术候选，包含未被执行选择的候选。'
                        '其他案件信息行没有在本次材料中证明该技术机制，不能因有交局日期而视为相关案。'
                        '本投影不证明全库技术覆盖，额外筛选及全库穷尽要求仍须独立核对。'}
    projected = {**ledger, 'content':json.dumps(included, ensure_ascii=False),
                 'content_complete':False, 'snippet':metadata['scope'], 'review_projection':metadata}
    for key in ('rows', 'columns', 'completeness'):
        projected.pop(key, None)
    return [projected if item['id'] == ledger['id'] else item for item in items]


def pack_review_evidence(items, report, budget=20000):
    from tools.evidence_refs import report_references
    references = report_references(report)
    available = {item.get('id') for item in items}
    cited = [item for item in items if item.get('id') in references]
    other = [item for item in reversed(items) if item.get('id') not in references]
    index = {'available_ids': [item.get('id') for item in items],
             'missing_reference_ids': sorted(references - available),
             'note': '摘录截断不等于来源不存在；不可见内容仍未核实。'}
    prefix = json.dumps(index, ensure_ascii=False) + '\n'
    if len(prefix) > budget // 3:
        prefix = '来源索引超过预算；下列仅含可见摘录，未展示来源不能判为不存在。\n'
    blocks = []
    remaining = budget - len(prefix)
    ordered = cited + other
    for position, item in enumerate(ordered):
        # Reserve a fair share for *all* cited sources, rather than filling the
        # context with the first table preview and dropping later citations.
        pending_cited = max(1, len(cited) - position)
        quota = remaining // pending_cited if position < len(cited) else remaining
        if quota < 120: break
        keys = ('id', 'type', 'title', 'case_id', 'owner_name', 'source', 'file_id', 'current_snapshot', 'location', 'revision', 'applicability',
                'comparison_scope','comparison_counts','comparison_locations','review_projection', 'content', 'content_complete', 'snippet', 'sql', 'params', 'columns', 'rows', 'completeness', 'rule_versions','answer_contract')
        compact = {key: item[key] for key in keys if item.get(key) is not None}
        if compact.get('content_complete') and compact.get('content'):compact.pop('snippet',None)
        body = json.dumps(compact, ensure_ascii=False, default=str)
        if len(body) + 2 > quota:
            # Preserve the excerpt as text and mark it, never present a cut JSON
            # object as a complete evidence record.
            label = f"[证据 {item.get('id')}] 摘录（已截断，完整来源仍已保存）：\n"
            body = label + body[:max(0, quota - len(label) - 20)] + '\n【摘录结束；其后未展示】'
        block = body + '\n\n'
        if len(block) > remaining: break
        blocks.append(block)
        remaining -= len(block)
    return prefix + ''.join(blocks)
