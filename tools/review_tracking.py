"""Application-owned issue identities and persisted, evidence-aware review history."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json


def _evidence_signatures(evidence: list[dict]) -> dict[str, str]:
    # Only content fields count; refreshed timestamps/round numbers do not prove new evidence.
    fields = ('content', 'text', 'snippet', 'excerpt', 'columns', 'rows', 'sql', 'params', 'query', 'url',
              'source', 'file_id', 'location', 'revision', 'rule_versions', 'applicability',
              'completeness', 'matched_rows', 'total_rows', 'result_rows')
    return {item.get('id', ''): hashlib.sha256(json.dumps(
        {key: item[key] for key in fields if key in item}, sort_keys=True,
        ensure_ascii=False, default=str).encode()).hexdigest() for item in evidence}


def _evidence_hash(evidence: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sorted(set(_evidence_signatures(evidence).values()))).encode()).hexdigest()


def pending_followups(ledger: list[dict]) -> list[dict]:
    return [{key: item.get(key) for key in ('issue_id', 'criterion', 'task_quote', 'detail', 'action', 'suggestion')}
            for item in ledger if item['kind'] == 'content' and item['status'] == 'pending']


def update_issues(ledger: list[dict], result: dict, hard_issues: list[str], round_num: int) -> list[dict]:
    ledger = deepcopy(ledger)
    verification = result.get('verification') or {}
    checks = verification.get('checks', [])
    report_hash = hashlib.sha256(result.get('report', '').encode()).hexdigest()
    evidence_hash = _evidence_hash(result.get('evidence', []))
    evidence_signatures = _evidence_signatures(result.get('evidence', []))

    def record(item: dict, outcome: str, detail: str, check: dict | None = None) -> None:
        event = {'round': round_num, 'outcome': outcome, 'detail': detail}
        if check:
            event.update(check_id=check['id'], evidence_ids=check.get('evidence_ids', []),
                         answer_quote=check.get('answer_quote', ''), review_status=check['status'])
        item['history'].append(event)

    for item in ledger:
        if item['status'] != 'pending':
            continue
        if item['kind'] == 'hard':
            if item['detail'] not in hard_issues:
                item['status'] = 'resolved'
                record(item, 'corrected', '程序重新检查，该问题已通过。')
            else:
                record(item, 'pending', '程序检查仍未通过。')
        elif item['kind'] == 'review_service':
            if verification.get('status') in ('passed', 'failed') and checks:
                item['status'] = 'resolved'
                record(item, 'rechecked', '内容审查已完成；各项问题分别记录。')
            else:
                record(item, 'pending', '内容审查仍未完成。')
        else:
            linked = next((check for check in checks if item['issue_id'] in check.get('followup_ids', [])), None)
            if not linked:
                record(item, 'unverified', '本轮未完成该问题的明确复核，保留待处理。')
            elif linked['status'] == 'passed':
                item['status'] = 'resolved'
                outcome = 'rechecked'
                if item['action'] == 'retrieve_evidence' and any(
                    evidence_signatures.get(source) and evidence_signatures[source] not in item['evidence_signatures']
                    for source in linked.get('evidence_ids', [])):
                    outcome = 'evidence_added'
                elif item['action'] == 'revise_answer' and report_hash != item['report_hash']:
                    outcome = 'corrected'
                record(item, outcome, linked['detail'], linked)
            else:
                item.update(action=linked['action'], suggestion=linked['suggestion'], reason=linked.get('reason', 'none'))
                record(item, 'pending', linked['detail'], linked)

    def create(kind: str, suffix: str, detail: str, **extra) -> None:
        item = {'issue_id': f'R{round_num}-{suffix}', 'kind': kind, 'detail': detail,
                'status': 'pending', 'first_round': round_num, 'history': [],
                'report_hash': report_hash, 'evidence_hash': evidence_hash, **extra}
        item['evidence_signatures'] = sorted(set(evidence_signatures.values()))
        record(item, 'pending', detail)
        ledger.append(item)

    for index, issue in enumerate(hard_issues, 1):
        if not any(item['kind'] == 'hard' and item['status'] == 'pending' and item['detail'] == issue for item in ledger):
            create('hard', f'H{index}', issue, label='程序硬校验')
    for check in checks:
        if check['status'] in ('failed', 'unverified') and not check.get('followup_ids'):
            create('content', check['id'], check['detail'],
                   **{key: check.get(key) for key in ('criterion', 'label', 'task_quote', 'answer_quote',
                                                      'action', 'suggestion', 'reason', 'evidence_ids')})
    if verification.get('status') == 'unverified' and not checks:
        if not any(item['kind'] == 'review_service' and item['status'] == 'pending' for item in ledger):
            create('review_service', 'SERVICE', verification.get('detail', '内容审查未完成'),
                   label='内容审查未完成', reason='verification_unavailable')
    return ledger


def finish_issues(ledger: list[dict], stop_reason: str, returned_round: int | None = None) -> list[dict]:
    result = deepcopy(ledger)
    for item in result:
        if item['status'] == 'pending':
            item['stop_reason'] = stop_reason
        if returned_round is not None:
            item['returned_report_unverified'] = item['first_round'] > returned_round or (
                item['status'] == 'resolved' and item['history'][-1]['round'] > returned_round)
    return result
