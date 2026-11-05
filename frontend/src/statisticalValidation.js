export function needsStatisticsValidation(session) {
  const result = session?.query_result
  if (!session?.degraded && result?.mode === 'verified_business' && result.status === 'ready' &&
      result.business_validation_version === 1 && result.business_check?.status === 'passed' &&
      result.content && result.content === session.result) return false
  return Boolean(session?.query_result && session.query_result.statistical_validation_version !== 1 &&
    session.evidence?.some(item => item.type === 'database'))
}
export function statisticsNotice(result) {
  const check = result?.statistics_check
  return check && ['failed', 'unverified'].includes(check.status)
    ? `统计核验未通过：${check.issues?.join('；') || check.detail || '请查看完整回答及来源。'}` : ''
}
