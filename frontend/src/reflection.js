const SOURCE_NAMES = {db_query: '数据库', knowledge: '知识库', web_search: '网络检索'}
const sourceNames = (values) => values.map(value => SOURCE_NAMES[value] || value).join('、')
export const CHECK_STATES = {
  passed: '通过', failed: '未通过', skipped: '审查未完成', not_run: '未执行',
  unknown: '未记录', unverified: '未核验', not_required: '无需检查',
  awaiting_input:'待你确认',
}
const UNVERIFIED_REASONS = {
  missing_evidence: '证据不足', ambiguous_requirement: '条件待确认',
  verification_unavailable: '核验未完成', other: '其他原因未核验',
}
const OUTCOMES = {pending:'待处理', unverified:'尚未复核', corrected:'已修正并复核通过',
  evidence_added:'证据已更新，复核通过', rechecked:'已复核通过'}
const STOP_OUTCOMES = {max_rounds:'轮次预算耗尽，仍未解决', execution_budget:'执行预算耗尽，仍未解决',
  no_progress:'无进展，停止时仍未解决', review_unavailable:'审查未完成，仍待核验',
  workflow_review_failed:'停止重复查询，仍未解决', passed:'未记录该问题的解决依据',
  timeout:'任务时间预算耗尽，仍未解决', user_cancel:'任务已终止，仍未解决',
  execution_error:'执行异常中断，仍未解决'}

export function issueLifecycleDetails(items) {
  return (Array.isArray(items) ? items : []).map(item => ({
    ...item,
    resolved: item.status === 'resolved' && !item.returned_report_unverified,
    outcomeText: item.status === 'resolved' ? (item.returned_report_unverified ? '后续复核通过；返回草稿仍待核对'
      : OUTCOMES[item.history?.at(-1)?.outcome] || '已复核通过')
      : STOP_OUTCOMES[item.stop_reason] || '待处理／待复核',
    history: (item.history || []).map(event => ({...event, outcomeText:OUTCOMES[event.outcome] || '未记录',
      evidenceIds: Array.isArray(event.evidence_ids) ? event.evidence_ids : []})),
  }))
}

export function reflectionDetails(record, finalSummary = null) {
  if (!record) return null
  const issues = Array.isArray(record.hard_issues) ? record.hard_issues : null
  const required = record.required_sources
  const called = record.called_sources
  const missing = (required || []).filter(key => !(called || []).includes(key))
  const allowed = record.allowed_sources
  const unexpected = Array.isArray(allowed) ? (called || []).filter(key => !allowed.includes(key)) : []
  const softState = record.soft_review_status || (record.soft_issue ? 'failed' : issues?.length ? 'not_run' : 'unknown')
  const check = (label, state, detail) => ({label, state, status: CHECK_STATES[state] || CHECK_STATES.unknown, detail})
  return {
    issueLifecycle: issueLifecycleDetails(finalSummary?.issue_lifecycle ?? record.issue_lifecycle),
    verification: record.verification || null,
    verificationChecks: (Array.isArray(record.verification?.checks) ? record.verification.checks : []).map(item => ({
      ...item,
      state: Object.hasOwn(CHECK_STATES, item.status) ? item.status : 'unknown',
      statusText: item.status === 'unverified' ? UNVERIFIED_REASONS[item.reason] || '未核验（原因未分类）'
        : ({passed:'已通过', failed:'需修正'}[item.status] || CHECK_STATES[item.status] || CHECK_STATES.unknown),
      evidenceIds: Array.isArray(item.evidence_ids) ? item.evidence_ids : [],
      pending: ['failed', 'unverified'].includes(item.status),
    })),
    gaps: (record.evidence_gaps || []).filter(gap => gap.missing),
    hardState: issues ? (issues.length ? 'failed' : 'passed') : 'unknown',
    softState,
    issues: [...(issues || []), ...(record.soft_issue ? [record.soft_issue] : [])],
    items: [
      check('占位符', issues ? (issues.some(issue => issue.includes('占位符')) ? 'failed' : 'passed') : 'unknown',
        issues?.find(issue => issue.includes('占位符')) || (issues ? '未发现规则内的未填充占位符。' : '未记录规则检查结果。')),
      check('PDF 产物', record.require_pdf === false ? 'not_required' : record.require_pdf === true && typeof record.pdf_produced === 'boolean' ? (record.pdf_produced ? 'passed' : 'failed') : 'unknown',
        record.require_pdf === false ? '当前任务不要求 PDF。' : typeof record.pdf_produced === 'boolean' ? (record.pdf_produced ? '已生成要求的 PDF 文件。' : '未生成要求的 PDF 文件。') : '未记录 PDF 产出结果。'),
      check('数据源覆盖', Array.isArray(required) && Array.isArray(called) ? (missing.length || unexpected.length ? 'failed' : required.length ? 'passed' : 'not_required') : 'unknown',
        `要求：${Array.isArray(required) ? sourceNames(required) || '无指定来源' : '未记录'}；已调用：${Array.isArray(called) ? sourceNames(called) || '无' : '未记录'}${missing.length ? `；缺少：${sourceNames(missing)}` : ''}${unexpected.length ? `；超出本轮开放范围：${sourceNames(unexpected)}` : ''}。`),
      ...(record.verification ? [check('引用编号', issues ? (issues.some(issue => issue.includes('不存在的来源编号')) ? 'failed' : 'passed') : 'unknown',
        issues?.find(issue => issue.includes('不存在的来源编号')) || '核对正文中的 [S编号] 是否实际存在；编号存在不代表原文支持结论。')] : []),
      check('关键统计核验', record.statistics_check?.status || 'unknown',
        record.statistics_check ? `${record.statistics_check.checked_claims || 0} 项已核对。${(record.statistics_check.issues || []).join('；') || record.statistics_check.detail || ''}` : '历史记录未执行关键统计核验。'),
      ...(record.business_check ? [check('业务事实与引用', record.business_check.status || 'unknown',
        `${record.business_check.checked_claims || 0} 项字段或原文绑定。${(record.business_check.issues || []).join('；') || record.business_check.scope || ''}`)] : []),
      check(record.soft_review_mode === 'program_contract' ? '任务条件核对（程序）' : '内容审查', softState,
        record.soft_issue || record.soft_review_detail || ({passed: '内容审查已返回通过。', skipped: '审查服务未完成，结果仍待核验。', not_run: '规则校验未通过，尚未执行内容审查。', unknown: '历史记录未区分通过与跳过，无法确认内容审查结果。'}[softState]) || '未记录内容审查结果。'),
    ],
  }
}


export function qualitySummaryText(summary) {
  if (!summary) return ''
  const reasons = {
    passed: '质量校验通过，已结束',
    awaiting_input:'关键证据不足，已补查并暂停，等待你确认下一步',
    max_rounds: '已达到本次轮次上限，返回未达标的部分结果',
    no_progress: '连续三轮回答、问题与证据未变化，已提前停止',
    review_unavailable: '内容审查服务不可用，结果尚未完成审查',
    workflow_review_failed: '固定查询仍有质量问题，已保留待核对项并停止重复查询',
    execution_budget: '上下文回读预算已耗尽，任务未完成，已保留来源和未达标项',
    timeout: '任务时间预算已耗尽，保留已完成的逐项校验记录',
    user_cancel: '任务已终止，未解决问题仍保留',
    execution_error: '执行异常中断，未解决问题仍保留',
  }
  const returned = summary.returned_round && summary.returned_round !== summary.rounds_used
    ? `最终保留第 ${summary.returned_round} 轮草稿；下方记录反映各轮处理过程，不代表该草稿已通过。` : ''
  return `${reasons[summary.stop_reason] || '质量处理已停止'}（${summary.rounds_used} / 最多 ${summary.max_rounds} 轮）。${returned}`
}
