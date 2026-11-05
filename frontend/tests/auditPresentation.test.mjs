import test from 'node:test'
import assert from 'node:assert/strict'
import {auditAction, auditObject, auditSummary, auditChanges, auditValue} from '../src/auditPresentation.js'

test('audit objects and actions remain readable after a directory is removed', () => {
  assert.equal(auditAction('scan_publish'), '发布同步结果')
  assert.equal(auditAction('future_action'), 'future_action')
  assert.equal(auditObject({action:'root_update',target:'root',before:{label:'历史目录'}}), '历史目录')
  assert.equal(auditObject({action:'field_confirmation',target:'root:S26P000001'}), 'S26P000001')
  assert.equal(auditObject({action:'person_update',target:'p',before:{name:'代理师甲'}}), '代理师甲')
})

test('audit patches never imply deletion of fields omitted from after', () => {
  const changes = auditChanges({before:{owner_name:'甲',title:'案件标题'},after:{owner_name:'乙',enabled:false}}, {owner_name:'代理师'})
  assert.deepEqual(changes.map(row => row.key), ['owner_name','enabled'])
  assert.equal(changes[0].label, '代理师')
  assert.equal(changes[0].before, '甲')
  assert.equal(changes[0].after, '乙')
  assert.equal(changes[1].beforeRecorded, false)
  assert.equal(changes[1].after, '否')
  assert.equal(auditChanges({before:{value:null},after:{value:0}})[0].beforeRecorded, true)
})

test('revoking confirmation is a marker and does not clear the business field', () => {
  const row = {action:'clear_confirmation',before:{value:'2026-10-01'},after:{field:'received_date'}}
  assert.deepEqual(auditChanges(row), [])
  assert.equal(auditSummary(row,{received_date:'实际收案日期'}), '撤销：实际收案日期')
  assert.deepEqual(auditChanges({action:'purge_catalog',before:{files:3},after:null}), [])
})

test('zero counts and false values remain explicit in audit summaries', () => {
  assert.equal(auditSummary({action:'scan_publish',after:{seen:0,parsed:0,unchanged:0,failed:0}}), '发现 0 个文件 · 解析 0 · 未变 0 · 失败 0')
  assert.equal(auditValue(0), '0')
  assert.equal(auditValue(false), '否')
  assert.equal(auditValue([]), '无')
})
