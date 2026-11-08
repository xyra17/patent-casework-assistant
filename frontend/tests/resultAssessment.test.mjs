import test from 'node:test'
import assert from 'node:assert/strict'
import { resultAssessmentItems } from '../src/resultAssessment.js'

test('completed status alone does not turn content checks green', () => {
  const items = resultAssessmentItems({status: 'completed', degraded: false})
  assert.equal(items[0].status, '待核验')
  assert.equal(items[1].status, '待核验')
  assert.ok(items.every(item => item.type !== 'success'))
})

test('evidence review, requirement coverage and delivery are displayed independently', () => {
  const items = resultAssessmentItems({result_assessment: {
    facts: {status: 'passed'}, completeness: {status: 'failed'},
    delivery: {status: 'partial', label: '部分交付'},
  }})
  assert.deepEqual(items.map(item => item.status), ['内部核对通过', '内部审查发现缺项', '部分交付'])
  assert.deepEqual(items.map(item => item.type), ['success', 'danger', 'warning'])
})
