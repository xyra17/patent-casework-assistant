// Explain each saved requirement by kind; never merge different conditions by quote.
export function planDisplaySteps(plan = {}) {
  const steps = plan.steps || []
  const request = plan.department_request
  if (!request?.requirements?.length || !steps[0]?.startsWith('需求：')) return steps
  const labels = {
    archive_materials:'查找历史参考材料', meeting_attendance:'查询实际参会人员',
    monthly_counts:'统计各月收案与交局数量', monthly_comparison:'比较各月变化',
    candidate_recommendation:'提供接案候选与依据', candidate_history:'核对候选人的相关办案经历',
    candidate_workload:'查询候选人的在办案件与期限', case_owner:'查询案件负责人',
    case_technology:'查找案件技术原文', case_deadlines:'查询案件期限',
    filing_month:'核对实际交局月份', other:'其他要求',
  }
  const node = {draft_due_date:'初稿', client_review_due_date:'客户送审', filing_due_date:'向局提交'}[request.due_field] || '指定期限'
  const requirements = request.requirements.map(item => {
    if (item.kind === 'deadline_upcoming') return `查询近期到期的${node}案件（基准日起14天内）`
    if (item.kind === 'deadline_overdue') return `查询已经逾期的${node}案件（期限早于基准日）`
    return `${labels[item.kind] || '需求'}：${item.quote}`
  })
  const prefixLength = steps.findIndex(step => !step.startsWith('需求：'))
  return [...requirements, ...steps.slice(prefixLength < 0 ? steps.length : prefixLength)]
}
