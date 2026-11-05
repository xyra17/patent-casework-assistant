import test from 'node:test'
import assert from 'node:assert/strict'
import { planDisplaySteps } from '../src/taskPresentation.js'

test('same quote with different kinds displays both conditions, leaving stored requirements intact', () => {
  const quote = '下两周该出的和已经拖过期的都给我看看'
  const plan = {
    department_request: {due_field:'draft_due_date', requirements:[
      {kind:'deadline_upcoming',quote}, {kind:'deadline_overdue',quote},
    ]},
    steps:[`需求：${quote}`,`需求：${quote}`,'执行查询','核对来源'],
  }
  const original = structuredClone(plan)
  assert.deepEqual(planDisplaySteps(plan), [
    '查询近期到期的初稿案件（基准日起14天内）',
    '查询已经逾期的初稿案件（期限早于基准日）','执行查询','核对来源',
  ])
  assert.deepEqual(plan, original)
  plan.department_request.due_field = 'client_review_due_date'
  assert.match(planDisplaySteps(plan)[0], /客户送审/)
})

test('other purposes sharing a quote remain distinct; untyped and current plans are not rewritten', () => {
  const plan = {department_request:{requirements:[{kind:'monthly_counts',quote:'比较两个月'},{kind:'monthly_comparison',quote:'比较两个月'}]},steps:['需求：比较两个月','需求：比较两个月']}
  assert.deepEqual(planDisplaySteps(plan), ['统计各月收案与交局数量：比较两个月','比较各月变化：比较两个月'])
  const generic = {steps:['查询','查询']}
  assert.equal(planDisplaySteps(generic),generic.steps)
  const current = {department_request:plan.department_request, steps:['统计各月收案与交局数量：比较两个月','比较各月变化：比较两个月']}
  assert.equal(planDisplaySteps(current),current.steps)
})
