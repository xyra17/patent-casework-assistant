import test from 'node:test'
import assert from 'node:assert/strict'
import { tablePresentation } from '../src/schemaPresentation.js'

test('权限后缀单独展示，保留有业务含义的括号和自定义名称', () => {
  assert.deepEqual(tablePresentation('cw_cases', {label:'部门案件信息（只读同步）'}), {label:'部门案件信息',note:'只读'})
  assert.deepEqual(tablePresentation('notes', {label:'业务补充 (可导入维护)'}), {label:'业务补充',note:'可维护'})
  assert.deepEqual(tablePresentation('notes', {label:'会议（客户确认）'}), {label:'会议（客户确认）',note:''})
})

test('已知表缺少业务名时使用短名称，未知表仍显示真实标识', () => {
  assert.equal(tablePresentation('cw_documents', {label:'cw_documents'}).label, '案件文件')
  assert.equal(tablePresentation('cw_tags').label, '案件标签')
  assert.equal(tablePresentation('cw_sync_state', {label:'自定义同步记录'}).label, '自定义同步记录')
  assert.deepEqual(tablePresentation('custom_table'), {label:'custom_table',note:''})
})
