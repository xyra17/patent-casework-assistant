"""Validate the finalized result without generating a second, abbreviated answer."""
from __future__ import annotations

import hashlib
import re
from tools.evidence_refs import citation_issues, report_references


def payload(content='', evidence_ids=None, mode='unavailable'):
    return {'content':content,'evidence_ids':evidence_ids or [],'status':'ready' if content else 'unavailable','mode':mode}


def business_review_from_run(run):
    """Use the last persisted review from this execution, never an earlier pass."""
    request = (run.get('plan') or {}).get('department_request')
    if not request:
        return None
    latest = next((event.get('data', {}) for event in reversed(run.get('events', []))
                   if event.get('event') == 'reflection'), {})
    return {**latest, 'planned_request': request}


def verified_business_result(query, report, evidence, review):
    """A sealed fixed answer already contains the requested facts and limits.

    Reuse it intact rather than asking a second model to select fragments or
    re-judge deterministic tables. This path cannot rescue a failed review.
    """
    from casework.workflow import DepartmentRequest
    from casework.verification import language_issues, task_contract_issues

    check = review.get('business_check') or {}
    result = {**payload(), 'business_validation_version': 1,
              'business_report_sha256': hashlib.sha256(report.encode()).hexdigest()}
    if (check.get('status') != 'passed' or check.get('issues') or review.get('hard_issues')
            or review.get('soft_issue') or review.get('soft_review_status') != 'passed'
            or check.get('report_sha256') != result['business_report_sha256']):
        return result
    try:
        request = DepartmentRequest.model_validate(check['request'])
        if request != DepartmentRequest.model_validate(review['planned_request']):
            return result
        issues = language_issues(request, report) + task_contract_issues(query, request, review.get('business_delivery', {}))
        if issues:
            return {**result, 'validation_issues': issues, 'requires_rerun': True}
    except (KeyError, TypeError, ValueError):
        return result
    refs = sorted(report_references(report), key=lambda i: int(i[1:]))
    if citation_issues(report, evidence):
        return result
    return {**result, **payload(report, refs, 'verified_business'),
            'business_check': {'status': 'passed', 'checked_claims': check.get('checked_claims', 0)},
            'statistics_check': review.get('statistics_check')}


async def checked_projection(candidate, evidence):
    if not candidate.get('content'):
        return candidate
    if citation_issues(candidate['content'], evidence):
        return payload()
    refs = set(candidate.get('evidence_ids', []))
    version_checks = [{'evidence_id': item.get('id'), **item['applicability']}
                      for item in evidence if item.get('applicability') and (not refs or item.get('id') in refs)]
    if version_checks:
        candidate = {**candidate, 'knowledge_applicability': version_checks}
    if not any(item.get('type') == 'database' for item in evidence):
        return candidate
    from tools.statistics_check import check_statistics
    check = await check_statistics(candidate['content'], evidence)
    if check['status'] in ('failed', 'unverified'):
        candidate = payload()
    elif check.get('checks'):
        candidate['evidence_ids'] = list(dict.fromkeys([*candidate['evidence_ids'],
            *(source['evidence_id'] for claim in check['checks'] for source in claim['sources'] if source.get('evidence_id'))]))
    return {**candidate, 'statistics_check': check, 'statistical_validation_version': 1}


async def build_query_result(query, report, evidence, degraded=False, *, business_review=None):
    if not report.strip() or degraded:
        return payload()
    if business_review is not None:
        return verified_business_result(query, report, evidence, business_review)
    # Preserve the source check for explicitly requested quotations, without
    # replacing the finalized body with a separate quotation-only answer.
    if re.search(r'(引用|摘录|给出|提供).{0,12}(一句|一段).{0,8}原文|引用一句', query):
        quotes = re.findall(r'^>\s*[“「"]?(.+?)[”」"]?\s*$', report, re.M)
        if len(quotes) == 1:
            quote = re.sub(r'\s+', '', quotes[0].strip('“”「」" '))
            if not any(quote in re.sub(r'\s+', '', item.get('snippet', '')) for item in evidence):
                return payload()
    refs = sorted(report_references(report), key=lambda item: int(item[1:]))
    return await checked_projection(payload(report, refs, 'final_result'), evidence)
