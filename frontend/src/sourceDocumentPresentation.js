// Only format complete label/value lines. Other source text stays unchanged.
export function sourceFieldRows(text = '') {
  const lines = String(text).split(/\r?\n/).filter(line => line.trim())
  if (lines.length < 2) return null
  const rows = []
  for (const line of lines) {
    const match = line.match(/^([^|\n]{1,40}?)\s+\|\s+(.*)$/)
    if (!match || !match[1].trim()) return null
    rows.push({ label: match[1].trim(), value: match[2] })
  }
  return rows
}

export function sourceTime(value) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
