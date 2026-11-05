import test from 'node:test'
import assert from 'node:assert/strict'
import {highlightSegments,archiveReferenceList} from '../src/archivePresentation.js'

test('highlighting keeps hostile HTML as text and reconstructs exact source',()=>{
  const text='<script>alert(1)</script> 向量检索与向量检索，HNSW。'
  const segments=highlightSegments(text,['向量','向量检索','HNSW','hnsw','不存在'])
  assert.equal(segments.map(s=>s.text).join(''),text)
  assert.deepEqual(segments.filter(s=>s.matched).map(s=>s.text),['向量检索','向量检索','HNSW'])
  assert.equal(segments[0].matched,false)
})
test('literal regex characters and absent terms do not become regex or invented highlights',()=>{
  assert.deepEqual(highlightSegments('A+B <原文>',['A+B']),[{text:'A+B',matched:true},{text:' <原文>',matched:false}])
  assert.deepEqual(highlightSegments('没有匹配',[]),[{text:'没有匹配',matched:false}])
})
test('reference copy respects visible selection and keeps revision and original quote',()=>{
  const row={case_id:'DEMO-2026-001',owner_name:'甲',sources:[{excerpt:'不能改写的原文',source:'目录/交底.docx',location:'第5段',revision:'original-version'}]}
  const result={query:'自然技术问题',results:[row,{case_id:'未展开案件',sources:[]}]}
  const text=archiveReferenceList(result,[row])
  assert.ok(text.includes('不能改写的原文'))
  assert.ok(text.includes('目录/交底.docx · 第5段'))
  assert.ok(text.includes('original-version'))
  assert.ok(text.includes('不代表已找齐所有案件'))
  assert.ok(!text.includes('未展开案件'))
})

test('drawing references retain association scope and version without inventing a body quote',()=>{
  const text=archiveReferenceList({query:'DEMO-2026-002'},[{case_id:'DEMO-2026-002',sources:[],related_drawings:[{source:'DEMO-2026-002.vsd',revision:'drawing-version',scope:'未提取到图中文字，请下载原件核对'}]}])
  assert.ok(text.includes('绘图附件：DEMO-2026-002.vsd'))
  assert.ok(text.includes('按所编号关联'))
  assert.ok(text.includes('drawing-version'))
  assert.ok(!text.includes('原文：'))
})
