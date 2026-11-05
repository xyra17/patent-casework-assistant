// Size to the current page's typical content, not its longest exceptional value.
export function businessColumnWidths(columns = [], rows = [], labelFor = name => name, measure = text => text.length * 14) {
  return Object.fromEntries(columns.map((name, index) => {
    const widths = rows.map(row => {
      const value = row[index]
      const text = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)
      return measure(text.slice(0, 2048))
    }).sort((a, b) => a - b)
    const typical = widths[Math.max(0, Math.ceil(widths.length * .85) - 1)] || 0
    const header = measure(String(labelFor(name)))
    return [name, Math.ceil(Math.min(320, Math.max(76, header + 28, typical + 28))) ]
  }))
}
