"""Keep evidence review, requirement coverage and delivery decisions independent.

These are internal review outcomes, never an independent accuracy measurement.
Only a review bound to the exact returned text can supply a content verdict.
"""
from __future__ import annotations

import hashlib


def report_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _axis(checks: list[dict], criteria: set[str], required: str, detail: str) -> dict:
    selected = [c for c in checks if c.get('criterion') in criteria]
    applicable = [c for c in selected if c.get('status') != 'not_required']
    if any(c.get('status') == 'failed' for c in applicable):
        state = 'failed'
    elif (any(c.get('criterion') == required and c.get('status') == 'passed' for c in applicable)
          and applicable and all(c.get('status') == 'passed' for c in applicable)):
        state = 'passed'
    else:
        state = 'unverified'
    return {'status': state, 'basis': 'internal_review', 'detail': detail,
            'checks': selected}


def assess_result(run: dict) -> dict:
    events = run.get('events') or []
    summary = next((e.get('data') or {} for e in reversed(events)
                    if e.get('event') == 'quality_summary'), {})
    text = run.get('result') or ''
    digest = report_hash(text)
    reflections = [e.get('data') or {} for e in events if e.get('event') == 'reflection']
    returned_round = summary.get('returned_round')
    selected = [r for r in reflections if r.get('candidate_report_ref') == digest
                and (returned_round is None or r.get('round') == returned_round)]
    review = selected[-1] if selected else {}
    checks = (review.get('verification') or {}).get('checks') or []
    # A fallback can retain a different candidate than the last round, then
    # append/change text. Its old review must not certify the new final artifact.
    reason = ('仅为内部来源核对，不代表独立准确率评测。' if review else
              '未找到与最终正文完全对应的分项审查；可能尚未审查或正文经兜底改写。')
    facts = _axis(checks, {'evidence_support', 'business_rules', 'consistency'},
                  'evidence_support', reason)
    # Fixed workflows already seal program-verified claims to both the report
    # and the complete evidence snapshot. They do not emit a model V2 check.
    # Reuse that proof without overriding a failed semantic check or certifying
    # a rewritten fallback. A bare business_check.status is never sufficient.
    bound = review.get('business_check') or {}
    if facts['status'] == 'unverified' and bound.get('status') == 'passed':
        from casework.store import digest as evidence_digest
        claims = bound.get('claims') or []
        if (bound.get('report_sha256') == digest
            and bound.get('evidence_sha256') == evidence_digest(run.get('evidence') or [])
            and (bound.get('source_binding') or {}).get('status') == 'passed'
            and not bound.get('issues') and claims and all(c.get('verified') is True for c in claims)):
            facts.update(status='passed', basis='sealed_program_contract',
                detail='程序已将客观声明、原件来源、完整证据快照及最终正文哈希绑定；不包含专业技术等价性确认。')
    completeness = _axis(checks, {'task_completion'}, 'task_completion',
                         '按用户要求逐项审查；与事实正确性分别记录。' if review else reason)
    status = run.get('status')
    unfinished = any(marker in text for marker in ('## 未完成部分', '## 兜底结果说明', '【未达标说明】'))
    if status == 'completed':
        if run.get('degraded') or unfinished or summary.get('passed') is False:
            state, label = 'partial', '部分交付'
        elif summary.get('passed') is True:
            state, label = 'released', '已放行'
        else:
            state, label = 'unverified', '已返回，放行依据未记录'
    else:
        state = {'failed': 'failed', 'cancelled': 'cancelled',
                 'awaiting_input': 'awaiting_input', 'running': 'running'}.get(status, 'unverified')
        label = {'failed': '未交付', 'cancelled': '已取消', 'awaiting_input': '待确认',
                 'running': '执行中', 'unverified': '交付状态未记录'}[state]
    # Do not certify an empty/running/failed output using a coincidental old hash.
    if status != 'completed' or not text:
        for axis in (facts, completeness):
            axis.update(status='unverified', checks=[], detail='最终交付尚未形成，未作分项结论。')
    from tools.product_boundary import assessment
    product = assessment(run, review, facts, completeness, {'status':state})
    return {'schema_version': 2, 'product_boundary':product, 'report_sha256': digest,
            'facts': facts, 'completeness': completeness,
            'delivery': {'status': state, 'label': label,
                         'stop_reason': summary.get('stop_reason') or run.get('stop_reason') or '',
                         'issues': summary.get('issues') or [],
                         'detail': '交付状态只表示系统处理结果，不等同于内容正确或任务完整。'},
            'review_round': review.get('round'), 'independent_review': False}
