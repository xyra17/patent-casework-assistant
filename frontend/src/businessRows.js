export function editableFields(record) {
  return Object.fromEntries(record.columns.map(c=>[c.name,{value:record.values[c.name]===null?'':String(record.values[c.name]),isNull:record.values[c.name]===null}]))
}
export function rowChanges(record,fields) {
  if(!record)return {}
  return Object.fromEntries(record.columns.filter(c=>c.editable).map(c=>[c.name,fields[c.name]?.isNull?null:fields[c.name]?.value]).filter(([key,value])=>value!==undefined && value!==(record.values[key]===null?null:String(record.values[key]))))
}
export function operationIdentity(body,previous,createId=()=>crypto.randomUUID()) {
  const payload=JSON.stringify(body)
  return previous?.payload===payload?previous:{payload,id:createId()}
}

// Attach the exact server key without colliding with real column names.
const maintenanceKey=Symbol('maintenance-primary-key')
export function businessRowsWithKeys(result) {
  return result.rows.map((row,index)=>({...Object.fromEntries(result.columns.map((name,i)=>[name,row[i]])),[maintenanceKey]:result.row_keys?.[index]}))
}
export function selectedBusinessKeys(rows) {
  const keys=rows.map(row=>row[maintenanceKey])
  if(keys.some(key=>!key || !Object.keys(key).length))throw new Error('选中记录缺少主键，请刷新后重新勾选')
  return keys
}

export function creationValues(record, fields) {
  if (!record) return {}
  return Object.fromEntries(record.columns.filter(c=>c.insertable && !fields[c.name]?.useDefault).map(c=>[c.name,fields[c.name]?.isNull?null:fields[c.name]?.value]).filter(([,value])=>value!==undefined))
}
