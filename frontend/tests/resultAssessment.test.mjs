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

import {productBoundaryView} from '../src/resultAssessment.js'
test('product status comes only from the bound backend contract',()=>{
  assert.equal(productBoundaryView({result:'人工确认',status:'completed'}),null)
  const session={result_assessment:{report_sha256:'x',product_boundary:{report_sha256:'x',
    status:'REVIEW_REQUIRED',professional_judgments:[{judgment:'机制关系'}],issues:[]}}}
  assert.equal(productBoundaryView(session).label,'待人工确认')
  session.result_assessment.product_boundary.status='INCOMPLETE'
  assert.equal(productBoundaryView(session).label,'未完成')
  assert.match(productBoundaryView(session).candidateLabel,/未作为已核验终稿/)
  session.result_assessment.product_boundary.report_sha256='old'
  assert.equal(productBoundaryView(session),null)
})
test('text comparison and professional judgment are separate sections',()=>{
  const b={report_sha256:'x',status:'REVIEW_REQUIRED',objective_status:'VERIFIED',
    objective_comparison:{status:'VERIFIED',records:[{kind:'modified'}]},professional_judgments:[{judgment:'技术影响'}],issues:[]}
  const view=productBoundaryView({result_assessment:{report_sha256:'x',product_boundary:b}})
  assert.equal(view.comparisonVerified,true)
  assert.equal(view.comparisonRows.length,1)
  assert.equal(view.professional_judgments.length,1)
  assert.match(view.candidateLabel,/初步分析/)
})
