"""Render source-bound quotations and complete fact blocks without model copying.

Tokens bind to this execution's source identity and content. They prove literal
origin only; relevance, interpretation and comparisons still need review.
"""
from __future__ import annotations

import hashlib
import json
import re

_QUOTE = re.compile(r'\{\{quote:(S[1-9]\d*):([a-f0-9]{64}):(\d+):(\d+)\}\}')
_SHORT_QUOTE = re.compile(r'\{\{quote:(Q[a-f0-9]{16})\}\}')
_FACTS = re.compile(r'\{\{facts:(S[1-9]\d*):([a-f0-9]{64})\}\}')


def fingerprint(item):
    keys = ('id', 'type', 'source', 'url', 'file_id', 'revision', 'chunk_id',
            'location', 'snippet', 'rows', 'columns', 'statistics', 'answer_contract')
    data = json.dumps({k: item.get(k) for k in keys}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def normalize_source(text):
    """Remove only duplicated numeric HTML/TeX multiplication renderings.

    Preserve all original text on the evidence record. No word stemming, sign
    changes, number changes, or general repeated-word removal is permitted.
    """
    changes = []
    pattern = re.compile(r'(?<![\d.])(?P<n>\d+(?:\.\d+)?)×(?P=n)\\times\b')
    def replace(match):
        replacement = match['n'] + '×'
        changes.append({'start': match.start(), 'end': match.end(),
                        'original': match[0], 'replacement': replacement,
                        'rule': 'duplicate_numeric_math_rendering_v1'})
        return replacement
    return pattern.sub(replace, str(text)), changes


def quote_options(item, limit=6):
    """Expose complete source sentences and immutable selectors, not summaries."""
    text = str(item.get('snippet') or '')
    if item.get('type') not in ('web', 'knowledge', 'upload', 'file') or not text:
        return []
    result = []
    digest = fingerprint(item)
    # Split English prose at sentence boundaries too; decimal points and
    # in-word periods remain in the source sentence. Every slice is literal.
    # PDF line wrapping can split a word or mechanism across a single newline.
    # Keep those wraps inside the literal sentence exposed to the selector.
    boundaries = [0] + [m.end() for m in re.finditer(r'[。！？]|\n\s*\n|[.!?](?=\s+[A-Z]|$)',text)]
    if boundaries[-1] != len(text):boundaries.append(len(text))
    for start,end in zip(boundaries,boundaries[1:]):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if not 1 <= end - start <= 1200:
            continue
        original = text[start:end]
        cleaned, changes = normalize_source(original)
        selector = 'Q' + hashlib.sha256(f'{digest}:{start}:{end}'.encode()).hexdigest()[:16]
        result.append({'quote_token': '{{quote:' + selector + '}}', 'selector_id': selector,
                       'start':start, 'end':end,
                       'text': cleaned, 'original': original, 'normalizations': changes})
        if len(result) >= limit:
            break
    return result


def fact_block(item):
    """All workload metrics, definitions and limitations travel as one unit."""
    if item.get('answer_contract_applicable') is False:
        return ''
    contract = item.get('answer_contract') or {}
    if contract.get('kind') == 'due_case_list' and contract.get('version') == 1:
        columns=item.get('columns') or []
        rows=item.get('rows') or []
        due_labels={'draft_due_date':'初稿期限／所限','client_review_due_date':'客户送审期限',
                    'filing_due_date':'向局提交期限'}
        if contract.get('due_field') not in due_labels:
            raise ValueError('期限字段无效')
        # Carry every planned date the source actually has, so the reader sees the
        # whole schedule and not only the field that was filtered on.
        dates=[label for label in due_labels.values() if label in columns]
        if due_labels[contract['due_field']] not in dates:
            dates=[due_labels[contract['due_field']]]
        required=['所编号','代理师',*dates]
        if (not contract.get('complete_snapshot') or not all(label in columns for label in required)
                or len(rows) != contract.get('total')):
            raise ValueError('期限全量清单缺少完整来源行')
        status={'overdue':'已过计划期限','next14':'未来两周计划到期',
                'unknown':'期限为空或冲突'}.get(contract.get('due_status'))
        if not status:
            raise ValueError('期限全量清单状态无效')
        indices=[columns.index(label) for label in required]
        lines=[f"### {status}：完整案号、承办人与计划期限（基准日 {contract['as_of']}，共 {len(rows)} 件）",'',
               '| 所编号 | 承办人 | '+' | '.join(dates)+' | 来源 |',
               '|'+' --- |'*(len(dates)+3)]
        for row in rows:
            values=[str(row[index] if row[index] is not None else '未核实').replace('|','&#124;')
                    for index in indices]
            lines.append('| '+' | '.join(values)+f" | [{item['id']}] |")
        if not rows:
            return ''
        return '\n'.join(lines)
    if contract.get('kind')=='monthly_report' and contract.get('version')==1:
        return contract.get('body','')
    if contract.get('kind') in ('workload_summary','table_aggregate') and contract.get('version')==1:
        stats=item.get('statistics') or {}
        cols=[*stats.get('dimensions',[]),*stats.get('metrics',[])]
        rows=item.get('rows') or []
        if not cols or not rows:return ''
        if any(not m.get('definition_verified') for m in stats.get('metrics',[])):
            raise ValueError('统计事实表含未核实指标')
        def cell(v):return str(v).replace('|','&#124;').replace('\n',' ')
        title=('基准日：'+contract['as_of']) if contract.get('as_of') else '附件统计结果'
        lines=['### '+title,'','|'+'|'.join(cell(c.get('label') or c['column']) for c in cols)+'|来源|',
               '|'+'|'.join(['---']*(len(cols)+1))+'|']
        for row in rows:
            lines.append('|'+'|'.join(cell(row[c['index']]) for c in cols)+f"|[{item['id']}]|")
        lines += ['','统计范围：'+contract['scope'],contract.get('definition','')]
        if contract.get('kind')=='workload_summary' and 'owner_case_details' in contract:
            lines += ['','### 承办人案号明细','','|承办人|在办案号|状态未知案号|来源|',
                      '|---|---|---|---|']
            for owner in contract['owner_case_details']:
                lines.append('|'+cell(owner['owner'])+'|'+cell('、'.join(owner['active_case_ids']) or '无')+
                             '|'+cell('、'.join(owner['unknown_stage_case_ids']) or '无')+f"|[{item['id']}]|")
            if contract.get('complete_snapshot'):
                lines.append('\n以上清单覆盖本次汇总的全部承办人和在办、状态未知案号；已核实实际交局的案件已排除。')
        return '\n'.join(lines)
    if contract.get('kind') != 'workload' or contract.get('version') != 1:
        return ''
    stats = item.get('statistics') or {}
    metrics = stats.get('metrics') or []
    dims = stats.get('dimensions') or []
    rows = item.get('rows') or []
    if len(rows) != 1 or not metrics or not dims:
        raise ValueError('负荷事实块缺少单行数据或维度')
    row = rows[0]
    if any(not m.get('definition_verified') for m in metrics):
        raise ValueError('负荷事实块含未核实指标定义')
    if any(type(c.get('index')) is not int or not 0 <= c['index'] < len(row) for c in [*dims, *metrics]):
        raise ValueError('负荷事实块列位置无效')
    if any(not contract.get(k) for k in ('as_of', 'scope', 'active_definition', 'deadline_definition')):
        raise ValueError('负荷事实块缺少日期、范围或口径')
    def cell(value):
        return str(value).replace('|', '&#124;').replace('\n', ' ')
    lines = [f"### {contract['as_of']} · 登记负荷"]
    for at in range(0, len(metrics), 4):
        cols = [*dims, *metrics[at:at + 4]]
        lines += ['', '|' + '|'.join(cell(c.get('label') or c['column']) for c in cols) + '|来源|',
                  '|' + '|'.join(['---'] * (len(cols) + 1)) + '|',
                  '|' + '|'.join(cell(row[c['index']]) for c in cols) + f"|[{item['id']}]|"]
    lines += ['', '统计范围：' + contract['scope'], '在办口径：' + contract['active_definition'],
              '期限口径：' + contract['deadline_definition']]
    if contract.get('excluded_case_ids'):
        lines += ['同案号重复记录已排除，未计为零负荷：'+'、'.join(contract['excluded_case_ids'])+'。']
    return '\n'.join(lines)


def bind_task_scope(query, items):
    """Retain every source; only applicable contracts may be forced into an answer."""
    from tools.task_evidence_checks import requested_client, metric_client
    from casework.routing import months
    client = requested_client(query, items)
    selected_months = months(query) if re.search(r'每月|各月|月报|月度|统计', query) else []
    lookup = {item.get('id'): item for item in items}
    result = []
    for original in items:
        item = dict(original)
        metrics = (item.get('statistics') or {}).get('metrics') or []
        monthly = [metric for metric in metrics if metric.get('time', {}).get('field')
                   in ('received_date','actual_filed_date')]
        if client and monthly and not any(metric_client(metric) == client for metric in monthly):
            item['task_scope_compatible'] = False
        if (client and selected_months and metrics
                and not any(metric_client(metric) == client for metric in metrics)
                and not any(dim.get('column') in ('client','client_alias')
                            for dim in (item.get('statistics') or {}).get('dimensions', []))):
            item['task_scope_compatible'] = False
        date_proof = item.get('date_completeness') or {}
        if client and date_proof:
            proof_client = metric_client(date_proof)
            if proof_client != client:
                item['task_scope_compatible'] = False
        contract = item.get('answer_contract') or {}
        if contract.get('kind') == 'monthly_report':
            actual_client = contract.get('client')
            if 'client' not in contract:
                try:
                    refs = json.loads(item.get('snippet') or '{}').get('input_evidence_ids', [])
                except (ValueError, TypeError, AttributeError):
                    refs = []
                clients = {metric_client(m) for sid in refs
                           for m in (lookup.get(sid, {}).get('statistics') or {}).get('metrics', [])}
                if len(clients) == 1:
                    actual_client = next(iter(clients))
            mismatch = client and actual_client != client
            if selected_months:
                mismatch = mismatch or (contract.get('start_month'), contract.get('end_month')) != (min(selected_months), max(selected_months))
            if mismatch:
                item['answer_contract_applicable'] = False
        result.append(item)
    return result


def fact_token(item):
    return f"{{{{facts:{item['id']}:{fingerprint(item)}}}}}"


def workload_requested(query):
    """Recognize person-level delivery, excluding definitions and assignment."""
    from casework.routing import affirmative
    positive = affirmative(query)
    if re.search(r'什么是|定义|什么意思|推荐|分案|候选|全组|所有人员|按(?:各)?(?:承办人|代理师|人员)|每位|各(?:承办人|代理师|人员)', positive):
        return False
    return bool(re.search(r'负荷|忙不忙|忙闲|有没有空|是否有空', positive) and
                re.search(r'查|列|看看|统计|报告|代理师|同事|负责|承办人|人员', positive))


def workload_summary_date_issues(query, items):
    from casework import analytics
    from casework.routing import dates
    if not re.search(r'今天|今日|当前',query) or dates(query):return []
    expected=analytics.today().isoformat()
    if any((item.get('answer_contract') or {}).get('kind')=='workload_summary'
           and item['answer_contract'].get('as_of')!=expected for item in items):
        return ['全员负荷基准日错误：本次用户要求当前/今天，必须按系统日期'+expected+'重新查询，不得沿用模型推测的日期或仅改正文日期。']
    return []


def workload_delivery_issues(query, items):
    """A case list is not a substitute for person/date-bound workload data.

    Inspect evidence already retrieved. This validator never opens an extra
    source, guesses an owner or executes a database query.
    """
    if not workload_requested(query):
        return []
    blocks = [i for i in items if (i.get('answer_contract') or {}).get('kind') == 'workload']
    if not blocks:
        return ['登记负荷缺少专用统计：案件清单不能代替负荷；请核实完整人员姓名，再调用query_department_cases(operation=workload,owner=完整姓名,as_of=原问题基准日)，使用返回的完整事实块']
    from casework.entities import normalize,aliases
    from casework.routing import dates
    from casework.parser import CASE_RE
    normalized = normalize(query)
    named = set()
    case_owners = set()
    case_ids = {m.group().upper() for m in CASE_RE.finditer(query)}
    for item in items:
        if item.get('case_id') in case_ids and item.get('owner_name'):
            case_owners.add(item['owner_name'])
        if item.get('type') != 'database':continue
        cols = item.get('columns') or []
        for row in item.get('rows') or []:
            if '代理师' not in cols or len(row) <= cols.index('代理师'):continue
            owner = row[cols.index('代理师')]
            if not isinstance(owner,str):continue
            if any(re.search(re.escape(a)+(r'(?!\d)' if a[-1:].isdigit() else ''),normalized)
                   for a in aliases(owner,'owner') if a):named.add(owner)
            if '所编号' in cols and len(row)>cols.index('所编号') and row[cols.index('所编号')] in case_ids:
                case_owners.add(owner)
    required_owners = named or case_owners
    if not required_owners:
        return ['登记负荷人员身份未绑定：请先用案件信息核实原问题指定人员或案件负责人，再执行workload；不能用无关人员的统计代替']
    requested_dates = dates(query)
    required_date = requested_dates[0] if len(requested_dates) == 1 else None
    def owner_of(item):
        dims=(item.get('statistics') or {}).get('dimensions') or []
        dim=next((d for d in dims if d.get('column')=='owner_name'),None)
        rows=item.get('rows') or []
        if dim and rows and 0<=dim['index']<len(rows[0]):return rows[0][dim['index']]
        return None
    matched=[i for i in blocks if not required_date or i['answer_contract'].get('as_of')==required_date]
    if required_owners and not required_owners <= {owner_of(i) for i in matched}:
        return ['登记负荷人员或基准日不匹配：须查询原问题指定人员/案件负责人及基准日的workload，不能用其他人员或其他日期事实块代替']
    if not matched:
        return ['登记负荷基准日不匹配：请按原问题基准日调用workload，不能用默认日期代替']
    return []


def render_answer(report, items, query=''):
    """Fail closed on stale selectors; never guess a source or rewrite a claim."""
    items = bind_task_scope(query, items) if query else items
    lookup = {item.get('id'): item for item in items}
    audit = []
    selectors = {option['selector_id']: (item, option)
                 for item in items for option in quote_options(item, limit=10000)}
    for item in items:
        _, changes = normalize_source(item.get('snippet') or '')
        if changes:
            audit.append({'kind':'source_normalization','evidence_id':item.get('id'),
                          'fingerprint':fingerprint(item),'normalizations':changes})
    def quote(match):
        ident, digest, start, end = match.groups()
        item = lookup.get(ident)
        if not item or fingerprint(item) != digest:
            return match[0]
        start, end = int(start), int(end)
        text = str(item.get('snippet') or '')
        if item.get('type') not in ('web', 'knowledge', 'upload', 'file') or not 0 <= start < end <= len(text) or end - start > 1200:
            return match[0]
        original = text[start:end]
        text, changes = normalize_source(original)
        audit.append({'kind': 'quote', 'evidence_id': ident, 'fingerprint': digest,
                      'start': start, 'end': end, 'original': original, 'normalizations': changes})
        if re.search(r'\[\d{1,5}\]', text):
            return '\n> ' + text.replace('\n','\n> ') + ' [' + ident + ']\n'
        return '“' + text.replace('\n',' ') + '”[' + ident + ']'
    def short_quote(match):
        selected = selectors.get(match[1])
        if selected is None:
            return match[0]
        item, option = selected
        # Reuse the full fingerprint/span validator. The model selects an ID;
        # the host supplies the original identity and extracts the literal text.
        token=f"{{{{quote:{item['id']}:{fingerprint(item)}:{option['start']}:{option['end']}}}}}"
        return quote(_QUOTE.fullmatch(token))
    def facts(match):
        item = lookup.get(match[1])
        if not item or fingerprint(item) != match[2]:
            return match[0]
        try:
            body = fact_block(item)
        except ValueError:
            return match[0]
        if not body:
            return match[0]
        audit.append({'kind': 'facts', 'evidence_id': item['id'], 'fingerprint': match[2]})
        return body
    # Do not expand a selector inside a JSON string into invalid JSON.
    try:
        json.loads(report.strip().removeprefix('```json').removesuffix('```'))
    except (ValueError, TypeError):
        report = _FACTS.sub(facts, _SHORT_QUOTE.sub(short_quote, _QUOTE.sub(quote, report)))
        for item in items:
            try:
                block = fact_block(item)
            except ValueError:
                continue
            if block and block not in report and not _equivalent_fact_block(block, report):
                report += '\n\n' + block
                audit.append({'kind': 'facts', 'evidence_id': item['id'],
                              'fingerprint': fingerprint(item), 'inserted_missing_block': True})
        report, due_audit = append_complete_due_lists(report, items, query)
        audit.extend(due_audit)
        report, provenance_audit = append_case_date_provenance(report, items, query)
        audit.extend(provenance_audit)
        if (any((item.get('answer_contract') or {}).get('kind')=='monthly_report' for item in items)
                and not re.search(r'基础记录数|案件总数|总案量|总体案量',query)):
            report='\n'.join(line for line in report.splitlines()
                             if not re.search(r'^\s*[-*]?\s*(?:\*\*)?基础记录数(?:\*\*)?\s*[：:]',line))
        from tools.chart_utils import _confirmed_ocr_tables, build_chart_items
        scanned=_confirmed_ocr_tables(items)
        if (len(scanned)==1 and len([item for item in items if item.get('type')=='file'
                                    and item.get('extraction_method')=='mineru'])==1
                and all(word in query for word in ('部件','编号','数量'))
                and re.search(r'柱状图|条形图',query)
                and len(scanned[0]['rows'])<=40):
            charts=build_chart_items(items,query)
            if len(charts)==1 and charts[0]['chart']['type'] in ('bar','hbar'):
                item=scanned[0];headers=item['columns'];rows=item['rows']
                if all(name in headers for name in ('部件','编号','数量')):
                    indices=[headers.index(name) for name in ('部件','编号','数量')]
                    source_ids=item['chart_source_ids']
                    location=re.search(r'\[第\s*\d+\s*页[^\n]*· 表格\]',
                                       str(next((e.get('snippet') for e in items if e.get('id')==item['id']),'') or ''))
                    where=location[0].strip('[]') if location else '原件表格区域'
                    lines=[f"已核对附件“{item['title']}”的{where}。MinerU表格与原件视觉核对一致，数量没有待确认的模糊项。[{source_ids[0]}][{source_ids[1]}]",
                           '', '| 部件 | 编号 | 数量 | 来源 |','| --- | --- | ---: | --- |']
                    for row in rows:
                        vals=[str(row[i]).replace('|','&#124;') for i in indices]
                        lines.append('| '+' | '.join(vals)+f" | [{source_ids[0]}][{source_ids[1]}] |")
                    total=sum(row[indices[2]] for row in rows)
                    lines += ['',f"数量合计：{total}。"+''.join('['+sid+']' for sid in source_ids)]
                    lines += ['','柱状图已在图表页生成；横轴为部件与编号，纵轴为数量，图中逐项数值与上表一致。']
                    report='\n'.join(lines)
                    audit.append({'kind':'confirmed_ocr_table_delivery','evidence_ids':source_ids,'rows':len(rows)})
        # A complete deadline query already contains the full source rows. Use
        # those rows for the delivery instead of carrying stale model selectors
        # or a partial preview into an otherwise complete answer.
        if (re.search(r'初稿.{0,50}(?:逾期|两周|14天)',query)
                and re.search(r'案号|所编号',query)):
            sources=_complete_due_sources(items, query)
            requested=[status for status,pattern in (('overdue',r'逾期'),
                        ('next14',r'两周|14天'),('unknown',r'没填|未填|缺失|未知'))
                       if re.search(pattern,query)]
            if requested and all(status in sources for status in requested):
                parts=[]
                audit=[entry for entry in audit if entry.get('kind')!='due_full_list']
                for status in requested:
                    item=sources[status]
                    contract={'kind':'due_case_list','version':1,'complete_snapshot':True,
                              'total':len(item['rows']),'due_status':status,
                              'due_field':'draft_due_date',
                              'as_of':(item.get('answer_contract') or {}).get('as_of') or
                                      __import__('datetime').date.today().isoformat()}
                    block = fact_block({**item,'answer_contract':contract})
                    if not block:
                        label = {'overdue':'初稿已逾期','next14':'初稿未来两周到期','unknown':'初稿期限未知'}[status]
                        block = f"### {label}\n\n本组无符合记录。[{item['id']}]"
                    parts.append(block)
                    audit.append({'kind':'due_full_list','evidence_id':item['id'],'rows':len(item['rows'])})
                from datetime import date,timedelta
                base=date.fromisoformat(contract['as_of'])
                refs=''.join('['+sources[status]['id']+']' for status in requested)
                scope=(f"统计范围：当前完整发布快照中的已知在办案件，排除已核实实际交局及非在办阶段。"
                       f"初稿逾期为期限早于{base.isoformat()}；未来两周为[{base.isoformat()}，"
                       f"{(base+timedelta(days=14)).isoformat()})，含起日、不含结束日；"
                       f"未填组查询初稿期限为空或冲突的记录。{refs}")
                full_block=_full_active_block(items, sources) if re.search(r'全部|所有|全量|在办',query) else ''
                other_blocks = [fact_block(item) for item in items
                    if (item.get('answer_contract') or {}).get('kind') == 'workload']
                report=scope+'\n\n'+(full_block+'\n\n' if full_block else '')+'\n\n'.join(parts + other_blocks)
    # A bounded monthly query asks for these source-owned numbers and event
    # identities, not a second free-form transcription of the same facts.
    # Preserve general/mixed tasks; only a complete applicable monthly body
    # may replace prose, and normal verification still runs on that exact body.
    monthly = [item for item in items if item.get('answer_contract_applicable') is not False
               and (item.get('answer_contract') or {}).get('kind') == 'monthly_report']
    if (monthly and re.search(r'统计|每月|各月|月报', query)
            and re.search(r'收案|交局', query) and re.search(r'案号|明细|清单', query)
            and re.search(r'实际日期|对应.*日期|日期', query)
            and not re.search(r'技术|相似|会议|推荐|PDF|Markdown|图表|为什么', query, re.I)):
        from tools.task_evidence_checks import task_evidence_issues
        selected = monthly[-1]
        body = fact_block(selected)
        if ('| 事件指标 | 所编号 | 实际日期 |' in body
                and ('收案日期缺失' not in query or '实际收案日期缺失数量' in body)
                and not task_evidence_issues(query, items)):
            report = body
            audit.append({'kind':'monthly_structured_delivery','evidence_id':selected['id'],
                          'replaced_freeform':True,'basis':'same_scope_verified_metrics_and_actual_event_rows'})
    # A model may copy the text beside its quote selector. Expansion must not
    # duplicate that same bound quotation in the user-facing answer.
    from tools.evidence_refs import _quote_supported
    pattern = re.compile(r'(?P<quote>“(?P<text>[^”\n]{12,})”)\s*(?P=quote)(?P<refs>(?:\s*\[S[1-9]\d*\])+)' )
    def collapse(match):
        refs = re.findall(r'\[(S\d+)\]', match['refs'])
        if not any(item.get('id') in refs and _quote_supported(match['text'], item.get('snippet', '')) for item in items):
            return match[0]
        audit.append({'kind': 'duplicate_bound_quote', 'evidence_ids': list(dict.fromkeys(refs))})
        return match['quote'] + ''.join('[' + sid + ']' for sid in dict.fromkeys(refs))
    report = pattern.sub(collapse, report)
    return report, audit


def _full_active_block(items, scope_sources):
    """One complete in-progress list with all three planned dates, if available."""
    from casework import analytics
    scope_keys = ('query', 'owner', 'client', 'domain', 'stage')
    def filters(item):
        try:
            value = json.loads(item.get('snippet') or '{}')
            return value if isinstance(value, dict) else None
        except (ValueError, TypeError):
            return None
    selected = list(scope_sources.values())
    if not selected or any(filters(item) is None for item in selected):
        return ''
    expected = {key: filters(selected[0]).get(key) or None for key in scope_keys}
    if any(any((filters(item).get(key) or None) != expected[key] for key in scope_keys) for item in selected):
        return ''
    as_of = (selected[0].get('answer_contract') or {}).get('as_of')
    if not as_of:
        return ''
    base = analytics.as_date(as_of)
    best=None
    for item in items:
        cols=item.get('columns') or []
        rows=item.get('rows') or []
        proof=item.get('completeness') or {}
        source_filters = filters(item)
        if (not item.get('casework') or source_filters is None
                or any(source_filters.get(k) for k in ('due_field','due_status','due_statuses','missing_date_field'))
                or any((source_filters.get(key) or None) != expected[key] for key in scope_keys)):
            continue
        if not rows or proof.get('has_more') or proof.get('fetch_truncated'):
            continue
        if proof.get('query_rows') != len(rows):
            continue
        if not all(label in cols for label in ('所编号','代理师','初稿期限／所限',
                                               '客户送审期限','向局提交期限','案件状态','实际交局日期','交局已核实')):
            continue
        if item.get('type')!='database':
            continue
        def is_active(row):
            return analytics.active({'stage':row[cols.index('案件状态')],
                'actual_filed_date':row[cols.index('实际交局日期')],
                'filing_verified':row[cols.index('交局已核实')]}, base)
        rows = [row for row in rows if is_active(row)]
        if best is None or len(rows)>len(best[1]):
            best=(item,rows)
    if not best:
        return ''
    item,rows=best
    cols=item['columns']
    indices=[cols.index(label) for label in ('所编号','代理师','初稿期限／所限',
                                             '客户送审期限','向局提交期限')]
    lines=['### 全部在办案件与三项计划期限','',
           '| 所编号 | 承办人 | 初稿期限／所限 | 客户送审期限 | 向局提交期限 | 来源 |',
           '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        values=[str(row[index] if row[index] is not None else '未核实').replace('|','&#124;')
                for index in indices]
        lines.append('| '+' | '.join(values)+f" | [{item['id']}] |")
    lines.append(f"共 {len(rows)} 件，本表覆盖本次筛选范围内、基准日{as_of}的全部已知在办案件。[{item['id']}]")
    return '\n'.join(lines)


def append_case_date_provenance(report, items, query):
    case=re.search(r'(?<!\w)AI-\d{4}-\d{3}(?!\w)',query)
    if (not case or not all(word in query for word in ('初稿','客户','提交'))
            or not re.search(r'出处|来源|冲突',query)):
        return report,[]
    proofs=[item for item in items if item.get('title')=='本案日期字段原件出处与冲突状态'
            and item.get('columns')==['所编号','字段','原件值','核对状态','原件路径','原件位置']]
    if len(proofs)!=1:return report,[]
    item=proofs[0]
    rows=[row for row in item.get('rows') or [] if row[0]==case[0]
          and row[1] in ('初稿期限／所限','客户送审期限','向局提交期限')]
    if not rows or any(not row[4] or not row[5] for row in rows):return report,[]
    lines=['### 三项计划期限的原件出处与冲突核对','','| 节点 | 原件值 | 核对状态 | 原件路径 | 原件位置 | 来源 |',
           '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        cell=lambda value: str(value).replace('|','&#124;').replace('\n',' ')
        lines.append('| '+' | '.join(cell(row[index]) for index in (1,2,3,4,5))+f" | [{item['id']}] |")
    states={row[3] for row in rows}
    if states=={'verified_source'}:
        lines.append(f"\n当前发布快照中，这三项计划期限均有一致的原件候选值；未发现这三项字段的候选值冲突。[{item['id']}]")
    else:
        lines.append(f"\n存在未核实或冲突的字段状态，须按上表逐项处理，不能把候选值当作确定期限。[{item['id']}]")
    block='\n'.join(lines)
    if block in report:return report,[]
    return report+'\n\n'+block,[{'kind':'case_date_provenance','evidence_id':item['id'],'rows':len(rows)}]


def case_date_provenance_issues(query, report, items):
    if (not re.search(r'(?<!\w)AI-\d{4}-\d{3}(?!\w)',query)
            or not all(word in query for word in ('初稿','客户','提交'))
            or not re.search(r'出处|来源|冲突',query)):
        return []
    proof=[item for item in items if item.get('title')=='本案日期字段原件出处与冲突状态']
    if len(proof)!=1:
        return ['三项计划期限缺少原件出处与冲突状态证据：请按案号重新查询cases，并引用逐字段原件路径和位置。']
    rows=proof[0].get('rows') or []
    if not {'初稿期限／所限','客户送审期限','向局提交期限'} <= {row[1] for row in rows if len(row)>5}:
        return ['三项计划期限的原件出处证据不完整：须核对三项字段及其原件位置。']
    _,audit=append_case_date_provenance(report,items,query)
    return ['三项计划期限的原件出处尚未完整交付：须逐字段列明原件值、状态、路径和位置。'] if audit else []


def _complete_due_sources(items, query=''):
    """Return only complete, unambiguous draft-deadline result sets."""
    found={}
    for item in items:
        try:
            filters=json.loads(item.get('snippet') or '{}')
        except (TypeError, ValueError):
            continue
        if not isinstance(filters,dict):
            continue
        status=filters.get('due_status')
        cols=item.get('columns') or []
        rows=item.get('rows') or []
        proof=item.get('completeness') or {}
        if (filters.get('due_field')!='draft_due_date' or status not in ('overdue','next14','unknown')
                or not all(label in cols for label in ('所编号','代理师','初稿期限／所限'))
                or proof.get('has_more') or proof.get('fetch_truncated')
                or proof.get('query_rows') != len(rows) or status in found):
            continue
        found[status]=item
    # The query tool can also return overdue and next14 in one complete page.
    # Partition its actual rows by the same date boundary; never infer missing
    # rows from a count or a preview.
    if 'overdue' not in found or 'next14' not in found:
        from datetime import timedelta
        from casework.analytics import today
        base=today()
        for item in items:
            try:filters=json.loads(item.get('snippet') or '{}')
            except (TypeError,ValueError):continue
            if not isinstance(filters,dict):continue
            cols=item.get('columns') or [];rows=item.get('rows') or []
            proof=item.get('completeness') or {}
            if (filters.get('due_field')!='draft_due_date'
                    or set(filters.get('due_statuses') or [])!={'overdue','next14'}
                    or not all(label in cols for label in ('所编号','代理师','初稿期限／所限'))
                    or proof.get('has_more') or proof.get('fetch_truncated')
                    or proof.get('query_rows')!=len(rows)):
                continue
            from datetime import date
            as_of=(item.get('answer_contract') or {}).get('as_of')
            if as_of:base=date.fromisoformat(as_of)
            at=cols.index('初稿期限／所限')
            overdue=[];next14=[]
            for row in rows:
                value=row[at]
                if not isinstance(value,str):break
                if value<base.isoformat():overdue.append(row)
                elif value<(base+timedelta(days=14)).isoformat():next14.append(row)
                else:break
            else:
                for status,subset in (('overdue',overdue),('next14',next14)):
                    found.setdefault(status,{**item,'rows':subset,
                        'completeness':{**proof,'query_rows':len(subset)},
                        'answer_contract':{'as_of':base.isoformat()}})
            break
    # A complete owner-filtered case table already contains the three dates.
    # Derive groups from those actual rows instead of asking the model to copy
    # workload counts without the corresponding case IDs. Never use a preview.
    from casework import analytics
    from casework.entities import aliases, normalize
    from casework.routing import dates
    requested_dates = dates(query)
    workloads = [item for item in items if (item.get('answer_contract') or {}).get('kind') == 'workload']
    anchors = workloads + ([None] if requested_dates and '基准' in query else [])
    for workload in anchors:
        contract = (workload or {}).get('answer_contract') or {}
        if workload is not None and not contract.get('as_of'):
            continue
        base = analytics.as_date(contract.get('as_of') or requested_dates[0])
        if requested_dates and requested_dates[0] != base.isoformat():
            continue
        for item in items:
            try:
                filters = json.loads(item.get('snippet') or '{}')
            except (TypeError, ValueError):
                continue
            if not isinstance(filters, dict) or not filters.get('owner'):
                continue
            owner = filters['owner']
            if not any(alias in normalize(query) for alias in aliases(owner, 'owner')):
                continue
            dims = ((workload or {}).get('statistics') or {}).get('dimensions') or []
            owner_dim = next((dim for dim in dims if dim.get('column') == 'owner_name'), None)
            workload_rows = (workload or {}).get('rows') or []
            if workload is not None and (not owner_dim or len(workload_rows) != 1
                    or len(workload_rows[0]) <= owner_dim['index']
                    or workload_rows[0][owner_dim['index']] != owner):
                continue
            if any(filters.get(key) and normalize(str(filters[key])) not in normalize(query)
                   for key in ('query','client','domain','stage')):
                continue
            columns, rows = item.get('columns') or [], item.get('rows') or []
            proof = item.get('completeness') or {}
            if (not item.get('casework') or item.get('type') != 'database'
                    or proof.get('has_more') or proof.get('fetch_truncated')
                    or proof.get('query_rows') != len(rows)
                    or any(filters.get(k) for k in ('due_field','due_status','due_statuses','missing_date_field'))
                    or not all(label in columns for label in ('所编号','代理师','初稿期限／所限',
                        '客户送审期限','向局提交期限','案件状态','实际交局日期','交局已核实'))):
                continue
            def active(row):
                return row[columns.index('代理师')] == owner and analytics.active({
                    'stage':row[columns.index('案件状态')],
                    'actual_filed_date':row[columns.index('实际交局日期')],
                    'filing_verified':row[columns.index('交局已核实')]}, base)
            from casework.requirements import due_group
            active_rows = [row for row in rows if active(row)]
            for status in ('overdue','next14','unknown'):
                subset = [row for row in active_rows if due_group(row[columns.index('初稿期限／所限')], base.isoformat()) == status]
                found.setdefault(status, {**item, 'rows':subset,
                    'completeness':{**proof,'query_rows':len(subset)},
                    'answer_contract':{'as_of':base.isoformat()}})
            return found
    return found


def append_complete_due_lists(report, items, query=''):
    """Deliver rows already fetched for an explicit case-and-owner list request."""
    if not re.search(r'初稿.{0,50}(?:逾期|两周|14天)', query) or not re.search(r'案号|所编号',query):
        return report, []
    sources=_complete_due_sources(items, query)
    requested=[status for status,pattern in (('overdue',r'逾期'),('next14',r'两周|14天'),('unknown',r'没填|未填|缺失|未知'))
               if re.search(pattern,query)]
    audit=[]
    for status in requested:
        item=sources.get(status)
        if not item:
            continue
        cols=item['columns'];rows=item['rows']
        label={'overdue':'已逾期','next14':'未来两周到期','unknown':'初稿期限未填写'}[status]
        lines=[f'### {label}：完整案号与承办人（{len(rows)} 件）','','| 所编号 | 承办人 | 初稿期限 | 来源 |','| --- | --- | --- | --- |']
        for row in rows:
            cell=lambda name: str(row[cols.index(name)] if row[cols.index(name)] is not None else '未核实').replace('|','&#124;')
            lines.append(f"| {cell('所编号')} | {cell('代理师')} | {cell('初稿期限／所限')} | [{item['id']}] |")
        if not rows:
            lines.append(f"本次完整查询结果为 0 件。[{item['id']}]")
        block='\n'.join(lines)
        if block in report:
            continue
        report+='\n\n'+block
        audit.append({'kind':'due_full_list','evidence_id':item['id'],'rows':len(rows)})
    if audit:
        report=report.replace('如需看到全量明细，请告诉我。','完整案号与承办人见下方清单。')
        if all(status in sources for status in requested):
            report='\n'.join(line for line in report.splitlines()
                             if not re.search(r'明细列表只成功返回了部分行|无法给出完整的逐一案号|明细行未成功返回|只返回了部分行',line))
            report=report.replace('已获取的部分明细如下：','以下先列示部分明细，完整清单见下方：')
    return report,audit


def _cells(line): return [part.strip() for part in re.split(r'(?<!\\)\|', line.strip().strip('|'))]


def _fact_block_cells(block):
    """Return the data rows of a fact block's first Markdown table."""
    lines=[line for line in block.splitlines()]
    table=[line for line in lines if line.strip().startswith('|')]
    if len(table)<2:return []
    rows=[]
    for line in table[1:]:
        if re.fullmatch(r'[\s|:\-]+', line.strip()):continue
        rows.append([cell.strip() for cell in _cells(line)])
    return rows


def _equivalent_fact_block(block, report):
    """Ignore layout, never field labels, values, scope or duplicate rows."""
    from collections import Counter
    def cell(value):
        return re.sub(r'\s+', ' ', value.replace('**', '').strip())
    def tables(text):
        lines = text.splitlines()
        for i in range(len(lines)-1):
            if not lines[i].strip().startswith('|') or not re.fullmatch(r'[\s|:\-]+', lines[i+1]):
                continue
            headers = [cell(v) for v in _cells(lines[i])]
            if len(set(headers)) != len(headers):
                continue
            rows = []
            for line in lines[i+2:]:
                if not line.strip().startswith('|'):
                    break
                values = [cell(v) for v in _cells(line)]
                if len(values) != len(headers):
                    break
                rows.append(tuple(sorted(zip(headers, values))))
            yield frozenset(headers), Counter(rows)
    expected = list(tables(block))
    actual = list(tables(report))
    if not expected or any(t not in actual for t in expected):
        return False
    # Keep every non-table definition/limitation, including the base date and
    # empty/unknown scope. Whitespace reflow is harmless; changed meaning is not.
    normalize = lambda text: re.sub(r'\s+', '', text.replace('**', ''))
    body = normalize(report)
    return all(normalize(line) in body for line in block.splitlines()
               if line.strip() and not line.strip().startswith('|'))


def contract_issues(report, items):
    issues = []
    if re.search(r'\{\{(?:quote|facts):', report):
        issues.append('证据选择标记未能解析：须使用本次来源的完整有效标记，不能猜测或沿用旧版本')
    for item in items:
        if not item.get('answer_contract'):
            continue
        try:
            block = fact_block(item)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not block:
            continue
        if block in report:
            continue
        if _equivalent_fact_block(block, report):
            continue
        issues.append(f"事实块缺失或被改写：请原样使用{fact_token(item)}；须一起保留数值、日期、范围、未知和口径")
    return issues
