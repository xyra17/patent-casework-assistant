export const REFERENCE_PREVIEW_COUNT = 3

// Render segments as Vue text nodes. Source text never becomes HTML.
export function highlightSegments(text = '', terms = []) {
  const original = String(text)
  const needles = [...new Set(terms.filter(t => typeof t === 'string' && t.length > 1))]
    .sort((a, b) => b.length - a.length)
  const parts = []
  let cursor = 0
  while (cursor < original.length) {
    let next = -1, needle = ''
    for (const term of needles) {
      const at = original.toLowerCase().indexOf(term.toLowerCase(), cursor)
      if (at >= 0 && (next < 0 || at < next)) { next = at; needle = term }
    }
    if (next < 0) { parts.push({ text: original.slice(cursor), matched: false }); break }
    if (next > cursor) parts.push({ text: original.slice(cursor, next), matched: false })
    parts.push({ text: original.slice(next, next + needle.length), matched: true })
    cursor = next + needle.length
  }
  return parts
}

export function archiveReferenceList(result, rows = result?.results || []) {
  const lines = [`技术资料参考清单：${result?.query || ''}`, `本清单含 ${rows.length} 件，仅列本批检索结果；不代表已找齐所有案件。`]
  if (result?.filters?.owner) lines.push(`代理师条件：${result.filters.owner}`)
  if (result?.filters?.domain) lines.push(`技术领域条件：${result.filters.domain}`)
  for (const row of rows) {
    const source = row.sources?.[0]
    lines.push('', `${row.case_id} · ${row.title || '名称未登记'}`, `代理师：${row.owner_name || '未登记'}`)
    if (source) lines.push(`原文：${source.excerpt}`, `出处：${source.source || source.path} · ${source.location}`, `内容版本：${source.revision}`)
    for (const drawing of row.related_drawings || []) lines.push(`绘图附件：${drawing.source || drawing.path}`, `关联依据：按所编号关联；${drawing.scope}`, `内容版本：${drawing.revision}`)
  }
  return lines.join('\n')
}
