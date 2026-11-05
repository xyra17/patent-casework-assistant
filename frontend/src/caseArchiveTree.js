const structure = [
  {key:'intake',label:'01_立案资料'},
  {key:'disclosure',label:'02_技术交底',children:['附图及补充实验材料']},
  {key:'communication',label:'03_沟通记录'},
  {key:'application',label:'04_申请文件'},
  {key:'prosecution',label:'05_办理文件',children:['后续通知及答复材料']},
  {key:'other',label:'06_其他资料'},
]
const bare = text => text.replace(/^\d+[_、.\s-]*/, '')
function directory(key,label) { return {key,label,type:'folder',count:0,children:[]} }

// Project existing documents into the standard archive; never invent file slots.
export function caseArchiveTree(folders=[]) {
  return structure.filter(spec=>spec.key!=='other' || folders.some(f=>f.key==='other' && f.documents?.length)).map(spec=>{
    const root=directory(spec.key,spec.label)
    for(const name of spec.children || [])root.children.push(directory(`${spec.key}/${name}`,name))
    for(const doc of folders.find(f=>f.key===spec.key)?.documents || []) {
      const parts=doc.relative_path.split('/').filter(Boolean)
      const name=doc.name || parts.at(-1)
      const caseIndex=parts.findIndex(p=>/^(?:[A-Za-z]+-)+\d{4}-\d+$/.test(p))
      const parents=parts.slice(caseIndex>=0?caseIndex+1:1,-1)
      const classified=parents.findIndex(p=>bare(p)===bare(spec.label))
      let nested=classified>=0?parents.slice(classified+1):parents
      if(!nested.length && spec.key==='disclosure' && (doc.kind==='drawing' || /附图|补充实验|实验材料/.test(name))) nested=['附图及补充实验材料']
      if(!nested.length && spec.key==='prosecution' && /通知|审查意见|答复|补正/.test(name))nested=['后续通知及答复材料']
      let parent=root
      parent.count++
      for(const label of nested) {
        let child=parent.children.find(n=>n.type==='folder' && bare(n.label)===bare(label))
        if(!child){child=directory(`${parent.key}/${label}`,label);parent.children.push(child)}
        child.count++;parent=child
      }
      parent.children.push({key:`file:${doc.id}`,type:'file',label:name,document:doc})
    }
    const sort=node=>{node.children.sort((a,b)=>a.type===b.type?a.label.localeCompare(b.label,'zh-CN'):a.type==='folder'?1:-1);node.children.filter(n=>n.type==='folder').forEach(sort)}
    sort(root)
    return root
  })
}
