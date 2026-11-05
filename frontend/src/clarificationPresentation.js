// Existing waiting questions keep their saved IDs and choices; only their display copy changes.
export function clarificationPresentation(question = {}) {
  if (question.kind !== 'recommendation_evidence') return question
  const count = Array.isArray(question.lookup_case_ids) ? question.lookup_case_ids.length : null
  const detail = count === null
    ? '已搜索系统里的案件文档，目前的资料还不足以判断哪位同事办过这类案件。'
    : count > 0
      ? `搜索了系统里的案件文档，找到${count}个可能相关的案件，但这些资料还不足以判断哪位同事办过这类案件。`
      : '搜索了系统里的案件文档，没找到明确记载“哪位同事办过这类案件”的材料。'
  return {
    ...question,
    title: '还不能确定谁有相关经验',
    message: '目前还不能确认谁办理过这类技术的案件，因此暂时没有给出接案推荐。',
    detail,
    technical_query: question.technical_query || question.request?.query || '',
  }
}

export function clarificationChoiceText(response = {}) {
  const labels = {
    workload_only: '仅查看人员负荷，暂不判断技术适配，也不作接案推荐。',
    scope_mention: '查询提及该概念的案件，包含背景、对比和否定提及。',
    scope_topic: '查询以该概念为核心主题的案件。',
    scope_mechanism: '查询方案中采用该技术机制的案件。',
  }
  return labels[response.choice] || response.text || ''
}
