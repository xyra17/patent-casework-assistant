"""Keep evidence and unverified model analysis distinct for human review.

The packet never changes acceptance states or records a fictitious human verdict.
It is bound to the delivered answer, evidence snapshot and original draft hash.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def build_packet(run, delivered, items, load_artifact):
    events = run.get('events') or []
    contract = next((e.get('data') for e in reversed(events) if e.get('event') == 'product_boundary_request'), None)
    if not contract or contract.get('profile') not in ('document-comparison', 'disclosure-search', 'public-reference'):
        return None
    summary = next((e.get('data') or {} for e in reversed(events) if e.get('event') == 'quality_summary'), {})
    reviews = [e.get('data') or {} for e in events if e.get('event') == 'reflection']
    review = next((r for r in reversed(reviews) if r.get('round') == summary.get('returned_round')), reviews[-1] if reviews and summary.get('returned_round') is None else {})
    ref = review.get('candidate_report_ref')
    draft = load_artifact(ref) if ref else None
    if not isinstance(draft, str) or hashlib.sha256(draft.encode()).hexdigest() != ref:
        draft = ''
    if not draft:
        return None
    from tools.answer_quality import answer_checks
    from tools.product_boundary import _sources
    from tools.evidence_refs import report_references
    cited = report_references(draft)
    source_records = _sources([s for s in items if s.get('task_scope_compatible') is not False],
                              {s.get('id') for s in items})
    # The most useful records come first; full evidence stays on the execution.
    source_records.sort(key=lambda s: (s['id'] not in cited, s['type'] not in ('file', 'knowledge', 'web')))
    checks = answer_checks(run.get('execution_query') or run.get('query') or '', draft, items)
    hard = list(review.get('hard_issues') or [])
    hard += [c['detail'] for c in checks if c['status'] == 'failed' and c['detail'] not in hard]
    semantic = (review.get('verification') or {}).get('checks') or []
    disputed = [c for c in semantic if c.get('status') in ('failed', 'unverified')]
    # A later unavailable review must not erase earlier unresolved findings.
    for issue in summary.get('issue_lifecycle') or []:
        if issue.get('kind') == 'content' and issue.get('status') not in ('resolved', 'closed'):
            if not any(c.get('detail') == issue.get('detail') for c in disputed):
                disputed.append(issue)
    manual = [{'item': text, 'kind': 'objective_issue', 'status': 'unresolved'} for text in hard]
    manual += [{'item': c.get('detail') or c.get('label') or c.get('criterion'),
                'kind': 'model_review_finding', 'status': 'needs_review'} for c in disputed]
    manual += [{'item': label, 'kind': 'professional_judgment', 'status': 'needs_review'}
               for label in contract.get('professional_judgments') or []]
    if not manual and summary.get('passed') is True:
        return None
    if review.get('soft_review_status') in ('skipped', 'not_run'):
        manual.append({'item': (review.get('review_failure') or {}).get('detail') or review.get('soft_review_detail') or '内容审查未完成。',
                       'kind': 'review_unavailable', 'status': 'needs_review'})
    if not manual and summary.get('passed') is not True:
        manual.append({'item': '内容审查未完成，请对照原件核对模型草稿及未答全的要求。',
                       'kind': 'review_unavailable', 'status': 'needs_review'})
    if review.get('soft_review_status') in ('skipped', 'not_run'):
        manual.append({'item': '按原问题核对尚未完成的要求：'+(run.get('execution_query') or run.get('query') or '')[:1000],
                       'kind': 'task_coverage', 'status': 'needs_review'})
    diagnoses = []
    if hard:
        diagnoses.append({'layer': 'program_or_output', 'finding': '存在可定位的格式、来源或输出问题。',
                          'next_action': '先修复可复现项，再用保存的正文与来源离线回放；不以免责声明放行错误。'})
    if disputed or review.get('soft_review_status') in ('skipped', 'not_run'):
        diagnoses.append({'layer': 'evaluation', 'finding': '自动评审存在未通过或未完成项，不能直接等同业务结论错误。',
                          'next_action': '逐项核对用户要求、来源与评审理由；预算或服务限制应与内容错误分别记录。'})
    gaps = review.get('evidence_gaps') or []
    manual += [{'item': (g.get('detail') or g.get('message') or str(g)) if isinstance(g, dict) else str(g),
                'kind': 'evidence_gap', 'status': 'needs_review'} for g in gaps]
    if gaps:
        diagnoses.append({'layer': 'evidence', 'finding': '检索过程记录过证据缺口，需核对是否已由后续来源补齐。',
                          'next_action': '先检查检索范围、正文读取和截断，再判断资料是否真正缺失。'})
    diagnoses.append({'layer': 'model_capability', 'finding': '尚未证明当前模型的能力上限。',
                      'next_action': '单次错误、预算触顶或片段不完整不能证明模型无能；专业结论无法自动确认时由人工结合证据判断。'})
    objective = next((e.get('data') for e in reversed(events) if e.get('event') == 'product_objective'), None)
    valid_ids={s['id'] for s in source_records if s['valid']}
    objective_valid = bool(objective and objective.get('source_ids') and set(objective['source_ids']) <= valid_ids and objective.get('status') == 'VERIFIED' and
                           objective.get('proof_sha256') == digest({k:v for k,v in objective.items() if k != 'proof_sha256'}))
    packet = {'schema_version': 1, 'candidate_round': review.get('round'),
              'review_unavailable': review.get('review_failure'), 'delivered_report_sha256': hashlib.sha256(delivered.encode()).hexdigest(),
              'evidence_sha256': digest(items), 'sources': source_records[:16],
              'more_sources': max(0, len(source_records)-16),
              'program_comparison_verified': objective_valid,
              'model_draft': {'artifact_ref': ref, 'sha256': ref, 'text': draft[:16000],
                              'truncated': len(draft)>16000, 'status': 'unverified_analysis'},
              'objective_issues': hard, 'review_findings': disputed, 'evidence_gaps': gaps,
              'diagnoses': diagnoses, 'manual_checks': manual, 'human_confirmation': None,
              'acceptance_changed': False}
    packet['packet_sha256'] = digest(packet)
    return packet


def for_run(run):
    packet = next((e.get('data') for e in reversed(run.get('events') or [])
                   if e.get('event') == 'analysis_handoff'), None)
    if not packet or run.get('status') != 'completed' or not run.get('result'):
        return None
    if (packet.get('packet_sha256') != digest({k:v for k,v in packet.items() if k != 'packet_sha256'}) or
        packet.get('delivered_report_sha256') != hashlib.sha256((run.get('result') or '').encode()).hexdigest() or
        packet.get('evidence_sha256') != digest(run.get('evidence') or [])):
        return None
    return packet
