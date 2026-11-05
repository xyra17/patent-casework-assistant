import test from 'node:test'
import assert from 'node:assert/strict'
import {BUSINESS_RECORDS,recordColumns,defaultRecordTable,recordLabel,recordValue} from '../src/businessRecords.js'
import {blankRule} from '../src/ruleForms.js'
test('department navigation prefers actual cases and keeps technical columns explicitly accessible',()=>{
 assert.equal(defaultRecordTable([{name:'cw_sync_state'},{name:'cw_cases'}]),'cw_cases')
 assert.equal(defaultRecordTable([]),'')
 assert.equal(defaultRecordTable([{name:'custom'}]),'custom')
 const cols=['case_key','case_id','received_date','source_refs','root_id','revision','state']
 assert.deepEqual(recordColumns('cw_cases',cols),['case_id','received_date','state'])
 assert.deepEqual(recordColumns('cw_cases',cols,true),cols)
 assert.deepEqual(recordColumns('custom',cols),cols)
 assert.equal(BUSINESS_RECORDS.length,4)
 assert.equal(recordLabel('cw_meetings'),'会议记录')
 assert.equal(recordLabel('custom','自定义'),'自定义')
})
test('new rules start disabled and carry an explicit scope to edit',()=>{
 assert.equal(blankRule().active,false)
 assert.equal(blankRule().match_mode,'keywords')
 assert.ok(blankRule().scope_note)
})
test('business display keeps unknown values distinct and tags retain their case association',()=>{
 assert.deepEqual(recordColumns('cw_tags',['case_key','tag','root_id']),['case_key','tag'])
 assert.equal(recordValue('cw_tags','case_key','root:AI-2026-001'),'AI-2026-001')
 assert.equal(recordValue('cw_cases','filing_verified',null),'—')
 assert.equal(recordValue('cw_cases','filing_verified',0),'未核实')
 assert.equal(recordValue('cw_cases','filing_verified',1),'已核实')
 assert.equal(recordValue('cw_meetings','agents','["甲","乙"]'),'甲、乙')
 assert.equal(recordValue('cw_meetings','agents','invalid'),'invalid')
 assert.equal(recordValue('cw_documents','state','parsed'),'已解析')
 assert.equal(recordValue('custom','state','parsed'),'parsed')
 assert.equal(recordValue('cw_documents','state','parsed',true),'parsed')
})

test('technical tag methods are readable while technical details keep the stored identifier',()=>{
 assert.equal(recordValue('cw_tags','method','local_dictionary_v1'),'按技术关键词匹配')
 assert.equal(recordValue('cw_tags','method','local_dictionary_v1',true),'local_dictionary_v1')
 assert.equal(recordValue('cw_tags','method','new_method'),'其他方式（技术详情中查看）')
})
