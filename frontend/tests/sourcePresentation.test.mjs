import test from 'node:test'
import assert from 'node:assert/strict'
import { sourceName, sourceLocationSummary } from '../src/sourcePresentation.js'

test('same-title references display distinct filenames without changing original paths', () => {
  const first = { title: '一种资源处理方法', source: '2026/案号/案件信息.xlsx' }
  const second = { ...first, source: '2026/案号/立案.doc' }
  assert.equal(sourceName(first), '案件信息.xlsx')
  assert.equal(sourceName(second), '立案.doc')
  assert.equal(first.source, '2026/案号/案件信息.xlsx')
  assert.equal(sourceName({ source: 'C:\\资料\\立案.doc' }), '立案.doc')
  assert.equal(sourceName({ url: 'https://example.com/a/b?q=1' }), 'example.com')
  assert.equal(sourceName(), '')
})

test('compact worksheet coordinates only when both sheet and row match', () => {
  assert.equal(sourceLocationSummary('工作表 案件信息 · 第2行 · 第6列；工作表 案件信息 · 第2行 · 第10列；工作表 案件信息 · 第2行 · 第5列'), '案件信息 · 第2行 · 第6、10、5列')
  const mixedRow = '工作表 案件信息 · 第2行 · 第6列；工作表 案件信息 · 第3行 · 第10列'
  const mixedSheet = '工作表 案件信息 · 第2行 · 第6列；工作表 交局 · 第2行 · 第10列'
  assert.equal(sourceLocationSummary(mixedRow), mixedRow)
  assert.equal(sourceLocationSummary(mixedSheet), mixedSheet)
})

test('known structured fields use business labels; unknown locators remain identifiable', () => {
  assert.equal(sourceLocationSummary('JSON /case_fields/actual_filed_date；JSON /case_fields/filing_confirmed'), '实际交局日期；交局核实状态')
  assert.equal(sourceLocationSummary('JSON /custom/field'), '结构化字段 /custom/field')
  assert.equal(sourceLocationSummary('正文 · 正文块 2'), '正文块 2')
  assert.equal(sourceLocationSummary('技术方案 · 起始行 5 · 字符1–101'), '技术方案 · 起始行 5 · 字符1–101')
  assert.equal(sourceLocationSummary(), '')
  assert.equal(sourceLocationSummary(null), '')
})
