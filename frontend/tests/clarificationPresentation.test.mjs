import test from 'node:test'
import assert from 'node:assert/strict'
import { clarificationPresentation, clarificationChoiceText } from '../src/clarificationPresentation.js'

test('existing waiting questions get readable copy without changing saved identity or allowed choices', () => {
  const question = {
    id: 'saved-question', kind: 'recommendation_evidence',
    reason: 'technical_mapping_unconfirmed', detail: '已按技术原话补查当前接入的正文',
    request: { query: '量子纠错 逻辑量子比特' },
    options: [{ id: 'supplement' }], lookup_case_ids: [], lookup_evidence_ids: [],
  }
  const saved = structuredClone(question)
  const display = clarificationPresentation(question)
  assert.deepEqual(question, saved)
  assert.equal(display.id, question.id)
  assert.equal(display.reason, question.reason)
  assert.deepEqual(display.options, [{ id: 'supplement' }])
  assert.equal(display.technical_query, '量子纠错 逻辑量子比特')
  assert.match(display.detail, /案件文档/)
  assert.doesNotMatch(display.detail, /接入|原话|正文|经历依据/)
})

test('search scope choices retain identity and show the selected scope in task understanding', () => {
  const scope = {id: 'scope1', kind: 'archive_scope', options: [
    {id: 'scope_mention'}, {id: 'scope_topic'}, {id: 'scope_mechanism'},
  ]}
  assert.equal(clarificationPresentation(scope), scope)
  assert.match(clarificationChoiceText({choice: 'scope_mention'}), /背景.*否定提及/)
  assert.match(clarificationChoiceText({choice: 'scope_topic'}), /核心主题/)
  assert.match(clarificationChoiceText({choice: 'scope_mechanism'}), /采用/)
  assert.equal(clarificationChoiceText({choice: 'supplement', text: '补充范围'}), '补充范围')
})

test('count means possible cases, not confirmed experience or number of evidence entries', () => {
  const display = clarificationPresentation({
    kind: 'recommendation_evidence', reason: 'historical_evidence_missing',
    technical_query: '教师学生模型', lookup_case_ids: ['C1', 'C2'], lookup_evidence_ids: ['S1', 'S2', 'S3'],
  })
  assert.match(display.detail, /2个可能相关的案件/)
  assert.match(display.detail, /不足以判断/)
  assert.equal(display.technical_query, '教师学生模型')
  const legacy = clarificationPresentation({ kind: 'recommendation_evidence' })
  assert.doesNotMatch(legacy.detail, /找到\d|没有找到/)
  const other = { kind: 'other', detail: '保留其他类型问题' }
  assert.equal(clarificationPresentation(other), other)
})
