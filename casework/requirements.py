"""Task clauses are data: preserve them before choosing bounded read actions."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from casework.parser import TAG_RULES


class TechnicalConcept(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tag: Literal[tuple(TAG_RULES)]
    quote: str=Field(min_length=1,max_length=500,description='逐字摘录用户正向要查的技术机制；禁止内容不能作为检索概念')


class TaskRequirement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quote: str = Field(min_length=1, max_length=500, description='逐字复制原问题中本项要求，不改写、不新增')
    kind: Literal['case_list','case_date_unknown','case_material_gaps','case_counts','archive_materials', 'meeting_attendance','meeting_pending','meeting_content','meeting_changes', 'monthly_counts', 'monthly_comparison',
                  'candidate_recommendation', 'candidate_history', 'candidate_workload', 'deadline_upcoming', 'deadline_overdue', 'deadline_unknown',
                  'case_owner', 'case_technology', 'case_deadlines', 'filing_month', 'markdown_report', 'other']=Field(
        description='辅助分案目标=candidate_recommendation；候选历史经历=candidate_history；候选在办及期限负荷=candidate_workload。other只表示现有动作确实不具备的能力。')


class TaskConstraint(BaseModel):
    """Finite execution conditions; never a sink for unsupported deliverables."""
    model_config = ConfigDict(extra='forbid')
    quote: str = Field(min_length=1, max_length=500)
    kind: Literal['data_scope', 'read_only', 'show_conflicts', 'actual_events', 'unique_cases',
                  'unknown_separate', 'manual_diagram', 'no_technical_body',
                  'workload_basis', 'cite_evidence', 'any_completion_state', 'require_technical_history', 'actual_attendees', 'lexical_only', 'no_recommendation', 'forbid_db', 'forbid_knowledge', 'forbid_web']


CONSTRAINT_ACTIONS = {
    'no_recommendation': {'archive','cases','case_brief','monthly','meetings','summary'},
    'data_scope': {'archive','cases','case_brief','monthly','meetings','recommend','summary'},
    'read_only': {'archive','cases','case_brief','monthly','meetings','recommend'},
    'cite_evidence': {'archive','cases','case_brief','monthly','meetings','recommend'},
    'show_conflicts': {'cases','case_brief','monthly'}, 'actual_events': {'monthly','cases'},
    'unique_cases': {'monthly'}, 'unknown_separate': {'monthly','cases','recommend'},
    'manual_diagram': {'archive'}, 'no_technical_body': {'cases','monthly','meetings'},
    'any_completion_state': {'archive'}, 'workload_basis': {'recommend'}, 'require_technical_history': {'recommend'}, 'actual_attendees': {'meetings'}, 'lexical_only': {'archive'},
    **{k: {'archive','cases','case_brief','monthly','meetings','recommend'}
       for k in ('forbid_db','forbid_knowledge','forbid_web')},
}
for _kind in ('read_only','cite_evidence','forbid_db','forbid_knowledge','forbid_web'):
    CONSTRAINT_ACTIONS[_kind].add('summary')


def workload_authorized(query):
    """Require both workload intent and explicit exclusion of technical fit.

    Check the original task, not a fragile word-order regex on one quoted fragment.
    Missing evidence never satisfies this permission.
    """
    import re
    if re.search(r'(?:不能|不可|不应|并非|不是)(?:不|省略|跳过|免除)[^，。；;]*(?:技术|专业|经历|经验)',query):return False
    if re.search(r'(?:技术|专业|经历|经验|擅长)[^，。；;]{0,8}(?:不能|不可|不应|并非|不是)(?:不|省略|跳过|免除)',query):return False
    excluded = (r'(?:不需要|不必|不用|无需|不查|不考虑|不论|不管|不判断|不评估)[^，。；;]{0,12}(?:技术|专业|经历|经验|擅长)'
                r'|(?:技术|专业|历史)?(?:经历|经验|适配|擅长)[^，。；;]{0,8}(?:不用查|不参与|不考虑|不管)')
    return bool(re.search(r'负荷|在办|工作量|期限',query) and re.search(excluded,query))


def constraint_issues(query, request, delivered=None):
    import re
    issues=[]
    for c in request.constraints:
        if c.quote not in query:issues.append('约束摘录不属于原问题：'+c.quote)
        if request.operation not in CONSTRAINT_ACTIONS[c.kind]:issues.append('动作不能完整兑现约束：'+c.quote)
        if c.kind=='manual_diagram' and request.archive_kind!='drawing':issues.append('人工核图约束必须绑定绘图附件')
        if c.kind=='any_completion_state' and request.completed_only:issues.append('不限定交局状态不能增加completed_only筛选')
        if c.kind=='require_technical_history':
            if workload_authorized(query):issues.append('原任务明确排除技术经历；不得把否定经历查询的原话提取为require_technical_history。请保留负荷排序授权，不添加技术经历要求')
            elif request.recommendation_mode!='technical':issues.append('不能以负荷代替用户要求的技术经历')
        if c.kind=='workload_basis':
            # Explicit basis only. A missing history match must never become authorization.
            if not workload_authorized(query):
                issues.append('缺少用户明确按负荷选择且排除技术经历的原话')
            if request.recommendation_mode!='workload_rank' or request.technical_concepts or request.domain or any(r.kind=='candidate_history' for r in request.requirements):
                issues.append('仅按负荷推荐不能混入技术经历或领域权重')
        if c.kind=='unknown_separate' and request.operation=='cases' and selected_due_statuses(request) and 'unknown' not in selected_due_statuses(request):
            issues.append('未知另列必须进入期限分组参数')
        if delivered is not None:
            if c.kind=='show_conflicts' and 'conflicts' not in delivered.get('ledger',delivered).get('fields',[]):issues.append('没有交付来源冲突')
            if c.kind=='unknown_separate' and request.operation=='monthly' and 'unknown_lists' not in delivered.get('fields',[]):issues.append('缺少实际发生日期未知清单')
    return issues


# A finite, lossless syntax normalization for already supported conditions.
# Full-clause matches only; positive deliverables and unsupported suffixes stay.
def canonical_constraint(quote):
    import re
    patterns = {
        'no_recommendation': r'(?:不要|不得|禁止|不|别)(?:再)?(?:给我|给|做|进行|提供)?(?:接案人|接案|人员|人选)?推荐',
        'data_scope': r'(?:说明|注明|明确)(?:这里是否属于测试生成的办理记录|数据是模拟办理信息|模拟数据限制|模拟办理信息(?:的)?(?:范围|限制))|(?:不要|不得)沿用旧的\d+案(?:或\d+案)?口径',
        'cite_evidence': r'给(?:出)?依据|提供(?:来源|依据|引用)|列(?:出)?来源|(?:统计)?(?:出处|来源)(?:也)?(?:留着|保留|标明|注明|带上|给我)',
        'read_only': r'(?:不要|不得|禁止|不|别)(?:再)?(?:(?:修改|写入)(?:案件信息|台账)|保存分案|自动分案|生成文件|生成报告|生成PDF)',
        'no_technical_body': r'(?:不|不用|无需|不需要)(?:看|查|阅读|核对|检索)?技术正文',
        'lexical_only': r'(?:不要|不得|禁止|不)(?:使用|用|检索|查|调用)?向量(?:数据库|检索|搜索)',
        'forbid_db': r'(?:不要|不得|禁止|不)(?:访问|查|查询|使用)?业务(?:数据库|库)',
        'forbid_knowledge': r'(?:不要|不得|禁止|不)(?:访问|查|查询|使用)?知识库',
        'forbid_web': r'(?:不要|不得|禁止|不)(?:再)?(?:联网|上网|网络检索)',
        'workload_basis': r'不(?:需要|用|必)(?:核对|查询|查|考虑)?技术(?:经历|经验)',
        'actual_events': r'(?:能(?:否)?根据(?:三方)?会议(?:时间|日期)或文件(?:更新时间|修改时间)猜(?:一个)?收案日期并计入月报吗[？?]?)|(?:两(?:个|项)(?:实际)?发生日期口径(?:分开|分别统计)|(?:不能|不得)(?:用|以)计划期限替代)|(?:预定|预计|计划)(?:日期|节点|期限|时间|日)(?:不能|不得|不准|不可)(?:补位|补齐|替代实际日期)|(?:给(?:出)?依据[，,]?)?(?:交局(?:只|仅)算(?:已核实|实际确认|已确认|实际核实)的|不(?:用|以|要用)(?:计划|截止|到期)(?:交局)?(?:期限|日期)(?:替代|判断))',
        'any_completion_state': r'不限定(?:有没有|是否)(?:交局|完成|办结)|不限制(?:交局|完成|办理)状态',
        'unknown_separate': r'(?:无|缺少|没有)(?:真实|实际|确认)?日期(?:或依据)?的(?:只)?(?:留|列)(?:为)?待核(?:项|清单)|(?:未知(?:日期)?|日期(?:未知|缺失|不明|不清楚)|未知或冲突|冲突)(?:的)?(?:另列|单列|分别列(?:出|出来)?|分开列(?:出|出来)?)',
        'show_conflicts': r'(?:说明|展示|列出|保留|给出)(?:来源|字段|日期)?冲突(?:及其依据|的依据)?|冲突(?:的)?(?:来源|依据|候选值)(?:也要|请)?(?:展示|说明|列出|保留)',
        'unique_cases': r'按(?:照)?案号(?:去重|计数)|(?:同案|重复)(?:重复)?(?:文件|附件)?(?:不能|不|只)(?:重复)?(?:计数|算一件)|重复(?:附件|文件)去重',
        'actual_attendees': r'不要把(?:现任|当前)?承办人当(?:成|作)参会人',
        'require_technical_history': r'(?:没|未)(?:有)?找到经历也不能只按负荷推荐',
        'manual_diagram': r'(?:图形(?:连接)?关系|连线(?:关系|含义))(?:我|由我|由用户)(?:打开文件)?(?:自行)?(?:核实|核对|确认)',
    }
    quote=quote.strip('。；，,; \n')
    clauses=[c.strip() for c in re.split('[，,；;]',quote)]
    return next((kind for kind,pattern in patterns.items() if re.fullmatch(pattern,quote) or (clauses and all(re.fullmatch(pattern,c) for c in clauses))),None)


def normalize_constraints(intent):
    """Preserve every converted quote and host-owned audit; never mutate raw intent."""
    normalized=intent.model_copy(deep=True)
    changes=[]
    for c in normalized.constraints:
        canonical=canonical_constraint(c.quote)
        if canonical and canonical!=c.kind:
            changes.append({'quote':c.quote,'from':'constraints.'+c.kind,'to':'constraints.'+canonical})
            c.kind=canonical
    requirements=[]
    for r in normalized.requirements:
        kind=canonical_constraint(r.quote)
        if kind:
            if not any(c.kind==kind and c.quote==r.quote for c in normalized.constraints):
                normalized.constraints.append(TaskConstraint(kind=kind,quote=r.quote))
            changes.append({'quote':r.quote,'from':'requirements.'+r.kind,'to':'constraints.'+kind})
        else:
            import re
            clauses=[c.strip('。；，,; ') for c in re.split('[，,；;]',r.quote) if c.strip('。；，,; ')]
            typed=[canonical_constraint(c) for c in clauses]
            if r.kind=='other' and clauses and all(typed):
                for quote,kind in zip(clauses,typed):
                    if not any(c.kind==kind and c.quote==quote for c in normalized.constraints):
                        normalized.constraints.append(TaskConstraint(kind=kind,quote=quote))
                    changes.append({'quote':quote,'from':'requirements.other','to':'constraints.'+kind})
                continue
            if r.kind=='other' and re.fullmatch(r'(?:给出|提供|引用)(?:技术)?正文原句(?:和|及|、)(?:实际|原件记载的)?承办人',r.quote.strip('。；，,; \n')):
                for target in ('case_technology','case_owner'):
                    requirements.append(TaskRequirement(kind=target,quote=r.quote))
                    changes.append({'quote':r.quote,'from':'requirements.other','to':'requirements.'+target})
            else:requirements.append(r)
    normalized.requirements=requirements
    return normalized,changes


SUPPORTED_ACTIONS = {
    'case_list': {'cases'}, 'case_date_unknown': {'cases','monthly'},
    'case_material_gaps': {'case_brief'},
    'case_counts': {'summary'},
    'archive_materials': {'archive'}, 'meeting_attendance': {'meetings','case_brief'},
    'meeting_pending': {'meetings','case_brief'}, 'meeting_content': {'meetings','case_brief'}, 'meeting_changes': {'meetings','case_brief'},
    'monthly_counts': {'monthly'}, 'monthly_comparison': {'monthly'},
    'candidate_recommendation': {'recommend'}, 'candidate_history': {'recommend'},
    'candidate_workload': {'recommend'}, 'deadline_upcoming': {'cases'},
    'deadline_overdue': {'cases'}, 'deadline_unknown': {'cases'}, 'case_owner': {'cases', 'case_brief', 'archive'},
    'case_technology': {'archive', 'case_brief'}, 'case_deadlines': {'cases', 'case_brief'},
    'filing_month': {'cases'}, 'markdown_report':{'monthly'}, 'other': set(),
}


def action_for(requirements, query):
    """Compile supported capabilities; do not ask another classifier for an action."""
    from casework.parser import CASE_RE
    choices = set().union(*SUPPORTED_ACTIONS.values())
    if not requirements:raise ValueError('缺少逐项需求')
    for requirement in requirements:choices &= SUPPORTED_ACTIONS[requirement.kind]
    if not choices:raise ValueError('需求组合没有一个可完整执行的固定动作')
    kinds={r.kind for r in requirements}
    if kinds & {'meeting_attendance','meeting_pending','meeting_content','meeting_changes'}:
        if CASE_RE.search(query) and 'case_brief' in choices:return 'case_brief'
        choices.discard('case_brief')
        if not choices:raise ValueError('单案会议要求需明确案号，不能丢失其他要求')
    if kinds & {'candidate_history','candidate_workload'} and 'candidate_recommendation' not in kinds:
        raise ValueError('候选经历与负荷只支持作为辅助分案的子要求，独立自定义统计交完整流程')
    if len(choices) == 1:return choices.pop()
    if CASE_RE.search(query) and 'case_technology' in kinds and kinds & {'case_owner','case_deadlines'}:
        return 'case_brief'
    if 'cases' in choices:return 'cases'
    if 'archive' in choices:return 'archive'
    if 'meetings' in choices:return 'meetings'
    return 'cases'


def selected_due_statuses(request):
    return set(request.due_statuses or ([request.due_status] if request.due_status else []))



def plan_requirement_steps(request):
    """One display item per typed requirement, even when quotes are identical."""
    labels = {
        'case_list':'查询筛选范围内的案件清单', 'case_date_unknown':'查询实际发生日期未核实的案件',
        'case_counts':'统计完整发布快照中的案件总量、在办和已核实实际交局量',
        'archive_materials':'查找历史参考材料', 'meeting_attendance':'查询实际参会人员',
        'meeting_pending':'查询会议登记待补材料','meeting_content':'查询会议内容','meeting_changes':'查询会议修改意见',
        'monthly_counts':'统计各月收案与交局数量', 'monthly_comparison':'比较各月变化',
        'candidate_recommendation':'提供接案候选与依据', 'candidate_history':'核对候选人的相关办案经历',
        'candidate_workload':'查询候选人的在办案件与期限', 'case_owner':'查询案件负责人',
        'deadline_unknown':'查询期限日期未知的在办案件', 'case_technology':'查找案件技术原文', 'case_deadlines':'查询案件期限',
        'filing_month':'核对实际交局月份', 'markdown_report':'保存Markdown月报文件', 'other':'其他要求',
    }
    node = {'draft_due_date':'初稿', 'client_review_due_date':'客户送审',
            'filing_due_date':'向局提交'}.get(request.due_field, '指定期限')
    steps = []
    for requirement in request.requirements:
        if requirement.kind == 'deadline_upcoming':
            steps.append(f'查询近期到期的{node}案件（基准日起14天内）')
        elif requirement.kind == 'deadline_overdue':
            steps.append(f'查询已经逾期的{node}案件（期限早于基准日）')
        else:
            steps.append(labels.get(requirement.kind, '需求')+'：'+requirement.quote)
    return steps


def due_group(value, as_of):
    from datetime import timedelta
    from casework.analytics import as_date
    if not value:return 'unknown'
    if value < as_of:return 'overdue'
    if value < (as_date(as_of)+timedelta(days=14)).isoformat():return 'next14'
    return 'later'


def requirement_issues(query, request, delivered=None):
    """Check operation capability and actual delivery separately from fact values.

    An independent reviewer still compares the whole original question: this
    checklist cannot prove that its extractor found every clause.
    """
    issues = []
    for requirement in request.requirements:
        kind, quote = requirement.kind, requirement.quote
        if quote not in query:
            issues.append('需求摘录不属于原问题：' + quote)
            continue
        if request.operation not in SUPPORTED_ACTIONS[kind]:
            issues.append('只读动作没有覆盖要求：' + quote)
            continue
        if kind == 'monthly_comparison' and not request.compare_months:
            issues.append('月份比较没有进入执行参数：' + quote)
        if kind in ('deadline_upcoming', 'deadline_overdue', 'deadline_unknown'):
            status = {'deadline_upcoming':'next14','deadline_overdue':'overdue','deadline_unknown':'unknown'}[kind]
            if status not in selected_due_statuses(request):
                issues.append('期限条件没有进入执行参数：' + quote)
        if kind == 'filing_month' and not request.filing_month:
            issues.append('实际交局归月要求没有进入执行参数：' + quote)
        if kind == 'case_date_unknown' and not request.missing_date_field:
            issues.append('实际日期未知筛选没有进入执行参数：' + quote)
        if delivered is not None:
            if kind=='markdown_report' and not delivered.get('markdown_report'):issues.append('Markdown文件未实际交付：'+quote)
            if kind == 'case_date_unknown' and delivered.get('missing_date_field') != request.missing_date_field:
                issues.append('实际日期未知筛选没有进入交付：' + quote)
            meeting_field={'meeting_pending':'pending','meeting_content':'content','meeting_changes':'changes'}.get(kind)
            if meeting_field and meeting_field not in delivered.get('fields',[]):
                issues.append('结果没有会议登记字段：'+quote)
            if kind == 'candidate_history' and 'historical_owner_evidence' not in delivered.get('fields',[]):
                issues.append('结果没有候选历史经历或未核实说明：' + quote)
            if kind == 'candidate_workload' and 'three_due_dates_and_stage' not in delivered.get('fields',[]):
                issues.append('结果没有候选在办及三节点负荷：' + quote)
            if kind == 'monthly_comparison' and not delivered.get('comparisons'):
                issues.append('结果缺少月份比较：' + quote)
            if kind in ('deadline_upcoming', 'deadline_overdue', 'deadline_unknown'):
                status = {'deadline_upcoming':'next14','deadline_overdue':'overdue','deadline_unknown':'unknown'}[kind]
                if status not in delivered.get('due_groups', {}):
                    issues.append('结果缺少期限分组（空结果也须说明）：' + quote)
            if kind == 'case_material_gaps' and not delivered.get('registered_material_gaps'):
                issues.append('结果没有本案已登记待补材料或未登记说明：'+quote)
            if kind == 'case_technology':
                section = delivered.get('archive', delivered)
                if 'technical_excerpts' not in section:
                    issues.append('结果没有技术正文或未找到的说明：' + quote)
            if kind == 'case_deadlines':
                section = delivered.get('ledger', delivered)
                if not section.get('deadline_context'):
                    issues.append('结果没有按基准日解释计划节点：' + quote)
    return issues


def external_comparison_requested(query):
    """A public comparison cannot be reduced to an internal excerpt/citation."""
    import re
    from casework.routing import affirmative
    text=affirmative(query)
    return bool(re.search(r'外面|外部|公开|互联网|网上',text)
                and re.search(r'对比|比较|区别|差(?:在|哪)|不同|有什么特别|特别之处',text))


def case_mechanism_judgment_requested(query):
    """A specified case's adoption question requires a reasoned conclusion."""
    import re
    from casework.parser import CASE_RE
    return bool(CASE_RE.search(query) and re.search(
        r'(?:到底|究竟).{0,12}(?:用了|采用|使用)|(?:有没有|是否|能否|能不能).{0,8}(?:用|采用|使用)|(?:反例|否定句).{0,20}(?:证明|说明)',query))
