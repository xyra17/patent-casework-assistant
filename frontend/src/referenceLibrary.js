export function referenceGroup(doc) {
  const parts=doc.name.split('/')
  return parts.length>1?parts[0]:'未分类'
}
export function referenceGroups(documents=[]) {
  const groups=new Map()
  for(const doc of documents){const group=referenceGroup(doc);groups.set(group,(groups.get(group)||0)+1)}
  return [...groups].map(([label,count])=>({label,count})).sort((a,b)=>a.label.localeCompare(b.label,'zh-CN'))
}
export function filterReferences(documents=[],group='',query='') {
  const term=query.trim().toLocaleLowerCase()
  return documents.filter(doc=>(!group || referenceGroup(doc)===group) && (!term || `${doc.name} ${doc.version_info?.version_label || ''}`.toLocaleLowerCase().includes(term)))
}
