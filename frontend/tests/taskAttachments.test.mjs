import test from 'node:test'
import assert from 'node:assert/strict'
import {stageAttachments,prepareTaskAttachments,comparisonQuery} from '../src/sessionFiles.js'
import {QUICK_TEMPLATES,TASK_GROUPS,taskTemplates,featuredTemplates,groupedTemplates,attachmentRequirement,attachmentTaskContext} from '../src/taskTemplates.js'

test('featured business entries keep their source permissions independently of menu ordering',()=>{
  const featured=featuredTemplates([...QUICK_TEMPLATES].reverse())
  assert.deepEqual(featured.map(t=>t.id),['assignment-reference','public-comparison','business-charts'])
  assert.deepEqual(featured.map(t=>t.allowed_sources),[
    ['db_query','knowledge'],['db_query','knowledge','web_search'],['db_query'],
  ])
  assert.deepEqual(featured.map(t=>t.mode),['auto','auto','analysis'])
  assert.match(featured[2].value,/图/)
})

test('department catalog refreshes stale backend templates while custom catalogs remain authoritative',()=>{
  const stale={task_examples_catalog:'department',quick_templates:QUICK_TEMPLATES.slice(0,3)}
  assert.equal(taskTemplates(stale),QUICK_TEMPLATES)
  const custom={quick_templates:[{id:'custom',label:'自定义任务'}],featured_template_ids:['missing']}
  assert.equal(taskTemplates(custom),custom.quick_templates)
  assert.deepEqual(featuredTemplates(taskTemplates(custom),custom),custom.quick_templates)
})

test('business scenarios preserve attachment requirements, modes and source permissions',()=>{
  assert.equal(new Set(QUICK_TEMPLATES.map(t=>t.id)).size,QUICK_TEMPLATES.length)
  for(const group of TASK_GROUPS) assert.ok(groupedTemplates(QUICK_TEMPLATES,group.id).length)
  const doc=QUICK_TEMPLATES.find(t=>t.id==='document-comparison')
  assert.deepEqual(doc.allowed_sources,[])
  assert.ok(attachmentRequirement(doc,[]))
  assert.equal(attachmentRequirement(doc,[{name:'old.docx'},{name:'new.pdf'}]),'')
  assert.ok(attachmentRequirement(doc,[{name:'old.csv'},{name:'new.pdf'}]))
  assert.match(attachmentTaskContext(doc,[{name:'old.pdf'},{name:'new.pdf'}]),/基准文件："old.pdf"；对比文件："new.pdf"/)
})

test('business scenarios include source-isolated research, disclosure delivery and uploaded-table analysis',()=>{
  const get=id=>QUICK_TEMPLATES.find(t=>t.id===id)
  const web=get('public-reference')
  assert.deepEqual(web.allowed_sources,['web_search'])
  assert.equal(web.mode,'quick')
  const company=get('company-guidance')
  assert.deepEqual(company.allowed_sources,['knowledge'])
  assert.match(company.value,/公司文档/)
  assert.match(company.value,/未核实|找不到.*说没找到/)
  const report=get('disclosure-report')
  assert.deepEqual(report.allowed_sources,[])
  assert.equal(report.requirePdf,true)
  assert.equal(report.mode,'report')
  assert.ok(attachmentRequirement(report,[]))
  assert.equal(attachmentRequirement(report,[{name:'交底.docx'}]),'')
  const table=get('uploaded-table-analysis')
  assert.deepEqual(table.allowed_sources,[])
  assert.equal(table.mode,'analysis')
  assert.ok(attachmentRequirement(table,[{name:'交底.docx'}]))
  assert.equal(attachmentRequirement(table,[{name:'案件.xlsx'}]),'')
  assert.match(table.value,/追问|先问我/)
  const dates=get('case-deadlines')
  assert.deepEqual(dates.allowed_sources,['db_query'])
  for(const field of [/初稿/,/客户送审|送客户审核/,/向局提交/])assert.match(dates.value,field)
})

test('pending attachment validation preserves the previous selection on invalid batches',()=>{
  const files=[{name:'one.docx',size:100}]
  assert.equal(stageAttachments(files,[{name:'two.pdf',size:100}]).length,2)
  for(const more of [[{name:'one.docx',size:20}],[{name:'bad.exe',size:20}], [{name:'empty.txt',size:0}],
    [{name:'large.txt',size:20*1024*1024+1}], Array.from({length:20},(_,n)=>({name:`${n}.txt`,size:1})),
    Array.from({length:6},(_,n)=>({name:`${n}.txt`,size:20*1024*1024}))]) {
    assert.throws(()=>stageAttachments(files,more))
    assert.equal(files.length,1)
  }
  assert.equal(stageAttachments([],[{name:'boundary.txt',size:20*1024*1024}]).length,1)
})

test('scan and drawing scenarios accept visual files while mixed PDFs require PDF',()=>{
  for (const id of ['scanned-disclosure-search','patent-drawing-review','scanned-table-analysis']) {
    const template=QUICK_TEMPLATES.find(t=>t.id===id)
    assert.ok(template)
    for (const name of ['扫描.pdf','附图.png','清单.jpg','原件.tiff'])
      assert.equal(attachmentRequirement(template,[{name}]),'')
    assert.ok(attachmentRequirement(template,[{name:'数据.xlsx'}]))
  }
  const mixed=QUICK_TEMPLATES.find(t=>t.id==='mixed-pdf-check')
  assert.equal(attachmentRequirement(mixed,[{name:'混合.pdf'}]),'')
  assert.ok(attachmentRequirement(mixed,[{name:'单页.png'}]))
  assert.deepEqual(mixed.allowed_sources,[])
  assert.deepEqual(QUICK_TEMPLATES.find(t=>t.id==='scanned-disclosure-search').allowed_sources,['knowledge'])
})

test('task preparation waits for successful complete upload and does not start on failure',async()=>{
  const files=[{name:'a.txt',size:1},{name:'b.txt',size:1}]
  let resolveUpload,completed=false
  const pending=prepareTaskAttachments('thread',files,async(id,received)=>{
    assert.equal(id,'thread');assert.equal(received,files)
    return await new Promise(resolve=>{resolveUpload=resolve})
  }).then(()=>{completed=true})
  await Promise.resolve();assert.equal(completed,false)
  resolveUpload({files:['a.txt','b.txt']});await pending;assert.equal(completed,true)
  await assert.rejects(()=>prepareTaskAttachments('thread',files,async()=>({files:['a.txt']})),/任务未开始/)
  await assert.rejects(()=>prepareTaskAttachments('thread',files,async()=>{throw Error('network')}),/network/)
  await prepareTaskAttachments('thread',[],async()=>assert.fail('empty selection must not upload'))
})

test('existing-session comparison binds both chosen files without changing external source permissions',()=>{
  assert.match(comparisonQuery('old.docx','new.pdf'),/基准文件："old.docx"；对比文件："new.pdf"/)
  assert.match(comparisonQuery('old.csv','new.xlsx'),/案号/)
  assert.match(comparisonQuery('old.csv','new.xlsx'),/不要生成文件/)
  assert.throws(()=>comparisonQuery('same.txt','same.txt'))
  assert.throws(()=>comparisonQuery('a.csv','b.pdf'))
})
