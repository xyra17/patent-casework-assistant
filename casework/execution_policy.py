"""Closed task contracts and four execution strategies, not four services.

Only whole-input grammars may authorize model-free review. A semantic model's
confidence, a tool success, or a strategy label never grants that exemption.
"""
from __future__ import annotations

import hashlib
import os
import re

FIELD_ALIASES = {
    '负责人':'owner_name', '承办人':'owner_name', '代理师':'owner_name',
    '客户简称':'client_alias', '发明名称':'title', '办理阶段':'stage', '案件状态':'stage',
    '实际收案日期':'received_date', '收案日期':'received_date',
    '初稿计划期限':'draft_due_date', '初稿期限':'draft_due_date', '所限':'draft_due_date',
    '客户送审计划期限':'client_review_due_date', '客户送审期限':'client_review_due_date',
    '向局提交计划期限':'filing_due_date', '向局提交期限':'filing_due_date',
    '实际交局日期':'actual_filed_date', '交局是否已经核实':'filing_verified',
    '交局核实状态':'filing_verified',
}
FIELD_LABELS = {
    'owner_name':'负责人','client_alias':'客户简称','title':'发明名称','stage':'办理阶段',
    'received_date':'实际收案日期','draft_due_date':'初稿计划期限',
    'client_review_due_date':'客户送审计划期限','filing_due_date':'向局提交计划期限',
    'actual_filed_date':'已核实的实际交局日期','filing_verified':'交局核实状态',
}


def _parts(query):
    return [p.strip() for p in re.split(r'[。；;\n]',query) if p.strip()]


def _constraints(parts, fields=()):
    from casework.requirements import TaskConstraint
    result=[]
    for part in parts:
        if part.rstrip('？?')=='未核实则回答未核实' and 'actual_filed_date' in fields:
            continue
        kind={'不联网':'forbid_web','不要联网':'forbid_web','仅查询案件信息':'no_technical_body',
              '不修改文件':'read_only','请注明来源':'cite_evidence','注明来源':'cite_evidence'}.get(part)
        if kind is None:return None
        result.append(TaskConstraint(kind=kind,quote=part))
    return result


def field_request(query):
    from casework.parser import CASE_RE
    from casework.workflow import DepartmentRequest
    from casework.requirements import TaskRequirement
    parts=_parts(query)
    if not parts:return None
    match=re.fullmatch(r'(?:请)?(?:查看|查询|查一下|查)?\s*(?P<case>'+CASE_RE.pattern+r')'
                      r'\s*的(?:案件信息[，,]?\s*(?:的)?)?\s*(?P<fields>.+?)[？?]?',parts[0],re.I)
    if not match:return None
    names='|'.join(sorted(map(re.escape,FIELD_ALIASES),key=len,reverse=True))
    fields=[]
    for piece in re.split(r'\s*(?:、|，|,|以及|和|及|与)\s*',match['fields']):
        found=re.fullmatch(r'(?:已核实的)?('+names+r')(?:是什么|是谁|是哪个|是哪位)?',piece)
        if not found:return None
        field=FIELD_ALIASES[found[1]]
        if field in fields:return None
        fields.append(field)
    constraints=_constraints(parts[1:],fields)
    if not fields or constraints is None:return None
    return DepartmentRequest(operation='cases',query=match['case'].upper(),
        case_field=fields[0] if len(fields)==1 else None,
        case_fields=fields if len(fields)>1 else [],
        requirements=[TaskRequirement(kind='case_list',quote=parts[0])],constraints=constraints)


def closed_request(query):
    """A deliberately finite language; unknown clauses fall back to planning."""
    if os.getenv('CASEWORK_PROGRAM_CONTRACTS','1')=='0':return None
    from casework.workflow import DepartmentRequest
    from casework.requirements import TaskRequirement
    from casework import analytics,store
    direct=field_request(query)
    if direct:return direct
    parts=_parts(query)
    if not parts:return None
    constraints=_constraints(parts[1:])
    if constraints is None:return None
    text=parts[0].rstrip('？?')
    from casework.parser import CASE_RE
    brief=re.fullmatch(r'(?:为|给)('+CASE_RE.pattern+r')整理会前卡'
        r'(?:，包括负责人、三项计划期限、申请文件技术原文和最近一次会议待补材料)?',text,re.I)
    if brief:
        return DepartmentRequest(operation='case_brief',query=brief[1].upper(),meeting_scope='latest',
            constraints=constraints,requirements=[TaskRequirement(kind=kind,quote=parts[0]) for kind in
                ('case_owner','case_deadlines','case_technology','case_material_gaps')])
    # Clients must match a complete known identity, never fuzzy aliases.
    clients={c.get('client_alias') for c in store.list_cases() if c.get('client_alias')}
    scope_pattern='|'.join(sorted(map(re.escape,clients),key=len,reverse=True)) or r'(?!)'
    monthly=re.fullmatch(r'统计(?:(?P<client>'+scope_pattern+r')的)?'
        r'(?P<start>\d{4}-\d{2})(?:至(?P<end>\d{4}-\d{2}))?'
        r'(?:每月|的)?实际收案数量和已核实实际交局数量',text)
    if monthly:
        try:
            start,end=monthly['start'],monthly['end'] or monthly['start']
            analytics.month_bounds(start);analytics.month_bounds(end)
            return DepartmentRequest(operation='monthly',start_month=start,end_month=end,
                client=monthly['client'],constraints=constraints,
                requirements=[TaskRequirement(kind='monthly_counts',quote=parts[0])])
        except ValueError:return None
    listing=re.fullmatch(r'列出(?P<client>'+scope_pattern+r')的全部案件(?:及负责人和三项计划期限)?',text)
    if listing:
        return DepartmentRequest(operation='cases',client=listing['client'],constraints=constraints,
            requirements=[TaskRequirement(kind='case_list',quote=parts[0])])
    due=re.fullmatch(r'以(?P<date>\d{4}-\d{2}-\d{2})为基准日，列出'
        r'(?:(?P<client>'+scope_pattern+r')的)?'
        r'(?P<node>初稿|客户送审|向局提交)计划期限(?P<status>已逾期|未来14天到期|未知)的全部在办案件',text)
    if due:
        try:analytics.as_date(due['date'])
        except ValueError:return None
        status={'已逾期':'overdue','未来14天到期':'next14','未知':'unknown'}[due['status']]
        kind={'overdue':'deadline_overdue','next14':'deadline_upcoming','unknown':'deadline_unknown'}[status]
        return DepartmentRequest(operation='cases',client=due['client'],as_of=due['date'],
            due_field={'初稿':'draft_due_date','客户送审':'client_review_due_date','向局提交':'filing_due_date'}[due['node']],
            due_status=status,constraints=constraints,
            requirements=[TaskRequirement(kind=kind,quote=parts[0])])
    return None


def strategy(request=None, *, route_method='', uploads=False, rules=False, require_pdf=False):
    """Describe the chosen executor, not a confidence score or authority grant."""
    if request is None or uploads or rules or require_pdf:
        return {'kind':'agent','reason':'需要完整规划、附件处理或附加约束','review':'semantic'}
    if request.case_field or request.case_fields:
        return {'kind':'direct','reason':'明确单案字段查询','review':'program_if_closed'}
    if request.operation in ('archive','recommend'):
        return {'kind':'conditional','reason':'固定读取起步，按证据缺口选择或澄清','review':'semantic'}
    return {'kind':'workflow','reason':'固定业务步骤和程序取数','review':'program_if_closed' if route_method=='closed_contract' and request.operation in ('cases','monthly') else 'semantic'}


def program_review_eligible(result):
    """Reparse the entire task and verify immutable report/evidence seals."""
    from casework import store
    from casework.workflow import DepartmentRequest
    from casework.verification import task_contract_issues
    check=result.get('business_check') or {}
    if check.get('status')!='passed':return False
    if check.get('report_sha256')!=hashlib.sha256(result.get('report','').encode()).hexdigest():return False
    if check.get('evidence_sha256')!=store.digest(result.get('evidence',[])):return False
    try:
        expected=closed_request(result.get('query',''))
        actual=DepartmentRequest(**check['request'])
        if expected is None or expected.operation not in ('cases','monthly') or actual.model_dump()!=expected.model_dump():return False
        contract=result.get('execution_contract') or {}
        if (set(contract.get('executed_sources',[]))!=set(result.get('required_sources',[]))
                or contract.get('source_files_written') or contract.get('assignment_saved')
                or contract.get('artifacts_created')):return False
        return not task_contract_issues(result['query'],actual,result.get('business_delivery',{}))
    except (KeyError,ValueError,TypeError):return False
