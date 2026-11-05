const TABLE_NAMES = {
  cw_documents: '案件文件',
  cw_sync_state: '同步状态',
  cw_tags: '案件标签',
}

export function tablePresentation(name, table = {}) {
  const label = (table.label && table.label !== name ? table.label : TABLE_NAMES[name]) || name
  const match = label.match(/^(.*?)\s*[（(](只读同步|可导入维护)[）)]$/)
  return {
    label: match ? match[1].trim() : label,
    note: match ? { 只读同步: '只读', 可导入维护: '可维护' }[match[2]] : '',
  }
}
