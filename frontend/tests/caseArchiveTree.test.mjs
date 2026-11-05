import test from 'node:test'
import assert from 'node:assert/strict'
import {caseArchiveTree} from '../src/caseArchiveTree.js'
import {referenceGroups,filterReferences} from '../src/referenceLibrary.js'

test('every case has the same numbered folders and empty subfolders without invented files',()=>{
  const tree=caseArchiveTree([])
  assert.deepEqual(tree.map(n=>n.label),['01_立案资料','02_技术交底','03_沟通记录','04_申请文件','05_办理文件'])
  assert.equal(tree[1].children[0].label,'附图及补充实验材料')
  assert.equal(tree[4].children[0].label,'后续通知及答复材料')
  assert.ok(tree.every(n=>n.count===0))
  assert.ok(tree.flatMap(n=>n.children).every(n=>n.type==='folder' && n.count===0))
})
test('real files preserve identity, source names, nested folders and aggregate counts',()=>{
  const doc=(id,path,kind='application')=>({id,relative_path:`AI-2026-032/${path}`,kind,state:'parsed'})
  const tree=caseArchiveTree([
    {key:'disclosure',documents:[doc('d','02_技术交底/技术交底书.docx','disclosure'),doc('g','附图.vsd','drawing'),doc('e','02_技术交底/附图及补充实验材料/实验1/记录.xlsx','disclosure')]},
    {key:'application',documents:[doc('a','申请文件.pdf')]},
    {key:'prosecution',documents:[doc('n','审查意见通知书.pdf','receipt'),doc('r','交局确认.json','receipt')]},
  ])
  const flatten=nodes=>nodes.flatMap(n=>[n,...flatten(n.children || [])])
  const nodes=flatten(tree)
  assert.deepEqual(nodes.filter(n=>n.type==='file').map(n=>n.document.id).sort(),['a','d','e','g','n','r'])
  assert.equal(tree.reduce((n,f)=>n+f.count,0),6)
  assert.equal(tree[1].count,3)
  assert.equal(tree[1].children.find(n=>n.label==='附图及补充实验材料').count,2)
  assert.equal(tree[4].children.find(n=>n.label==='后续通知及答复材料').count,1)
  assert.ok(nodes.some(n=>n.label==='申请文件.pdf' && n.document.id==='a'))
  assert.ok(!nodes.some(n=>n.label==='提交稿.pdf'))
})
test('public reference categories and filtering derive from actual documents',()=>{
  const docs=[{name:'公开原文/a.pdf',version_info:{version_label:'2026版'}},{name:'公开原文/b.pdf'},{name:'审查资料/c.md'},{name:'manual.docx'}]
  assert.equal(referenceGroups(docs).reduce((n,g)=>n+g.count,0),4)
  assert.ok(referenceGroups(docs).some(g=>g.label==='未分类' && g.count===1))
  assert.deepEqual(filterReferences(docs,'公开原文','2026').map(d=>d.name),['公开原文/a.pdf'])
  assert.equal(filterReferences(docs,'审查资料','pdf').length,0)
  assert.equal(filterReferences(docs,'','MANUAL').length,1)
})

test('domain and year are outside the case subtree, and unknown files stay visible',()=>{
  const tree=caseArchiveTree([{key:'application',documents:[{id:'x',relative_path:'01_AI领域/2026/AI-2026-032/04_申请文件/提交稿.pdf'}]},{key:'other',documents:[{id:'u',relative_path:'01_AI领域/2026/AI-2026-032/说明.txt'}]}])
  assert.equal(tree.length,6)
  assert.equal(tree[3].children[0].label,'提交稿.pdf')
  assert.equal(tree[5].children[0].label,'说明.txt')
})
