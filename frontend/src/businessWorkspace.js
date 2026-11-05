// Navigation preferences belong to this browser tab; no records, SQL or query results are persisted.
const KEY='tanyuan-business-workspace-v1'
export function readWorkspace(section){try{return JSON.parse(sessionStorage.getItem(KEY)||'{}')[section] || null}catch{return null}}
export function saveWorkspace(section,value){try{const all=JSON.parse(sessionStorage.getItem(KEY)||'{}');sessionStorage.setItem(KEY,JSON.stringify({...all,[section]:value}))}catch{/* Storage may be disabled. Navigation still works. */}}
export function restoreBrowse(saved,tables){
  const table=tables.find(t=>t.name===saved?.table)
  if(!table)return null
  const ops=['eq','contains','gt','gte','lt','lte'],fields=new Set(table.columns.map(c=>c.name))
  const validFilter=f=>f && fields.has(f.field) && ops.includes(f.op) && typeof f.value==='string' && f.value.length<=500
  const pageSize=[20,50,100].includes(saved.pageSize)?saved.pageSize:50
  const filters=Array.isArray(saved.filters)?saved.filters.filter(validFilter).slice(0,8):[]
  const removed=(saved.filters?.length || 0)!==filters.length
  const page=Number.isInteger(saved.page)&&saved.page>0&&saved.page<=10000&&(saved.page-1)*pageSize<=200000&&!removed?saved.page:1
  return {table:table.name,field:fields.has(saved.field)?saved.field:'',op:ops.includes(saved.op)?saved.op:'eq',value:typeof saved.value==='string'?saved.value.slice(0,500):'',pageSize,page,filters,removed}
}
export function mergeStructure(semantic,physical){
  const tables={}
  for(const [name,cfg] of Object.entries(semantic || {}))tables[name]={...cfg,exists:physical?false:null,columns:Object.fromEntries(Object.entries(cfg.columns || {}).map(([n,c])=>[n,{...c,exists:physical?false:null,configured:undefined}]))}
  for(const table of physical?.tables || []){
    const cfg=semantic?.[table.name] || {label:table.label,columns:{}}
    const columns={...tables[table.name]?.columns}
    for(const column of table.columns)columns[column.name]={...cfg.columns?.[column.name],label:cfg.columns?.[column.name]?.label || column.label || column.name,physical:column,exists:true,configured:column.semantic_configured===true}
    tables[table.name]={...cfg,exists:true,columns}
  }
  return tables
}
