"""Independent bindings for program-rendered business answers.

These checks prove specific values and excerpts against execution evidence. They
do not prove that every source document is correct, or replace semantic review.
"""
from __future__ import annotations

import hashlib
import json
import re


def language_issues(request, report):
    issues = []
    if request.operation in ('cases', 'case_brief', 'recommend') and any('法定期限' in line for line in report.splitlines() if line.startswith('|')):
        issues.append('部门登记的计划期限不能在表格中重命名为法定期限')
    for sentence in re.split(r'[。！？\n]', report):
        if re.search(r'不能|不应|不代表|不证明|不推定|无法|未验证|未核实|不作|不能据此', sentence):
            continue
        if re.search(r'(?:证明|说明|所以|因此|意味着).{0,15}(?:尚未交局|没有交局)|(?:交底书|申请文件)全文.{0,8}(?:没有|缺少)|(?:保证|必然).{0,15}(?:准确率|技术效果)|(?:可以认定|一定是).{0,10}同一人', sentence):
            issues.append('固定流程出现超出原件字段与局部技术摘录的断言')
    return issues


def delivery(request, payload):
    """Only semantic uncertainties go to the model, not duplicated giant tables."""
    def archive(value):
        return {'retrieval_query':value.get('query',''),
                'case_ids': [c['case_id'] for c in value['results']], 'has_more': value['has_more'],
                'technical_excerpts': [{'case_id': c['case_id'], 'text': next((p['excerpt'] for p in c['sources']
                    if p['kind'] in ('application', 'disclosure')), c['sources'][0]['excerpt'] if c['sources'] else '')} for c in value['results']],
                'archive_kind':value.get('filters',{}).get('kind'),
                'query_scope':value.get('selection_review',{}).get('mode',value.get('query_scope','ambiguous')),
                'exhaustive':value.get('exhaustive',False),
                'relations':[{'case_id':c['case_id'],**c['relevance']} for c in value['results'] if c.get('relevance')],
                'unverified_candidate_ids':[c['case_id'] for c in value['results'] if c.get('candidate_only')],
                'related_drawings':[{'case_id':c['case_id'],'file_id':p['file_id'],'source':p['source'],'scope':p['scope']}
                                    for c in value['results'] for p in c.get('related_drawings',[])],
                'fields': ['case_id','source_owner_if_verified','source_stage','body_quote','source_references'],
                'scope': '当前命中候选与局部原文；未核实承办人不推定原件未记载'}
    def ledger(value):
        from casework.requirements import selected_due_statuses, due_group
        return {'case_ids': [r['case_id'] for r in value['rows']], 'has_more': value['has_more'],
                'fields': ['case_id','owner','stage','received_date','draft_due_date','client_review_due_date','filing_due_date','actual_filed_date','filing_verified','conflicts'],
                'unknown_due_ids': [r['case_id'] for r in value.get('unknown_due_cases', [])],
                'filing_month_decision': request.filing_month,
                'missing_date_field': request.missing_date_field,
                'actual_date_filter': {'field':request.missing_date_field, 'rows':[
                    {'case_id':r['case_id'],'value':r.get(request.missing_date_field),
                     'conflict':request.missing_date_field in r.get('conflicting_candidates',{}),
                     'filing_verified':r.get('filing_verified')} for r in value['rows']]} if request.missing_date_field else None,
                'deadline_context': {'as_of':value['as_of'],'planned_dates_only':True},
                'due_group_proofs':value.get('due_group_proofs',{}),
                'due_groups': {status:[r['case_id'] for r in value['rows'] if due_group(r.get(request.due_field),value['as_of'])==status]
                               for status in selected_due_statuses(request)},
                'scope': '案件信息，冲突不选值；实际日期未知不等于未交局'}
    if request.operation == 'summary':return {'fields':['total_count','active_count','filed_count','unknown_stage_count'],'complete_snapshot':True,'as_of':payload['as_of']}
    if request.operation == 'archive': return {**archive(payload),'selection_review':payload.get('selection_review')}
    if request.operation == 'cases': return ledger(payload)
    if request.operation == 'case_brief': return {'ledger': ledger(payload['ledger']), 'archive': archive(payload['archive']), 'registered_material_gaps':'case_meetings' in payload['ledger'],'manual_checks': True}
    if request.operation == 'monthly': return {'missing_date_field':payload.get('missing_date_field'), 'unknown_actual_ids':[r['case_id'] for r in payload.get('unknown_actual_dates',[])], 'date_completeness':payload.get('date_completeness',{}), 'markdown_report':'planned' if any(r.kind=='markdown_report' for r in request.requirements) else None, 'months': [r['month'] for r in payload['totals']], 'fields': ['actual_received_count','verified_actual_filed_count','unknown_lists','conflicts'], 'comparisons':payload.get('comparisons',[]), 'scope': '两个实际发生日期分别归月；未知不能归月；月份变化由程序相减'}
    if request.operation == 'meetings':
        from casework.workflow import meeting_detail_fields
        return {'fields': ['date','case_id','client','actual_iprs','actual_agents','references',*[key for key,_ in meeting_detail_fields(request)]],
        'actual_agents':sorted({name for row in payload['rows'] for name in row['agents']}),
        'has_more': payload['has_more'], 'scope': '逐场实际记录；同事取实际参会代理师列，IPR另列用于核对筛选；不同客户同名不合并，不作永久偏好推断'}
    load_only=request.recommendation_mode=='workload_only'
    has_history=request.recommendation_mode=='technical'
    selected=payload['candidates'] if load_only else payload['candidates'][:3]
    return {'selected_candidates': [r['name'] for r in selected],
            'recommendation_mode':request.recommendation_mode,
            'technical_intent_unresolved':payload.get('technical_intent_unresolved',False),
            'technical_excerpts': [] if not has_history else [{'case_id': h['case_id'], 'text': p['excerpt']} for r in selected for h in r['history_matches'][:3] for p in h['sources'][:1]],
            'fields': (['historical_owner_evidence','body_quote'] if has_history else [])+['current_active_count','near_due_count','overdue_count','unknown_stage','three_due_dates_and_stage','unknown_due_nodes','human_confirmation'],
            'scope': ('用户已确认仅展示当前启用登记人员的负荷，按姓名排列，不作技术推荐。' if load_only else '仅按已知在办与三个期限负荷排序的前三名，不核对技术经历；仍需人工确认，不表示工时或技术适配。' if request.recommendation_mode=='workload_rank' else '只展示按公开规则排序的前三名，领域和负荷兜底不等于专业能力；未知针对展示候选，完整名单可在工作台查看')}


def task_contract_issues(query, request, delivered):
    """Check preserved task anchors independently of action selection.

    This is an explicit bounded business grammar, not a claim to understand all
    natural language. Unsupported constraints belong to the full agent route.
    """
    from casework import parser
    from casework.routing import affirmative, dates, months, result_limit
    from casework.requirements import requirement_issues, constraint_issues, selected_due_statuses
    text = affirmative(query)
    issues = []
    from casework.requirements import external_comparison_requested,case_mechanism_judgment_requested
    if case_mechanism_judgment_requested(query):
        issues.append('指定案件是否采用机制需要综合解释，原文摘录不能代替推理结论')
    if external_comparison_requested(query):
        issues.append('外部公开比较不属于内部固定动作，不能用技术摘录或引用约束代替比较交付')
    if request.operation!='recommend' and re.search(r'忙不忙|忙不忙碌|最近忙|人员负荷|工作量|排期|有没有空|是否有空',text):
        issues.append('人员忙闲需要该人员整体在办与期限负荷，案件负责人或单案信息不能代替')
    if request.operation=='recommend' and not re.search(r'谁|哪位|推荐|接案|接手|分案|承办|负责|分给|交给|安排给|派给|人选',text):
        issues.append('原问题没有选择接案人员的要求，不能把查找参考案件变成接案推荐；请重新保留实际交付目标')
    if request.operation in ('archive','case_brief') and re.search(r'(?:是否能|能否|能不能)证明.{0,30}(?:实现|采用|使用)',query):
        issues.append('判断反例能否证明已实现机制需要综合解释，原文摘录动作不能替代推理结论')
    issues.extend(requirement_issues(query,request,delivered if delivered else None))
    issues.extend(constraint_issues(query,request,delivered if delivered else None))
    if request.requirements and request.operation in ('cases','monthly') and re.search(r'(?:未知(?:日期)?|日期(?:缺失|不明|不清楚)|未知或冲突)(?:的)?(?:另列|单列|分别列(?:出|出来)?|分开列)',query) and not any(c.kind=='unknown_separate' for c in request.constraints):
        issues.append('未知日期另列的原句必须保留到constraints.unknown_separate，且期限查询须保留unknown分组')
    ids = {m.group().upper() for m in parser.CASE_RE.finditer(query)}
    if request.operation=='cases':
        from casework.routing import known_entities
        entities=known_entities(text)
        if len(entities['client'])==1 and request.client!=entities['client'][0]:
            issues.append('客户案件筛选未保留')
        missing_words=r'未知|未核实|尚未核实|未记录|缺失|空白|为空'
        for field,words in [('received_date',r'(?:实际)?收案日期'),('actual_filed_date',r'实际交局日期')]:
            if re.search(words+r'.{0,8}(?:'+missing_words+')',text) and not ids and request.missing_date_field!=field:
                issues.append('实际日期未知清单筛选未保留：'+field)
    if ids and (request.operation not in ('cases','case_brief','archive') or ids != {request.query.upper()}):
        issues.append('执行契约未保留明确案号')
    completed = bool(re.search(r'已(?:经)?(?:交局|完成|交付|结案|办结)|(?:完成|交局|结案|办结)的|办完的', text))
    if request.operation == 'archive' and completed and not request.completed_only:
        issues.append('原问题的完成状态筛选未执行')
    if request.operation == 'archive' and not completed and request.completed_only:
        issues.append('原问题未限定已完成案件，不能自行缩小历史材料范围')
    if re.search(r'推荐|分给|分案|代理师候选', text) and request.operation != 'recommend':
        issues.append('辅助分案要求被替换为其他动作')
    if request.operation == 'archive':
        if request.retrieval_query:
            from casework.workflow import original_search_query
            if request.retrieval_query != original_search_query(query):
                issues.append('技术检索没有保留完整原问题，不能仅用模型缩写后的问句执行')
            if delivered and delivered.get('retrieval_query') != request.retrieval_query:
                issues.append('实际技术检索参数与原问题不一致')
            if delivered and delivered.get('unverified_candidate_ids'):
                issues.append('技术相关性仍待核实，不能将检索候选作为已完成的相似案件交付')
        from casework.archive_intent import explicit_scope
        expected_scope,_=explicit_scope(query)
        if expected_scope!='ambiguous' and request.archive_intent.mode!=expected_scope:
            issues.append('用户明确的案件查询口径未保留')
        if delivered and request.archive_intent.mode!='ambiguous' and delivered.get('query_scope')!=request.archive_intent.mode:
            issues.append('案件查询口径与实际执行不一致')
        if re.search(r'(?:不要|禁止|不得|不)(?:使用|用|检索|查|调用)?向量(?:数据库|检索|搜索)',query) and not any(c.kind=='lexical_only' for c in request.constraints):
            issues.append('禁止向量检索的约束未进入工具参数')
        if re.search(r'Visio|\.vsdx?\b|绘图附件',text,re.I) and request.archive_kind!='drawing':
            issues.append('绘图文件筛选未保留')
        if delivered and delivered.get('archive_kind')!=request.archive_kind:
            issues.append('绘图文件筛选与执行结果不符')
        expected = result_limit(text)
        if expected is not None:
            if request.result_limit != expected or len(delivered.get('case_ids', [])) > expected:
                issues.append('显式返回数量未保留')
    if request.operation in ('cases','recommend'):
        explicit_dates = dates(text)
        if explicit_dates and selected_due_statuses(request) and request.as_of not in explicit_dates:
            issues.append('期限基准日与原问题不符')
        if request.operation == 'recommend' and explicit_dates and request.as_of not in explicit_dates:
            issues.append('候选负荷基准日与原问题不符')
        if selected_due_statuses(request):
            for field, words in [('draft_due_date',r'初稿|所限'), ('client_review_due_date',r'客户送审|客户期限'), ('filing_due_date',r'向局提交|提交期限|交局期限')]:
                if re.search(words, text) and request.due_field != field:
                    issues.append('期限节点与原问题不符')
    if request.operation == 'monthly':
        if re.search(r'按(?:照)?(?:每(?:位|个|一位|一个)?|各)(?:代理师|承办人|客户|技术领域)',text):issues.append('固定月报不支持按人员或客户等自定义分组；须将分组原句记为other')
        comparison=bool(re.search(r'变化|增减|多了|少了|相比|比较|比一比|比比|对比|增加|减少|哪.{0,3}月.{0,6}(?:多|少)|多一点|少一点',text))
        if request.compare_months and not comparison:issues.append('用户只要求分别计数，不能自行增加monthly_comparison，请保留monthly_counts并设compare_months=false')
        if comparison and not request.compare_months:issues.append('用户要求比较月份，须保留monthly_comparison和compare_months=true，不能只返回各月计数')
        if dates(text):issues.append('固定月报不支持历史截点；日期必须保留为不支持要求，不能忽略')
        expected = months(text)
        if expected and (request.start_month != min(expected) or request.end_month != max(expected)):
            issues.append('实际发生月份与原问题不符')
    if request.operation == 'cases' and re.search(r'计入|能算|交局统计', text):
        expected = months(text)
        if len(expected) == 1 and request.filing_month != expected[0]:
            issues.append('单案实际交局归月条件未保留')
    if request.operation == 'meetings':
        from casework.routing import known_entities
        entities = known_entities(text)
        for field in ('client','ipr'):
            if len(entities[field]) == 1 and getattr(request, field) != entities[field][0]:
                issues.append('实际会议的'+field+'筛选未保留')
        if len(entities['client']) > 1 and set(request.clients or []) != set(entities['client']):
            issues.append('多个客户不能退化成无过滤的会议查询')
    expected_tags = set(parser.query_tags(text)) or set(parser.query_tags(request.query))
    excerpts = delivered.get('technical_excerpts', []) or delivered.get('archive', {}).get('technical_excerpts', [])
    for hit in excerpts:
        if expected_tags and not expected_tags.intersection(parser.tags(hit['text'])):
            issues.append(hit['case_id']+' 没有支持所问正向技术机制的原文')
    return issues


def verify(request, payload, evidence, report):
    from casework import analytics, parser, store
    from casework.workflow import render, cell

    sources = {e['id']: e for e in evidence if e.get('id')}
    issues = []
    claims = []
    requested_tags=set(parser.query_tags(request.retrieval_query or request.query))

    def fail(message):
        issues.append(message)

    def source(sid, kind):
        value = sources.get(sid)
        if not value or value.get('type') != kind:
            fail(f'{sid} 缺失或不是所需的{kind}证据')
            return {}
        return value

    def db(sid, values, subject):
        proof = source(sid, 'database')
        columns = proof.get('columns', [])
        valid = all(k in columns for k in values)
        if valid:
            valid = any(all(len(row) > columns.index(k) and row[columns.index(k)] == v for k, v in values.items())
                        for row in proof.get('rows', []))
        if not valid:
            fail(f'{sid} 不能同时支持 {subject} 的字段和值：{list(values)}')
        claims.append({'subject': subject, 'values': values, 'source': sid, 'binding': 'same_row', 'verified': valid})

    def body(proof, case_id):
        sid = proof.get('evidence_id')
        entry = source(sid, 'knowledge')
        exact = bool(proof.get('excerpt')) and entry.get('snippet') == proof.get('excerpt')
        exact = exact and entry.get('case_id') == case_id and entry.get('file_id') == proof.get('file_id')
        exact = exact and entry.get('revision') == proof.get('revision') and entry.get('location') == proof.get('location')
        if not exact:
            fail(f'{sid} 技术摘录、案件、文件、版本或定位未绑定')
        quote = proof.get('excerpt', '').replace('\n', '\n> ')
        # Recommendation uses escaped inline excerpts; archives use blockquotes.
        if quote not in report and cell(proof.get('excerpt', '')) not in report:
            fail(f'{sid} 所展示技术摘录不是执行时原文')
        claims.append({'subject': case_id, 'predicate': 'quoted_body', 'source': sid, 'verified': exact})

    def archive(value, completed_only=False):
        if request.operation=='archive' and value.get('filters',{}).get('kind')!=request.archive_kind:
            fail('绘图文件筛选与执行契约不符')
        for case in value['results']:
            cid = case['case_id']
            facts = case['document_facts']
            if completed_only and facts.get('stage') not in analytics.COMPLETED:
                fail(f'{cid} 未满足原件完成状态')
            for field, label in [('owner_name', '代理师'), ('stage', '案件状态'),
                                 ('actual_filed_date', '实际交局日期'), ('filing_confirmed', '交局核实状态'), ('notes','备注')]:
                fact = facts.get(field)
                if fact is None:
                    continue
                if field=='notes' and cell(fact) not in report:
                    fail(f'{cid} 材料来源与办理范围未按原件备注交付')
                ids = facts['field_evidence_ids'].get(field, [])
                if not ids:
                    fail(f'{cid} 的{label}没有字段级原件证据')
                for sid in ids:
                    entry = source(sid, 'knowledge')
                    valid = entry.get('case_id') == cid and f'{label}：{fact}' in entry.get('snippet', '')
                    if not valid:
                        fail(f'{sid} 不支持 {cid} 的{label}')
                    claims.append({'subject': cid, 'predicate': field, 'value': fact, 'source': sid, 'verified': valid})
            proof = next((p for p in case['sources'] if p['kind'] in ('application', 'disclosure')),
                         case['sources'][0] if case['sources'] else None)
            if proof:
                body(proof, cid)
                relation=case.get('relevance')
                if relation and relation['relation'] not in ('unverified','unrelated'):
                    entry=source(relation.get('evidence_id'),'knowledge')
                    from casework.archive_selection import literal_quote
                    if entry.get('case_id')!=cid or not literal_quote(entry.get('snippet',''),relation['quote'],min_chars=2):
                        fail(f'{cid} 关联程度依据没有绑定本案原文')
                    if relation['quote'].replace('|','\\|').replace('\n','；') not in report:
                        fail(f'{cid} 关联依据未交付')
                if not relation and requested_tags and not requested_tags.intersection(parser.tags(proof.get('excerpt',''))):
                    if not case.get('candidate_only') or cid+' 技术相关性待核实' not in report:
                        fail(f'{cid} 技术原文没有支持所问正向机制')
            for drawing in case.get('related_drawings',[]):
                sid=drawing['evidence_id'];entry=source(sid,'knowledge')
                valid=all(entry.get(k)==drawing[k] for k in ('file_id','revision','parser_key')) and entry.get('case_id')==cid
                valid=valid and entry.get('source')==drawing['source'] and entry.get('snippet')==drawing['excerpt']
                if not valid:fail(f'{sid} 绘图附件与案件登记不符')
                if sid not in report:fail(f'{sid} 绘图附件引用未交付')
                claims.append({'subject':cid,'predicate':'related_drawing','value':drawing['source'],'source':sid,'verified':valid})
            if request.operation=='archive' and request.archive_kind=='drawing' and any(p['kind']!='drawing' for p in case['sources']):
                fail(f'{cid} 绘图筛选混入其他正文')
            if request.query == cid and len(value['results']) != 1:
                fail('单案技术核对混入其他案件')

    def cases(value):
        labels = {'case_id': '所编号', 'client_case_id': '客户案号', 'owner_name': '代理师', 'stage': '案件状态',
                  'received_date': '实际收案日期', 'draft_due_date': '初稿期限／所限', 'client_review_due_date': '客户送审期限',
                  'filing_due_date': '向局提交期限', 'actual_filed_date': '实际交局日期'}
        for row in value['rows']:
            cid = row['case_id']
            if request.missing_date_field:
                field=request.missing_date_field
                if row.get(field) and field not in row.get('conflicting_candidates',{}) and not (
                    field=='actual_filed_date' and row.get('filing_verified')!='已核实'):
                    fail(f'{cid} 实际日期未知清单混入已核实日期')
            db(value['evidence_id'], {label: row.get(k) for k, label in labels.items()} |
               {'交局已核实': row['filing_verified'] == '已核实'}, cid)
            if request.operation in ('cases', 'case_brief') and parser.CASE_RE.fullmatch(request.query) and cid != request.query:
                fail('案件查询结果与显式案号不符')
            for field, candidates in row['conflicting_candidates'].items():
                if row.get(field) is not None:
                    fail(f'{cid} 冲突字段 {field} 被静默选值')
                proof = source(row['conflict_evidence_id'], 'database')
                for c in candidates:
                    if not any(cid in r and c['value'] in r and c['path'] in r and c['location'] in r for r in proof.get('rows', [])):
                        fail(f'{cid} 冲突候选缺少对应依据')
            from casework.requirements import selected_due_statuses, due_group
            statuses = selected_due_statuses(request)
            if statuses & {'overdue','next14'}:
                base = analytics.as_date(value['as_of'])
                if due_group(row.get(request.due_field),value['as_of'])!='unknown' and not analytics.active({**row, 'filing_verified': row['filing_verified'] == '已核实'}, base):
                    fail(f'{cid} 不在程序定义的在办范围')
                due = row.get(request.due_field)
                if due_group(due,value['as_of']) not in statuses:
                    fail(f'{cid} 不满足指定期限条件')
        for row in value.get('unknown_due_cases', []):
            db(value['unknown_due_evidence_id'], {'所编号': row['case_id'], '代理师': row.get('owner_name'),
                labels[request.due_field]: row.get(request.due_field)}, row['case_id'])
            if row.get(request.due_field) is not None:
                fail('未知期限清单混入已知期限')

    if request.operation == 'summary':
        db(payload['evidence_id'],dict(zip(['案件总数','在办案件数量','实际交局数量','状态未知案件数量'],
                                         [payload[k] for k in ['total_count','active_count','filed_count','unknown_stage_count']])), '当前完整发布快照')
    elif request.operation == 'case_brief':
        cases(payload['ledger'])
        archive(payload['archive'])
        if any(r.kind=='case_material_gaps' for r in request.requirements):
            meetings=payload['ledger'].get('case_meetings')
            if meetings is None:fail('单案登记待补材料未读取')
            else:
                for row in meetings['rows']:
                    if row['case_id']!=request.query:fail('单案待补材料混入其他案号')
                    db(meetings['evidence_id'],{'所编号':row['case_id'],'会议日期':row['date'],'待补材料':row.get('pending')},row['case_id'])
                    if cell(row.get('pending')) not in report:fail('本案已登记待补材料未交付')
    elif request.operation == 'archive':
        archive(payload, request.completed_only)
    elif request.operation == 'cases':
        cases(payload)
    elif request.operation == 'meetings':
        from casework.workflow import meeting_detail_fields
        for row in payload['rows']:
            db(payload['evidence_id'], {'所编号': row['case_id'], '客户': row['client'], '会议日期': row['date'],
                                      '参会IPR': '、'.join(row['iprs']), '参会代理师': '、'.join(row['agents']),
                                      **{label:row.get(key) for key,label in meeting_detail_fields(request)}}, row['case_id'])
            if request.client and row['client'] != request.client:
                fail('会议客户过滤未兑现')
            if request.clients and row['client'] not in request.clients:
                fail('会议多客户范围未兑现')
            if request.ipr and request.ipr not in row['iprs']:
                fail('会议实际IPR过滤未兑现')
    elif request.operation == 'monthly':
        expected_cases,duplicates=analytics.unique_cases([c for c in store.list_cases() if not request.client or c.get('client_alias')==request.client])
        for key,field in [('unknown_received','received_date'),('unknown_filed','actual_filed_date')]:
            expected_ids={c['case_id'] for c in expected_cases if analytics.actual_date_unknown(c,field)}
            actual_ids=[r['case_id'] for r in payload[key]]
            if expected_ids!=set(actual_ids) or len(actual_ids)!=len(set(actual_ids)):
                fail('所有办理阶段日期完整性清单缺项、重复或混入已核实记录：'+field)
            contract=payload.get('date_completeness',{}).get(key,{})
            proof=sources.get(contract.get('evidence_id'),{})
            if proof.get('date_completeness')!={k:v for k,v in contract.items() if k!='evidence_id'}:
                fail('日期完整性查询范围未绑定证据：'+field)
            expected_filters=[{'field':'client_alias','value':request.client}] if request.client else []
            if (contract.get('predicate')!=key or contract.get('filters')!=expected_filters
                    or contract.get('stage_scope')!='all' or contract.get('time_scope')!='unassigned'
                    or contract.get('complete')!=(not bool(duplicates))):
                fail('日期完整性查询字段、筛选或完整范围不符：'+field)
            if contract.get('population_count')!=len(expected_cases) or contract.get('matched_count')!=len(expected_ids):
                fail('日期完整性查询总量或匹配数量不符：'+field)
            if any(cid not in report for cid in expected_ids):fail('日期未核实完整清单未全部交付：'+field)
        expected_chronology={c['case_id'] for c in expected_cases if not analytics.actual_date_unknown(c,'received_date')
            and not analytics.actual_date_unknown(c,'actual_filed_date') and c['actual_filed_date']<c['received_date']}
        chronology_ids=[c['case_id'] for c in payload.get('chronology_conflicts',[])]
        if set(chronology_ids)!=expected_chronology or len(chronology_ids)!=len(set(chronology_ids)):
            fail('日期先后矛盾清单与源记录不符')
        chronology_contract=payload.get('date_completeness',{}).get('chronology_conflicts',{})
        chronology_proof=sources.get(chronology_contract.get('evidence_id'),{})
        if (chronology_contract.get('predicate')!='chronology_conflicts'
                or chronology_contract.get('matched_count')!=len(expected_chronology)
                or chronology_contract.get('population_count')!=len(expected_cases)
                or chronology_contract.get('filters')!=([{'field':'client_alias','value':request.client}] if request.client else [])
                or chronology_contract.get('stage_scope')!='all'
                or chronology_contract.get('complete')!=(not bool(duplicates))
                or chronology_proof.get('date_completeness')!={k:v for k,v in chronology_contract.items() if k!='evidence_id'}):
            fail('日期先后核对范围或证据绑定不符')
        from tools.date_completeness import date_completeness_issues
        issues.extend(date_completeness_issues(report,evidence))
        if request.missing_date_field:
            field=request.missing_date_field
            label={'received_date':'实际收案日期','actual_filed_date':'实际交局日期或核实依据'}[field]
            expected,_=analytics.unique_cases([c for c in store.list_cases() if not request.client or c.get('client_alias')==request.client])
            expected_ids={c['case_id'] for c in expected if analytics.actual_date_unknown(c,field)}
            actual_ids={c['case_id'] for c in payload['unknown_actual_dates']}
            if expected_ids!=actual_ids:fail('月报实际日期未知清单缺项或混入已核实记录')
            for row in payload['unknown_actual_dates']:
                db(payload['unknown_actual_evidence_id'],{'所编号':row['case_id'],'未核实字段':label},row['case_id'])
        for total in payload['totals']:
            for metric, label, field in [('received_count', '实际收案数量', 'received_date'),
                                         ('filed_count', '实际交局数量', 'actual_filed_date')]:
                sid = total[label + '_evidence_id']
                db(sid, {label: total[metric]}, total['month'])
                definition = sources.get(sid, {}).get('statistics', {}).get('metrics', [{}])[0]
                start, end = analytics.month_bounds(total['month'])
                time = definition.get('time', {})
                if time.get('field') != field or time.get('start') != start.isoformat() or time.get('end') != end.isoformat():
                    fail(f'{sid} 实际日期字段或月份边界不一致')
        for key in ('unknown_received', 'unknown_filed'):
            for row in payload[key]:
                db(payload['unknown_evidence_id'], {'所编号': row['case_id'], '缺口': row['reason']}, row['case_id'])
        comparisons=payload.get('comparisons',[])
        if request.compare_months and len(comparisons)!=len(payload['totals'])-1:
            fail('月份比较没有覆盖相邻月份')
        for comparison,(previous,current) in zip(comparisons,zip(payload['totals'],payload['totals'][1:])):
            if comparison['previous_month']!=previous['month'] or comparison['month']!=current['month']:
                fail('月份比较的月份不一致')
            for metric,label in [('received','实际收案数量变化'),('filed','实际交局数量变化')]:
                change=current[metric+'_count']-previous[metric+'_count']
                if comparison[metric+'_change_count']!=change:fail('月份变化不是实际数量之差')
                db(comparison['evidence_id'],{label:change},current['month']+'相比'+previous['month'])
    elif request.operation == 'recommend':
        from datetime import timedelta
        base = analytics.as_date(payload['as_of'])
        end = (base + timedelta(days=14)).isoformat()
        for row in (payload['candidates'] if request.recommendation_mode=='workload_only' else payload['candidates'][:3]):
            name = row['name']
            for history in ([] if request.recommendation_mode!='technical' else row['history_matches']):
                supported=set().union(*(set(parser.tags(p.get('excerpt',''))) for p in history['sources']))
                matched=set(history.get('matched_tags',[]))
                if not matched or not matched <= requested_tags & supported:
                    fail(f"{history['case_id']} 历史经历缺少所问正向机制证据，不能参与推荐")
            db(payload['evidence_id'], {'代理师': name, '在办案件数量': row['active_count'],
                                      '临近期限案件数量': len(row['near_due_cases']),
                                      '逾期案件数量': len(row['overdue_cases']),
                                      '状态未知案件数量': len(row['unknown_stage_cases'])}, name)
            near, overdue = set(), set()
            for c in row['active_cases']:
                db(row['load_evidence_id'], {'所编号': c['case_id'], '代理师': name,
                    '案件状态': c.get('stage'),
                    '初稿期限／所限': c.get('draft_due_date'), '客户送审期限': c.get('client_review_due_date'),
                    '向局提交期限': c.get('filing_due_date')}, c['case_id'])
                for field in ('draft_due_date', 'client_review_due_date', 'filing_due_date'):
                    date = c.get(field)
                    if date and base.isoformat() <= date < end:
                        near.add(c['case_id'])
                    if date and date < base.isoformat():
                        overdue.add(c['case_id'])
            if len(row['active_cases']) != row['active_count'] or len(near) != len(row['near_due_cases']) or len(overdue) != len(row['overdue_cases']):
                fail(f'{name} 案件计数与逐节点日期不一致')
            for history in ([] if request.recommendation_mode!='technical' else row['history_matches'][:3]):
                db(row['history_evidence_id'], {'所编号': history['case_id'], '代理师': name}, history['case_id'])
                proof = next((p for p in history['sources'] if p.get('excerpt')), None)
                if proof:
                    body(proof, history['case_id'])

    from tools.evidence_refs import report_references, citation_issues
    referenced = report_references(report)
    issues.extend(citation_issues(report, evidence))
    issues.extend(language_issues(request, report))
    if not referenced <= sources.keys():
        fail('回答包含未执行的引用编号')
    # Fixed workflows have no free model rewrite after binding. Mutations (extra
    # claims, changed deadlines, switched owners/citations) invalidate the seal.
    if report != render(payload, request):
        fail('固定流程正文偏离已核对的只读渲染，不能添加自由推断')
    return {'version': 1, 'status': 'failed' if issues else 'passed', 'issues': issues,
            'checked_claims': len(claims), 'claims': claims,
            'report_sha256': hashlib.sha256(report.encode()).hexdigest(),
            'request': request.model_dump(exclude_none=True,exclude_defaults=True),
            'scope': '字段同列同行、原文及文件版本绑定、业务日期和负荷不变量；仍需语义审查与原件质量核对'}
