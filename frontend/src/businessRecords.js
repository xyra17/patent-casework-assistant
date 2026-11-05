export const BUSINESS_RECORDS = [
  {table:'cw_cases',label:'案件信息',description:'客户、代理师、阶段与办理日期'},
  {table:'cw_meetings',label:'会议记录',description:'实际参会人、沟通内容与待补材料'},
  {table:'cw_documents',label:'文件目录',description:'所号关联的文件、版本与解析状态'},
  {table:'cw_tags',label:'技术标签',description:'检索关联标签及其来源'},
]
const technical=new Set(['case_key','root_id','generation','source_refs','snapshot_hash','parser_key','revision','document_id','meeting_id','tag_id'])
export function recordColumns(table, columns, showTechnical=false) {
  if(showTechnical || !table?.startsWith('cw_'))return columns || []
  return (columns || []).filter(name=>!technical.has(name) || (table==='cw_tags' && name==='case_key'))
}
export function recordLabel(table, fallback) {return BUSINESS_RECORDS.find(item=>item.table===table)?.label || fallback || table}
export function defaultRecordTable(tables) {return tables.some(t=>t.name==='cw_cases')?'cw_cases':tables[0]?.name || ''}

export function recordValue(table, column, value, showTechnical=false) {
  if (!table?.startsWith('cw_') || showTechnical) return value
  if (value === null || value === undefined || value === '') return '—'
  if (column === 'case_key' && table === 'cw_tags') return String(value).split(':').slice(1).join(':') || value
  if (column === 'filing_verified') return value === true || value === 1 || value === '1' ? '已核实' : value === false || value === 0 || value === '0' ? '未核实' : value
  if (column === 'method') return ({local_dictionary_v1:'按技术关键词匹配',keyword:'按关键词匹配',keywords:'按关键词匹配',semantic:'按内容相似度匹配',manual:'人工标注',llm:'模型分析',hybrid:'关键词与内容相似度匹配'})[value] || '其他方式（技术详情中查看）'
  if (column === 'kind') return ({intake:'立案资料',ledger:'案件信息表',disclosure:'技术交底',drawing:'附图',meeting:'沟通记录',application:'申请文件',receipt:'办理回执',filing:'提交文件',fee:'费用文件',other:'其他资料'})[value] || value
  if (column === 'state') return ({parsed:'已解析',inventory:'仅登记附件',error:'解析失败',failed:'解析失败',unsupported:'暂不支持解析'})[value] || value
  if (['iprs','agents','inventors','conflict_fields'].includes(column)) {
    try { const items=typeof value==='string'?JSON.parse(value):value; if(Array.isArray(items)) return items.length?items.join('、'):(column==='conflict_fields'?'无':'—') } catch {}
  }
  return value
}
