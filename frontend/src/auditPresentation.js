export const AUDIT_ACTIONS = [
  ['scan_publish', '发布同步结果'], ['field_confirmation', '确认案件字段'],
  ['clear_confirmation', '撤销字段确认'], ['assignment_confirmed', '确认分案'],
  ['root_create', '接入共享目录'], ['root_update', '修改目录配置'],
  ['person_create', '新增代理师'], ['person_update', '修改代理师配置'],
  ['purge_catalog', '移除应用索引'],
].map(([value, label]) => ({value, label}))

export function auditAction(action) {
  return AUDIT_ACTIONS.find(item => item.value === action)?.label || action || '操作未登记'
}

export function auditObject(row, roots = [], people = []) {
  if (['scan_publish', 'root_create', 'root_update', 'purge_catalog'].includes(row.action)) {
    return roots.find(item => item.id === row.target)?.label || row.after?.label || row.before?.label || row.target
  }
  if (['person_create', 'person_update'].includes(row.action)) {
    return row.after?.name || row.before?.name || people.find(item => item.id === row.target)?.name || row.target
  }
  return row.target?.split(':').slice(1).join(':') || row.target || '对象未登记'
}

export function auditSummary(row, labels = {}) {
  const after = row.after || {}
  const fieldLabel = key => labels[key] || key
  if (row.action === 'scan_publish') {
    return `发现 ${after.seen ?? '—'} 个文件 · 解析 ${after.parsed ?? '—'} · 未变 ${after.unchanged ?? '—'} · 失败 ${after.failed ?? '—'}`
  }
  if (row.action === 'assignment_confirmed') return `${row.before?.owner_name || '未分案'} → ${after.owner_name || '未登记'}`
  if (row.action === 'field_confirmation') return `核对：${Object.keys(after).map(fieldLabel).join('、') || '字段未登记'}`
  if (row.action === 'clear_confirmation') return `撤销：${fieldLabel(after.field || '字段未登记')}`
  if (row.action === 'purge_catalog') return `移除 ${row.before?.files ?? '—'} 个文件的应用索引，源盘原件保留`
  if (row.action === 'root_create') return after.classification === 'synthetic' ? '接入独立模拟资料目录' : '接入仅限本地的资料目录'
  if (row.action === 'root_update') return `更新目录配置${after.enabled === false ? ' · 已停用' : after.enabled === true ? ' · 已启用' : ''}`
  if (['person_create', 'person_update'].includes(row.action)) return `${after.domains?.join('、') || '领域未登记'}${after.enabled === false ? ' · 已停用' : ''}`
  return row.reason || '查看详情核对操作记录'
}

const FIELD_LABELS = {generation:'发布版本', owner_name:'代理师', person_id:'代理师标识',
  label:'目录名称', path:'目录路径', classification:'资料分类', mapping:'字段映射',
  enabled:'启用状态', reattach:'重新确认目录身份', name:'姓名', domains:'绑定领域',
  seen:'发现文件', parsed:'解析文件', unchanged:'未变文件', failed:'失败文件',
  removed:'移出索引', renamed:'疑似移动', inventory:'仅登记文件',
  pending_removal_count:'待清理文件', pending_removals:'待清理文件清单',
  full_traversal:'完整扫描', embedding:'索引处理说明', notes:'备注'}

export function auditValue(value) {
  if (value == null || value === '') return '未登记'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.length ? value.map(item => typeof item === 'object' ? JSON.stringify(item) : String(item)).join('、') : '无'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

export function auditChanges(row, labels = {}) {
  // The stored "after" is often a patch, not a full snapshot. Omitted keys were not deleted.
  // A revoked confirmation stores the field name as a marker, not a new field value.
  if (!row.after || row.action === 'clear_confirmation') return []
  return Object.keys(row.after).map(key => ({key, label: labels[key] || FIELD_LABELS[key] || key,
    before: auditValue(row.before?.[key]), after: auditValue(row.after[key]),
    beforeRecorded: Object.hasOwn(row.before || {}, key),
  }))
}
