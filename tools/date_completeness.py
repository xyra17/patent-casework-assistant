"""Bound global date assertions to the predicate, filters and full query result.

This is a bounded Chinese assertion grammar; semantic review still checks prose
outside these forms. Conflict absence never proves date completeness.
"""
import re


def date_completeness_issues(report, evidence):
    entries={e.get('id'):e for e in evidence}
    if not any(e.get('date_completeness') for e in evidence):
        # Legacy date-conflict evidence must not justify a missing-date claim.
        if not any(e.get('casework') and '日期' in e.get('title','') for e in evidence):return []
    definitions={str((e.get('answer_contract') or {}).get(k,'')) for e in evidence
                 for k in ('active_definition','definition','deadline_definition')} - {''}
    issues=[]
    for line in report.replace('**','').splitlines():
        if line.strip() in definitions or re.sub(r'^(?:在办口径|期限口径)[：:]\s*','',line.strip()) in definitions:
            continue # Exact source-owned definition, not a claim about observed missing dates.
        line_refs=re.findall(r'[\[〔](S\d+)[\]〕]',line)
        for sentence in re.split(r'[。；](?!\s*[\[〔])',line):
            sentence_refs=re.findall(r'[\[〔](S\d+)[\]〕]',sentence)
            subject=sentence
            for sentence in re.split(r'[，,]',sentence):
                if re.search(r'不能|不代表|不等于|不表示|无法据此|无法确认|不能据此',sentence):continue
                absent=bool(re.search(r'没有|不存在|未查到|未发现|未找到|未检出|(?:^|[：，、\s])无(?=符合|任何|记录|此类|相关|案件|日期|交局|收案|未核实|缺失)|(?:为|共|：|:)\s*(?:0|零)\s*(?:件|条)',sentence))
                none_verified = '交局' in subject and bool(re.search(r'(?:未见|没有|不存在|无).{0,8}已核实', sentence))
                all_known=bool(re.search(r'(?:全部|所有|均|都)(?:(?!未|没有|不).){0,12}(?:已核实|已记录|完整|非空|不为空)',sentence))
                all_unknown=bool(re.search(r'(?:全部|所有)(?:的)?案件.{0,10}(?:未核实|缺失|为空)|案件(?:全部|均|都)(?:未核实|缺失|为空)',sentence))
                if not (absent or all_known or all_unknown or none_verified):continue
                missing=bool(re.search(r'未核实|缺失|为空|空白|没记|未记录',sentence))
                predicates=[]
                if '收案' in subject and (missing or all_known):predicates.append('unknown_received')
                if '交局' in subject and (missing or all_known or none_verified):predicates.append('unknown_filed')
                if absent and re.search(r'早于|先于|先后|逻辑矛盾',subject):
                    predicates.append('chronology_conflicts')
                elif '日期' in sentence and re.search(r'冲突|矛盾',sentence) and absent:predicates.append('date_conflicts')
                if 'chronology_conflicts' in predicates:
                    predicates=['chronology_conflicts']
                if not predicates and '日期' in sentence and (missing or all_known):
                    predicates=['unknown_received','unknown_filed']
                # A bullet may put its one source after a semicolon describing
                # the same query scope. Keep that local binding; never borrow
                # from another line or from multiple competing sources.
                refs=sentence_refs or (line_refs if len(line_refs)==1 and
                    re.match(r'^\s*[-*]\s*日期冲突候选[：:]',line) else [])
                for predicate in predicates:
                    valid=False
                    for sid in refs:
                        e=entries.get(sid,{})
                        if e.get('task_scope_compatible') is False:continue
                        c=e.get('date_completeness',{})
                        scope=e.get('completeness',{})
                        if c.get('predicate')!=predicate:continue
                        if not c.get('complete') or c.get('stage_scope')!='all':continue
                        if any(scope.get(k) for k in ('has_more','fetch_truncated','model_truncated','evidence_truncated')):continue
                        if c.get('matched_count')!=len(e.get('rows',[])):continue
                        # A filter stated once for the whole report still governs
                        # this sentence; it must not be inherited silently from a
                        # different query though, so the report must name it.
                        filters=c.get('filters',[])
                        if filters and not all(str(f['value']) in sentence or str(f['value']) in report for f in filters):continue
                        if c.get('time_scope')=='unassigned' and re.search(r'\d{1,2}月|本月|当月|期间',sentence) and not none_verified:continue
                        expected=c.get('population_count') if none_verified or all_unknown and not absent else 0
                        if expected is not None and c['matched_count']==expected:valid=True;break
                    if not valid:
                        issues.append('日期完整性断言缺少范围匹配的完整证据：'+sentence.strip()[:200]+'；须引用对应字段、客户范围及所有办理阶段的完整查询，冲突为空不能证明日期或核实依据完整。')
    return list(dict.fromkeys(issues))
