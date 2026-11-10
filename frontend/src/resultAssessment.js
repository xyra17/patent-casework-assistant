// No content verdict may be inferred from completed/degraded alone.
export function resultAssessmentItems(session) {
  const assessment = session?.result_assessment
  const state = (axis) => axis?.status || 'unverified'
  const contentLabel = (axis, failure) => ({
    passed: '内部核对通过', failed: failure, unverified: '待核验',
  }[state(axis)] || '待核验')
  const type = (axis) => ({passed: 'success', failed: 'danger', released: 'success',
    partial: 'warning'}[state(axis)] || 'info')
  return [
    {key: 'facts', label: '事实正确性', status: contentLabel(assessment?.facts, '内部审查发现错误'),
      type: type(assessment?.facts), detail: assessment?.facts?.detail || '没有对应正文的分项审查，不能由运行完成推定正确。'},
    {key: 'completeness', label: '任务完整性', status: contentLabel(assessment?.completeness, '内部审查发现缺项'),
      type: type(assessment?.completeness), detail: assessment?.completeness?.detail || '尚未确认是否答全用户要求。'},
    {key: 'delivery', label: '交付状态', status: assessment?.delivery?.label || '状态未记录',
      type: type(assessment?.delivery), detail: assessment?.delivery?.detail || '交付状态不等同于内容正确或任务完整。'},
  ]
}

// Business state is supplied by the host contract, never inferred from answer
// wording, task completion, or the presence of “人工确认”.
export function productBoundaryView(session) {
  const assessment = session?.result_assessment
  const boundary = assessment?.product_boundary
  if (!boundary || boundary.report_sha256 !== assessment.report_sha256) return null
  const statuses = {
    VERIFIED: ['已核验', 'success'],
    REVIEW_REQUIRED: ['待人工确认', 'warning'],
    INCOMPLETE: ['未完成', 'warning'],
    BLOCKED: ['已拦截', 'error'],
  }
  const [label, type] = statuses[boundary.status] || ['未完成', 'warning']
  return {...boundary, label, type,
    candidateLabel: boundary.status === 'VERIFIED' ? '业务说明（内部核验）'
      : boundary.status === 'REVIEW_REQUIRED' ? 'Agent初步分析（专业判断待人工确认）'
      : 'Agent候选及执行说明（未作为已核验终稿）',
    comparisonRows: boundary.objective_comparison?.records || [],
    comparisonVerified: boundary.objective_status === 'VERIFIED'
      && boundary.objective_comparison?.status === 'VERIFIED',
  }
}
