export const SOURCE_OPTIONS = [
  {id:'db_query', label:'业务数据库', detail:'查询结构化业务记录与统计'},
  {id:'knowledge', label:'知识库', detail:'检索本地案件档案与公司文档'},
  {id:'web_search', label:'联网检索', detail:'允许将必要的检索词发送到外部搜索服务'},
]
export const DEFAULT_SOURCES = ['db_query','knowledge']
const known = SOURCE_OPTIONS.map(item=>item.id)
export function normalizeSources(values) {
  return Array.isArray(values) ? known.filter(id=>values.includes(id)) : [...DEFAULT_SOURCES]
}
export function readSourcePreference(storage) {
  try {
    const value = JSON.parse(storage.getItem('tanyuan-internal-sources'))
    if(!Array.isArray(value) || value.some(id=>!known.includes(id)))return [...DEFAULT_SOURCES]
    return normalizeSources(value).filter(id=>id!=='web_search')
  } catch { return [...DEFAULT_SOURCES] }
}
export function saveSourcePreference(storage, values) {
  try { storage.setItem('tanyuan-internal-sources',JSON.stringify(normalizeSources(values).filter(id=>id!=='web_search'))) }
  catch { /* Browser storage may be disabled; current selection still works. */ }
}
export function sourceSummary(values) {
  const sources=normalizeSources(values)
  if(!sources.length)return '仅当前材料'
  const internal=sources.filter(id=>id!=='web_search')
  const names=internal.length===2 ? '内部来源' : internal.length ? SOURCE_OPTIONS.find(item=>item.id===internal[0]).label : ''
  return sources.includes('web_search') ? `${names ? names+' + ' : ''}联网` : `${names} · 联网关`
}
