export function evidenceScopeText(item) {
  const c = item.completeness
  if (!c) return item.type === 'database' ? '该来源未记录结果范围；不能据此判断全量。' : ''
  const unit = c.scope === 'semantic_groups' ? '组' : '行'
  const parts = [`已取回 ${c.returned_rows ?? item.rowCount ?? item.rows?.length ?? 0} ${unit}`]
  if (c.query_rows != null) parts.push(`本次查询范围 ${c.query_rows} ${unit}`)
  if (c.sql_limited) parts.push('SQL 含 LIMIT')
  parts.push(c.has_more === true ? '还有未返回结果' : c.has_more === false ? '本次取数已结束' : '取数是否完整未记录')
  parts.push(c.matched_rows == null ? '基础记录总数未知' : `匹配基础记录 ${c.matched_rows} 条`)
  parts.push(`来源保存 ${c.evidence_rows ?? item.rows?.length ?? 0} ${unit}`)
  if (c.evidence_truncated || c.evidence_cells_truncated) parts.push('来源仅保存部分输出')
  if (c.model_rows != null) parts.push(`模型展示 ${c.model_rows} ${unit}${c.model_truncated || c.model_cells_truncated ? '（已截断）' : ''}`)
  return parts.join(' · ')
}
