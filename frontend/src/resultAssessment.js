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
