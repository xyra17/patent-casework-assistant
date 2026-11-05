// Display summaries only; the original source and locator remain available in details.
export function sourceName(source = {}) {
  const path = source.source || source.url || ''
  if (/^https?:\/\//i.test(path)) {
    try { return new URL(path).hostname } catch { return path }
  }
  return path.replace(/\\/g, '/').split('/').filter(Boolean).pop() || ''
}

export function sourceLocationSummary(location = '') {
  const parts = (location || '').split('；').map(part => part.trim()).filter(Boolean)
  const cells = parts.map(part => part.match(/^工作表 (.+?) · 第(\d+)行 · 第(\d+)列$/))
  if (cells.length && cells.every(cell => cell && cell[1] === cells[0][1] && cell[2] === cells[0][2])) {
    const columns = [...new Set(cells.map(cell => cell[3]))]
    return `${cells[0][1]} · 第${cells[0][2]}行 · 第${columns.join('、')}列`
  }
  const fields = {
    '/case_fields/actual_filed_date': '实际交局日期',
    '/case_fields/filing_confirmed': '交局核实状态',
  }
  return parts.map(part => {
    const json = part.match(/^JSON (\/\S+)$/)
    return json ? fields[json[1]] || `结构化字段 ${json[1]}` : part
  }).join('；').replace(/^正文 · 正文块 /, '正文块 ')
}

// Compatibility for platform labels saved in earlier executions; source excerpts stay unchanged.
export function caseFieldLabel(label) {
  return label === '办理阶段' ? '案件状态' : label
}

export function evidenceTitle(source = {}) {
  const title = source.title || ''
  if (source.case_calculation && title === '案卷技术与台账关联（程序计算）') return '案卷技术与案件信息关联（程序计算）'
  if (source.type !== 'database') return title
  if (['部门案件信息（当前发布视图）', '部门案件台账（当前发布视图）'].includes(title)) return '部门案件信息（当前已同步数据）'
  return title.replace(/^历史(?:案件信息|台账)承办与完成状态(?= · )/, '历史案件承办与完成状态')
}
