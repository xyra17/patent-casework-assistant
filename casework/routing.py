"""Semantic read contracts with an offline grammar and independent coverage checks."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from casework import analytics, catalog, parser, store
from casework.workflow import DepartmentRequest, plan_details, sources_for, validate_request


@dataclass
class RouteDecision:
    status: str
    reason: str
    request: DepartmentRequest | None = None
    method: str = "contract"
    diagnostics: list | None = None

    def audit(self):
        return {"status": self.status, "reason": self.reason, "method": self.method,
                "request": self.request.model_dump(exclude_none=True) if self.request else None,
                **({'diagnostics':self.diagnostics} if self.diagnostics else {})}


def affirmative(text):
    """Negative clauses must not become requested operations or retrieval terms."""
    return "；".join(c for c in re.split(r"[。；;，,\n？！?!]", text)
                    if c.strip() and not re.match(r"\s*(?:别|不要|不得|不能|不准|禁止|不查|不联网|不用|无需|不生成|不把|不保存|不访问)", c))


def affirmative_quote(text, quote):
    """Check original character spans without rewriting their punctuation."""
    for occurrence in re.finditer(re.escape(quote),text):
        clauses=[m.group() for m in re.finditer(r'[^。；;，,\n？！?!]+',text)
                 if m.end()>occurrence.start() and m.start()<occurrence.end()]
        if clauses and all(affirmative(clause).strip() for clause in clauses):return True
    return False


def dates(text):
    found = []
    for match in re.finditer(r"(?<!\d)(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})日?(?!\d)", text):
        value = f"{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}"
        analytics.as_date(value)
        if value not in found:
            found.append(value)
    return found


def months(text):
    values = []
    year = None
    for match in re.finditer(r"(?:(\d{4})[-年/])?(\d{1,2})月|(?<!\d)(\d{4})-(\d{2})(?![-\d])", text):
        year = int(match[1] or match[3] or year or analytics.today().year)
        value = f"{year:04d}-{int(match[2] or match[4]):02d}"
        analytics.month_bounds(value)
        if value not in values:
            values.append(value)
    if not values and re.search(r"上(?:一|个|一个)?(?:自然)?月|前一个月|本(?:自然)?月|这个月|当前自然月", text):
        today = analytics.today()
        this = today.replace(day=1)
        from datetime import timedelta
        if re.search(r"上(?:一|个|一个)?(?:自然)?月|前一个月", text):
            values.append((this - timedelta(days=1)).strftime("%Y-%m"))
        if re.search(r"本(?:自然)?月|这个月|当前自然月", text):
            values.append(this.strftime("%Y-%m"))
    return values


def known_entities(text):
    text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).casefold()
    cases = store.list_cases()
    meetings = catalog.meetings()
    values = {
        "client": {c["client_alias"] for c in cases if c.get("client_alias")},
        "owner": {c["owner_name"] for c in cases if c.get("owner_name")},
        "domain": {c["technical_domain"] for c in cases if c.get("technical_domain")},
        "ipr": {p for m in meetings for p in m["iprs"]},
        "stage": {c["stage"] for c in cases if c.get("stage")},
    }
    matches = {}
    for field, options in values.items():
        hits = []
        for value in options:
            alias = re.sub(r"[（(]模拟[）)]", "", value).casefold()
            aliases = [alias]
            if field=='domain':
                from casework.domains import mentioned
                if mentioned(text,value):hits.append(value)
                continue
            if field == 'ipr':
                match = re.fullmatch(r'ipr(.+)', alias)
                if match:aliases.append(match[1]+'ipr')
                match = re.fullmatch(r'(.+)ipr', alias)
                if match:aliases.append('ipr'+match[1])
            from casework.entities import aliases as entity_aliases
            aliases=entity_aliases(value,field)
            if any(re.search(re.escape(a)+(r'(?!\d)' if a[-1:].isdigit() else ''),text) for a in aliases if a and not a.isdecimal()):
                hits.append(value)
        matches[field] = sorted(hits)
    return matches


def result_limit(text):
    """Small explicit material counts; shared by routing and independent checks."""
    match = re.search(r'(?:找|给|提供|检索|列|返回)(?:出|我|一下|到|来|上)?\s*([一二两三四五六七八九十\d]+)\s*(?:个|件|份|篇)', text)
    if not match:return None
    value = match[1]
    if value.isdecimal():return int(value)
    digits = dict(zip('一二两三四五六七八九', (1,2,2,3,4,5,6,7,8,9)))
    if value in digits:return digits[value]
    if value == '十':return 10
    if value.count('十') == 1:
        tens, ones = value.split('十')
        if (not tens or tens in digits) and (not ones or ones in digits):
            return digits.get(tens,1)*10+digits.get(ones,0)
    return None


def _recognize(query):
    text = unicodedata.normalize("NFKC", query)
    positive = affirmative(text)
    network = re.sub(r'(?:不|不要|不得|禁止|无需)(?:再|去|进行)?(?:联网|上网|互联网|公开网络|实时检索)', '', positive)
    if re.search(r'联网|上网|互联网|公开网络|实时检索', network):
        return RouteDecision('general', '显式公开网络任务不属于部门固定只读动作')
    if re.search(r'(?:排除|不要|不含|不包括)(?:查|找|看|检索|列出|包含|筛选|的|这些|任何|所有|\s)*(?:已完成|已交局|客户|代理师|领域)|只要未|未交局的|未完成的', text):
        return RouteDecision('general', '额外排除或未完成筛选须完整规划，不能丢弃')
    # Unsupported transformations remain with the full agent. Negated write or
    # legal instructions are constraints, not requests for those operations.
    if re.search(r"自动分案|保存分案|写入|修改(?:源|共享|案件信息|台账)|删除|导出|生成(?:报告|PDF|文件)|计算法定|判断.{0,8}(?:新颖性|创造性)|增长率|同比|环比|自定义|打分|评分|比较(?!合适)|对比", positive):
        return RouteDecision("general", "包含固定只读动作以外的写入、计算或专业分析")
    ids = list(dict.fromkeys(m.group().upper() for m in parser.CASE_RE.finditer(text)))
    if len(ids) > 1:
        return RouteDecision("general", "多案组合须完整规划，不能缩成单案核对")
    entity = known_entities(positive)
    if not entity['owner'] and re.search(r'(?:代理师|承办人)[:：为是]?\s*[^。；，,\s]{1,12}的', positive):
        return RouteDecision('general', '指定承办人未绑定目录，不能退化为全员查询')
    if not entity['domain'] and re.search(r'领域(?:为|是|[:：])|[^。；，,\s]{1,8}领域', positive):
        return RouteDecision('general', '指定领域未绑定目录，不能丢弃领域过滤')
    # Multiple clients remain an explicit filter, not an all-client listing.
    meeting = bool(re.search(r"三方|参会|会议", positive))
    recommend = bool(re.search(r"推荐|代理师候选|候选代理师|分给|分案|谁.{0,5}(?:适合|来写)", positive))
    monthly = bool(re.search(r"统计|合计|数量|多少|月报", positive) and re.search(r"收案|接收|新收|交局|实际完成", positive))
    drawing = bool(re.search(r"Visio|\.vsdx?\b|绘图附件|绘图文件",positive,re.I))
    body = drawing or bool(re.search(r"交底|技术原文|技术正文|正文|技术机制|相似|类似|历史.{0,6}(?:案|技术)|做过|写过", positive))
    ledger = bool(re.search(r"案件信息|台账|期限|所限|承办|代理师|实际日期|计入|逾期|到期|待核实", positive))
    if not any((meeting, recommend, monthly, body, ledger, ids)):
        return RouteDecision("unrecognized", "不属于确定性语言覆盖范围", method="semantic")
    if re.search(r"客户|IPR", positive, re.I) and not any(entity[f] for f in ("client", "ipr")):
        # “客户期限” is a field, not a client identity filter.
        residual = re.sub(r"客户(?:期限|送审|审核|审阅)|客户/IPR(?:审核)?", "", positive)
        if re.search(r"(?:客户|IPR)[\w\u4e00-\u9fff]+", residual, re.I):
            return RouteDecision("general", "客户/IPR名称未能唯一绑定目录，不能丢弃过滤条件")
    if any(len(entity[f]) > 1 for f in ("owner", "domain")):
        return RouteDecision("general", "多个承办人或领域筛选需要完整规划")
    if not meeting and any(len(entity[f]) > 1 for f in ("client", "ipr")):
        return RouteDecision("general", "多个客户或IPR条件不能缩成无过滤查询")
    get = lambda key: entity[key][0] if len(entity[key]) == 1 else None
    ds = dates(positive)
    if len(ds) > 1:
        return RouteDecision("general", "多个日期不能压成单一基准日")
    if re.search(r"(?:未来|接下来|近|内)\s*(\d+)\s*天", positive) and not re.search(r"(?:未来|接下来|近|内)\s*14\s*天", positive):
        return RouteDecision("general", "自定义天数不属于固定14天期限动作")
    base = ds[0] if ds else analytics.today().isoformat()
    params = {}
    if recommend:
        if meeting or ids or get("owner") or get("client") or get("ipr"):
            return RouteDecision("general", "推荐附加了未支持的对象约束")
        params = dict(operation="recommend", query=positive, as_of=base)
        if get("domain"):
            params["domain"] = get("domain")
    elif meeting:
        if ids or get("owner") or get("domain") or ds or re.search(r"\d{4}年\d+月|截至|截止|上月|本月", positive):
            return RouteDecision("general", "会议包含固定流程尚不支持的时间、案号或参会代理师过滤")
        params = dict(operation="meetings", client=get("client"), ipr=get("ipr"))
        if len(entity['ipr']) > 1:
            return RouteDecision('general', '多个IPR筛选需要完整规划，不能退化成全部会议')
        if len(entity["client"]) > 1 and not re.search(r"区分|分别|分开", positive):
            return RouteDecision("general", "多客户条件需要明确展示范围")
        if len(entity['client']) > 1:
            params['clients'] = entity['client']
    elif ids:
        if get("client") or get("owner") or get("domain"):
            return RouteDecision("general", "单案附加对象过滤须完整规划")
        if drawing and ledger:return RouteDecision('general','绘图与案件信息组合须完整规划')
        params = dict(operation="case_brief" if body and ledger else "archive" if body else "cases", query=ids[0])
        if drawing:params['archive_kind']='drawing'
        ms = months(positive)
        if params['operation'] == 'cases' and len(ms) == 1 and re.search(r'计入|能算|算.{0,6}(?:实际完成|交局)|交局统计', positive):
            params['filing_month'] = ms[0]
    elif monthly:
        ms = months(positive)
        if not ms or len(ms) > 2 or ds or get("domain") or get("owner") or get("ipr") or re.search(r"按.{0,8}(?:领域|代理师|客户)|分组|关键词|主题", positive):
            return RouteDecision("general", "月份或统计维度超出固定每月两指标合计")
        params = dict(operation="monthly", start_month=min(ms), end_month=max(ms), client=get("client"))
    elif body:
        if get("client") or get("owner") or get("domain") or ds or re.search(r"20\d{2}年|上月|本月|(?:只|仅).{0,4}(?:Word|PDF|申请文件|交底书)", positive, re.I):
            return RouteDecision("general", "技术检索附加了未支持的对象、日期或文件类型筛选")
        params = dict(operation="archive", query=positive,
                      completed_only=bool(re.search(r"已(?:经)?交局|已(?:经)?完成|已(?:经)?交付|完成的|交局的", positive)))
        if drawing:params['archive_kind']='drawing'
        limit = result_limit(positive)
        if limit is not None:params['result_limit'] = limit
    elif ledger:
        nodes = [("draft_due_date", r"初稿|所限"), ("client_review_due_date", r"客户期限|客户送审|送审期限"),
                 ("filing_due_date", r"提交期限|交局期限|向局提交")]
        fields = [field for field, pattern in nodes if re.search(pattern, positive)]
        statuses = [status for status, pattern in [("overdue", r"逾期|超期|过期"), ("next14", r"14\s*天|两周|近期到期"), ("unknown", r"未知|缺失|冲突|待核实")]
                    if re.search(pattern, positive)]
        if len(statuses) > 1 and "unknown" in statuses and re.search(r"(?:日期|未知|冲突).{0,8}(?:另列|另说|单列)|未知.*(?:另列|另说)", positive):
            statuses.remove("unknown")
        if len(fields) != 1 or len(statuses) != 1:
            return RouteDecision("general", "期限节点或条件不能唯一绑定")
        if entity['stage']:
            return RouteDecision('general', '显式阶段筛选须完整规划，不能静默改成统一在办范围')
        params = dict(operation="cases", due_field=fields[0], due_status=statuses[0], as_of=base,
                      owner=get("owner"), client=get("client"), domain=get("domain"))
    else:
        return RouteDecision("unrecognized", "请语义提取业务动作", method="semantic")
    if params['operation']=='archive' and not params.get('archive_kind'):
        from casework.archive_intent import bind_intent
        params['archive_intent']=bind_intent(query)
    request = DepartmentRequest(**params)
    return RouteDecision("matched", "业务动作与参数通过固定契约", request)


async def select_route(query, forbidden=(), *, allow_semantic=True):
    """A single route decision; forbidden dependencies never become permissions."""
    from casework.requirements import external_comparison_requested,case_mechanism_judgment_requested
    if case_mechanism_judgment_requested(query):
        return RouteDecision('general','指定案件是否采用机制需要综合解释，不能用原文摘录代替结论')
    if external_comparison_requested(query):
        return RouteDecision('general','外部公开比较需要完整分析，不能缩成内部原文或引用查询')
    # A grammar match is only a candidate, not proof that all natural-language
    # clauses were understood. Production always extracts the whole task; the
    # explicit offline mode exists for grammar regression tests and diagnostics.
    if allow_semantic:
        from casework.workflow import resolve
        try:
            request = await resolve(query)
            decision = RouteDecision("matched" if request else "general", "语义提取已校验" if request else "语义提取不支持固定动作", request, "semantic")
        except Exception as exc:
            from api.clarification import ClarificationRequired
            if isinstance(exc,ClarificationRequired):raise
            return RouteDecision("general", "语义提取未完成：" + type(exc).__name__, method="semantic",
                                 diagnostics=getattr(exc,'diagnostics',None))
    else:
        try:
            decision = _recognize(query)
        except ValueError:
            return RouteDecision('general', '日期或业务参数无效，不能执行有损转换')
    if decision.request:
        if not validate_request(query, decision.request):
            return RouteDecision("general", "业务参数未保留显式案件锚点")
        from casework.verification import task_contract_issues
        issues = task_contract_issues(query, decision.request, {})
        if issues:
            return RouteDecision('general', '执行契约遗漏任务条件：'+'；'.join(issues), method=decision.method)
        constraint_denials={'forbid_db':'db_query','forbid_knowledge':'knowledge','forbid_web':'web_search'}
        denied = set(sources_for(decision.request)) & (set(forbidden) | {
            constraint_denials[c.kind] for c in decision.request.constraints if c.kind in constraint_denials})
        if denied:
            return RouteDecision("blocked", "所需来源被当前任务禁止：" + "、".join(sorted(denied)))
    return decision


def apply_route(plan, decision):
    """General planning cannot silently veto or invent a department request."""
    if decision.status != "matched" or not decision.request:
        return plan.model_copy(update={"department_request": None})
    request = decision.request
    return plan.model_copy(update={"department_request": request, "required_sources": sources_for(request),
                                   "task_type": "analysis" if request.operation in ("recommend", "monthly") else "qa",
                                   **plan_details(request)})
