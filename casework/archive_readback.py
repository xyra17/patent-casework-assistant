"""Deterministic, permission-bound archive evidence handoff and final gates."""
from __future__ import annotations

import json
import re
import time

from casework.retrieval_context import _requirements, _assess


def published_complete(entries,key):
    ranges=[e['read_scope'] for e in entries if (e.get('file_id'),e.get('revision'),e.get('parser_key'))==key
            and e.get('read_scope') and e['read_scope'].get('current') is True]
    totals={r['total_chunks'] for r in ranges}
    covered={i for r in ranges for i in range(r['offset'],r['offset']+r['returned'])}
    return len(totals)==1 and covered==set(range(next(iter(totals))))


def assess_sources(query, sources):
    # Joining for assessment is not window expansion: each source retains its
    # independent location, version, evidence ID and structure.
    requirements = _requirements(query)
    assessment = _assess(requirements, {'text': '\n\n'.join(p.get('excerpt', '') for p in sources)})
    facts = []
    for fact in assessment['bound_facts']:
        matches = [p for p in sources if fact['quote'] in p.get('excerpt', '') and p.get('evidence_id')]
        if matches:
            proof = matches[0]
            key = (proof['file_id'], proof['revision'], proof['parser_key'])
            ids = list(dict.fromkeys(p['evidence_id'] for p in matches
                if (p['file_id'], p['revision'], p['parser_key']) == key))
            facts.append({**fact, 'evidence_id': proof['evidence_id'], 'evidence_ids': ids,
                          'file_id': key[0], 'revision': key[1], 'parser_key': key[2]})
    required = bool(requirements['facets'])
    supported = {f['facet'] for f in facts}
    missing = list(assessment['missing']) if required else []
    for facet in requirements['facets']:
        if facet not in supported and facet not in missing:
            missing.append(facet)
    return {'status': 'unverified' if missing else 'passed' if required else 'not_required',
            'required': required, 'missing': missing, 'facts': facts,
            'assessment': assessment, 'method': 'registered_object_state_quotes', 'semantic_verified': False}


def complete(result, query, read, ledger=None):
    """Consume critical gaps with the existing read tool, never raw DB text.

    The caller controls snapshot/permissions; the read tool rechecks source
    policy, privacy and the shared knowledge budget. At most five distinct
    pages are attempted, within the existing six-call budget including search.
    """
    from api import context, evidence
    ledger = ledger or (lambda: evidence.snapshot(context.get_thread_id()))
    trace = list(result.get('evidence_readback', {}).get('calls', []))
    attempted = {(c['args']['file_id'], c['args']['revision'], c['args']['parser_key'], c['args']['offset'])
                 for c in trace if c.get('tool') == 'read_case_archive_document' and c.get('args')}
    deadline = time.monotonic() + 30
    for case in result.get('results', []):
        files = {}
        for proof in case.get('sources', []):
            if proof.get('kind') in ('application', 'disclosure', 'other','drawing'):
                files.setdefault((proof['file_id'], proof['revision'], proof['parser_key']), []).append(proof)
        checks = []
        for (fid, revision, parser_key), sources in files.items():
            critical={'conditions','exceptions','procedure','reason'}
            gaps=[g for g in result.get('context_expansion',{}).get('unresolved_sources',[]) if g.get('file_id')==fid]
            declared=any(critical.intersection(g.get('missing',[])) for g in gaps)
            hints=[p.get('context_expansion',{}).get('follow_up') for p in sources]
            hints=[h for h in hints if h]
            effective_query=query
            if not _requirements(query)['facets'] and _requirements(result.get('query',query))['facets']:
                effective_query=result['query']
            def assess_current():
                current=assess_sources(effective_query,sources)
                if declared and not current['required']:
                    current.update(status='unverified',required=True,missing=['uninterpretable_declared_gap'])
                return current
            check = assess_current()
            invalid_hint=any(h.get('tool','read_case_archive_document')!='read_case_archive_document'
                or (h.get('file_id'),h.get('revision'),h.get('parser_key'))!=(fid,revision,parser_key) for h in hints)
            failure = 'invalid_follow_up' if invalid_hint else None
            offset = 0
            read_complete=published_complete(ledger(),(fid,revision,parser_key))
            while check['status'] == 'unverified':
                if invalid_hint:break
                args = {'file_id': fid, 'revision': revision, 'parser_key': parser_key, 'offset': offset, 'limit': 8}
                key = (fid, revision, parser_key, offset)
                if key in attempted or len(attempted) >= 5:
                    prior = next((c for c in reversed(trace) if c.get('args') == args), {})
                    failure = prior.get('reason', 'read_budget_exhausted'); break
                if time.monotonic() >= deadline:
                    failure = 'tool_timeout'; break
                attempted.add(key)
                call = {'tool': 'read_case_archive_document', 'args': args, 'status': 'started'}
                trace.append(call)
                try:
                    raw = read(args)
                    if time.monotonic() >= deadline:
                        raise TimeoutError('原文读取超过本次处理期限')
                    if isinstance(raw, str):
                        if '预算已耗尽' in raw:
                            raise ValueError('read_budget_exhausted')
                        if raw.startswith('禁止执行'):
                            raise PermissionError('来源权限拒绝原文读取')
                        if raw.startswith('原文读取超时'):
                            raise TimeoutError('原文读取超时')
                        page = json.loads(raw)
                    else:
                        page = raw
                    if (page.get('file_id'), page.get('revision'), page.get('parser_key')) != (fid, revision, parser_key) or page.get('current') is not True:
                        raise ValueError('version_mismatch')
                    if page.get('offset') != offset or not page.get('chunks'):
                        raise ValueError('invalid_page')
                    registered = {e['id']: e for e in ledger()}
                    new_sources = []
                    for chunk in page['chunks']:
                        entry = registered.get(chunk.get('evidence_id'), {})
                        if (entry.get('file_id'), entry.get('revision'), entry.get('parser_key'), entry.get('snippet'), entry.get('location')) != (
                                fid, revision, parser_key, chunk['text'], chunk['location']):
                            raise ValueError('unregistered_read_evidence')
                        new_sources.append({'file_id': fid, 'revision': revision, 'parser_key': parser_key,
                            'kind': sources[0]['kind'], 'source': page.get('source', sources[0].get('source')),
                            'excerpt': chunk['text'], 'location': chunk['location'], 'spans': chunk.get('spans', []),
                            'evidence_id': chunk['evidence_id'], 'channels': ['source_readback']})
                    for proof in new_sources:
                        if not any(all(p.get(k) == proof.get(k) for k in ('file_id', 'revision', 'parser_key', 'location', 'excerpt')) for p in sources):
                            sources.append(proof); case['sources'].append(proof)
                    call.update(status='read', evidence_ids=[p['evidence_id'] for p in new_sources],
                                has_more=page.get('has_more'), current=page['current'],
                                offset=offset,next_offset=page.get('next_offset'))
                    read_complete=page.get('has_more') is False
                    check = assess_current()
                    if check['status'] != 'unverified':
                        break
                    if not page.get('has_more'):
                        failure = 'published_text_insufficient'; break
                    next_offset = page.get('next_offset')
                    if not isinstance(next_offset, int) or next_offset <= offset:
                        raise ValueError('invalid_pagination')
                    offset = next_offset
                except Exception as exc:
                    failure = ('permission_denied' if isinstance(exc, PermissionError) else 'tool_timeout' if isinstance(exc, TimeoutError)
                               else str(exc) if str(exc) in ('version_mismatch', 'invalid_page', 'invalid_pagination', 'unregistered_read_evidence', 'read_budget_exhausted')
                               else 'read_failed')
                    call.update(status='unverified', reason=failure)
                    break
                finally:
                    from api.telemetry import record
                    record({'event':'archive_readback','round':context.get_round(),**call})
            check.update(file_id=fid, revision=revision, parser_key=parser_key, failure=failure,
                         published_text_read_complete=read_complete)
            checks.append(check)
        required = [c for c in checks if c['required']]
        case['evidence_completeness'] = {'status': 'unverified' if any(c['status']=='unverified' for c in required)
                                        else 'passed' if required else 'not_required', 'files': checks}
    pending = [{'case_id': c['case_id'], **f} for c in result.get('results', [])
               for f in c.get('evidence_completeness', {}).get('files', []) if f['status']=='unverified']
    result['evidence_readback'] = {'status': 'unverified' if pending else 'passed', 'calls': trace, 'unresolved': pending,
                                  'scope': '独立同版本读取；来源绑定与局部条件证据，不是普遍语义正确性证明'}
    return result


def final_check(query, entries, report):
    """Final guard shared by fixed rendering and open Agent answers.

    Recompute from registered evidence, not covered_by_checks or a model vote.
    Search excerpts and independent document reads both enter this gate.
    """
    from tools.evidence_refs import report_references, citation_issues
    from casework.parser import CASE_RE
    refs = report_references(report)
    seeds = [e for e in entries if e.get('casework') and (e.get('context_expansion') or e.get('read_scope'))]
    requested={m.group().upper() for m in CASE_RE.finditer(query)}
    if requested:
        seeds=[e for e in seeds if e.get('case_id') in requested]
    cited_files={(e.get('file_id'),e.get('revision'),e.get('parser_key')) for e in entries
                 if e.get('id') in refs and e.get('casework') and e.get('type')=='knowledge'}
    if cited_files and not requested:
        seeds=[e for e in seeds if (e.get('file_id'),e.get('revision'),e.get('parser_key')) in cited_files]
    grouped = {}
    for seed in seeds:
        key = (seed['file_id'], seed['revision'], seed['parser_key'])
        grouped[key] = seed.get('case_id')
    files = []
    for key, case_id in grouped.items():
        proofs = [{'file_id': key[0], 'revision': key[1], 'parser_key': key[2],
                   'excerpt': e.get('snippet', ''), 'evidence_id': e['id']}
                  for e in entries if (e.get('file_id'), e.get('revision'), e.get('parser_key')) == key
                  and e.get('current_snapshot') is not False and e.get('type') == 'knowledge']
        check = assess_sources(query, proofs)
        check['published_text_read_complete']=published_complete(entries,key)
        files.append({**check, 'case_id': case_id, 'file_id': key[0]})
    # A planner/tool failure can leave no archive seeds at all. For an explicit
    # case-condition/original-text task this is missing evidence, not a vacuous
    # successful check. An honest "cannot confirm" may be delivered, but must
    # remain unverified rather than count as completed evidence acceptance.
    if requested and _requirements(query)['facets'] and re.search(r'归档|案卷|交底|申请文件|原文', query):
        acquired = {f['case_id'] for f in files if f['required']}
        for case_id in sorted(requested - acquired):
            files.append({'case_id':case_id, 'status':'unverified', 'required':True,
                          'missing':['archive_evidence_not_acquired'], 'facts':[], 'semantic_verified':False})
    required = [f for f in files if f['required']]
    missing = [f for f in required if f['status']=='unverified']
    cited = {e['id'] for e in entries if e.get('id')} & refs
    # Equivalent registered excerpts of the same file/version can have distinct
    # IDs (search vs direct read). Each required facet still needs a real cite.
    def fact_ids(fact):
        return set(fact.get('evidence_ids', [fact['evidence_id']]))
    unsupported = [f for f in required if f['status']=='passed' and any(
        not any(p['facet'] == facet and fact_ids(p) & cited for p in f['facts'])
        for facet in f['assessment']['checked_facets'])]
    binding_issues = citation_issues(report, entries) if grouped else []
    for entry in entries:
        key = (entry.get('file_id'), entry.get('revision'), entry.get('parser_key'))
        if key in grouped and entry.get('id') in refs and entry.get('casework') and entry.get('type') == 'knowledge':
            if not all(entry.get(k) for k in ('file_id', 'revision', 'parser_key', 'location')) or entry.get('current_snapshot') is False:
                binding_issues.append('案件引用缺少当前来源位置或版本绑定：' + entry['id'])
    issues = list(binding_issues)
    if missing:
        issues.append('案件证据不足：必要对象/状态/条件尚未取得同版本原文，不能仅凭来源绑定通过作出确定结论')
    if unsupported:
        issues.append('案件结论缺少直接条件证据引用：已取得的限制或状态原句必须在回答中引用')
    # Reject an explicit universal assertion contradicted by acquired scoped
    # restrictions. Quoted source blocks and questions are not answer claims.
    if _requirements(query).get('universal') and any(p.get('negative') or p.get('exclusive') for f in required for p in f['facts']):
        for line in report.splitlines():
            if line.lstrip().startswith(('>', '#', '```')):
                continue
            for match in re.finditer(r'(?:一直|始终|全程|总是).{0,8}(?:启用|开启|执行|运行|生效|工作)',line):
                prefix=re.split(r'[。，,；;!?！？：:]|但是|但(?!言)|然而|不过',line[:match.start()])[-1][-80:]
                suffix=line[match.end():match.end()+24]
                if re.search(r'不是|并非|不(?:一直|始终)|不能.{0,70}(?:推断|认为|认定|说明|证明|表述为|理解为|解读为|解释为|视为|看作)|不(?:构成|属于|算是|等于|表明|说明|支持|足以|代表|意味着|能确认|能断言)|无法(?:确认|判断|证明)|未(?:核实|见|说明|表明|证实)|没有.{0,12}(?:看到|见到)|否定的是|是否',prefix):
                    continue
                if re.search(r'没有依据|缺乏依据|无法证明|不成立',suffix) or re.match(r'[”」\"]?(?:直接)?(?:相反|不一致|相矛盾)',suffix):
                    continue
                if re.search(r'关于[“「\"]?$',prefix) or re.match(r'[”」\"]?(?:这一|的)?(?:问法|问题|说法)',suffix):
                    continue
                # Only a direct affirmative object/state assertion belongs in
                # this bounded contradiction guard. Metalinguistic mentions
                # ("不能称为一直启用") are left to content review, rather than
                # extending an unbounded list of Chinese negation paraphrases.
                plain = re.sub(r'[\s*`“”「」\"]', '', prefix)
                requirements = _requirements(query)
                subjects = [requirements['subject_phrase']] if requirements.get('subject_phrase') else requirements['terms']
                explicit = any(re.search(re.escape(s) + r'(?:控制|功能|模块|系统|服务)?(?:会|将|是|保持|处于|确实|仍然)?$', plain)
                               for s in subjects)
                short_answer = bool(re.fullmatch(r'(?:结论|答案|该功能|该模块|它|其)?(?:是|会|将|确实)?', plain))
                if not (explicit or short_answer):
                    continue
                line_refs=report_references(line)
                contradicted=any(len(required)==1 or any(
                    fact_ids(p) & line_refs for p in f['facts'] if p.get('negative') or p.get('exclusive'))
                    for f in required if any(p.get('negative') or p.get('exclusive') for p in f['facts']))
                if contradicted:
                    issues.append('回答的全程状态断言与已取得的对象限制原文矛盾')
                    break
    return {'status': 'unverified' if issues else 'passed', 'issues': issues,
            'source_binding': {'status': 'failed' if binding_issues else 'passed' if grouped else 'not_required',
                               'issues': binding_issues},
            'evidence_completeness': {'status': 'unverified' if missing else 'passed' if required else 'not_required', 'files': files},
            'answer_support': {'status': 'unverified' if issues else 'passed' if required else 'not_required',
                               'method': 'object_state_quotes_and_citations', 'semantic_verified': False}}
