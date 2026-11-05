import test from 'node:test'
import assert from 'node:assert/strict'
import {reflectionDetails, qualitySummaryText, issueLifecycleDetails} from '../src/reflection.js'

const base = {hard_issues: [], soft_issue: null, require_report: false, require_pdf: false,
  required_sources: [], called_sources: [], word_count: 200}

test('legacy empty soft_issue cannot prove that content review passed', () => {
  assert.equal(reflectionDetails(base).softState, 'unknown')
  assert.equal(reflectionDetails(base).items.at(-1).status, '未记录')
})
test('skipped review is distinct from successful review', () => {
  const detail = reflectionDetails({...base, soft_review_status:'skipped', soft_review_detail:'服务调用失败'})
  assert.equal(detail.items.at(-1).state, 'skipped')
  assert.equal(detail.items.at(-1).status, '审查未完成')
  assert.equal(detail.items.at(-1).detail, '服务调用失败')
  assert.equal(reflectionDetails({...base, soft_review_status:'passed'}).softState, 'passed')
})
test('inapplicable checks are not green passes', () => {
  assert.deepEqual(reflectionDetails(base).items.filter(item => ['PDF 产物','数据源覆盖'].includes(item.label)).map(item => item.state), ['not_required','not_required'])
})
test('failed hard checks prevent content review and retain complete issues', () => {
  const issue = '缺少 knowledge 的检索结果：' + '详细问题'.repeat(100)
  const detail = reflectionDetails({...base, hard_issues:[issue], required_sources:['knowledge']})
  assert.equal(detail.hardState, 'failed')
  assert.equal(detail.softState, 'not_run')
  assert.equal(detail.items.find(item => item.label === '数据源覆盖').state, 'failed')
  assert.equal(detail.issues[0], issue)
})
test('older explicit failure stays failed; missing fields stay unknown', () => {
  assert.equal(reflectionDetails({...base, soft_issue:'结论无依据'}).softState, 'failed')
  assert.equal(reflectionDetails({}).hardState, 'unknown')
  assert.ok(reflectionDetails({}).items.every(item => item.state === 'unknown'))
  assert.equal(reflectionDetails(null), null)
})

test('quality stop reasons are distinct and retain selected budget', () => {
  assert.equal(qualitySummaryText(null), '')
  assert.match(qualitySummaryText({stop_reason:'no_progress', rounds_used:3, max_rounds:6}), /提前停止.*3 \/ 最多 6/)
  assert.match(qualitySummaryText({stop_reason:'max_rounds', rounds_used:1, max_rounds:1}), /上限.*1 \/ 最多 1/)
  assert.match(qualitySummaryText({stop_reason:'review_unavailable', rounds_used:1, max_rounds:5}), /尚未完成审查/)
})

test('retrieval gap observations remain separate from reviewer conclusions', () => {
  const gaps = [{id:'G1', missing:'需要实际金额'}, {routing_decision:{status:'approved'}}]
  const details = reflectionDetails({...base, evidence_gaps:gaps, soft_review_status:'passed'})
  assert.deepEqual(details.gaps, [gaps[0]])
  assert.equal(details.softState, 'passed')
})

test('source coverage fails when actual tools exceed this round permissions', () => {
  const detail = reflectionDetails({...base, required_sources:['knowledge'], called_sources:['knowledge','web_search'], allowed_sources:['knowledge']})
  const source = detail.items.find(item => item.label === '数据源覆盖')
  assert.equal(source.state, 'failed')
  assert.match(source.detail, /超出本轮开放范围：网络检索/)
})

test('program verification is visible and never labeled as a model verdict', () => {
  const detail = reflectionDetails({...base, soft_review_status:'passed', soft_review_mode:'program_contract',
    soft_review_detail:'程序核对，本次没有调用模型审稿。', business_check:{status:'passed', checked_claims:4, issues:[], scope:'字段与原文绑定'}})
  assert.equal(detail.items.at(-1).label, '任务条件核对（程序）')
  assert.match(detail.items.at(-1).detail, /没有调用模型审稿/)
  assert.match(detail.items.find(item => item.label === '业务事实与引用').detail, /4 项/)
})

test('structured review preserves exact anchors and marks unresolved criteria', () => {
  const verification = {mode:'structured_content',status:'failed',checks:[
    {id:'V1',label:'任务完成度',status:'passed',detail:'已完成',task_quote:'统计净额',evidence_ids:[]},
    {id:'V2',label:'证据支持',status:'unverified',reason:'missing_evidence',detail:'退货未核实',answer_quote:'已扣除20元',
      evidence_ids:['S4'],action:'retrieve_evidence',action_label:'补查依据',suggestion:'核对退货汇总'},
  ]}
  const detail = reflectionDetails({...base,verification,soft_review_status:'failed'})
  assert.equal(detail.verificationChecks[0].pending,false)
  assert.equal(detail.verificationChecks[1].pending,true)
  assert.equal(detail.verificationChecks[1].statusText,'证据不足')
  assert.deepEqual(detail.verificationChecks[1].evidenceIds,['S4'])
  assert.equal(detail.verificationChecks[1].answer_quote,'已扣除20元')
  assert.equal(detail.verificationChecks[1].suggestion,'核对退货汇总')
})

test('unverified reasons never conflate service errors with missing evidence', () => {
  const checks = ['missing_evidence','ambiguous_requirement','verification_unavailable','other',undefined].map(reason =>
    ({status:'unverified',reason}))
  const details = reflectionDetails({...base,verification:{checks}})
  assert.deepEqual(details.verificationChecks.map(item=>item.statusText),
    ['证据不足','条件待确认','核验未完成','其他原因未核验','未核验（原因未分类）'])
  assert.ok(details.verificationChecks.every(item=>item.pending))
})

test('issue history exposes corrections, updated evidence, and budget exhaustion', () => {
  const items = issueLifecycleDetails([
    {issue_id:'R1-V1', status:'resolved', history:[{round:1,outcome:'pending'}, {round:2,outcome:'corrected',detail:'已扣除退货'}]},
    {issue_id:'R1-V2', status:'resolved', history:[{round:1,outcome:'pending'}, {round:2,outcome:'evidence_added',evidence_ids:['S4']}]},
    {issue_id:'R2-V3', status:'pending', stop_reason:'max_rounds', history:[{round:2,outcome:'pending'}]},
  ])
  assert.equal(items[0].outcomeText,'已修正并复核通过')
  assert.equal(items[0].history[1].detail,'已扣除退货')
  assert.equal(items[1].outcomeText,'证据已更新，复核通过')
  assert.deepEqual(items[1].history[1].evidenceIds,['S4'])
  assert.equal(items[2].outcomeText,'轮次预算耗尽，仍未解决')
  assert.equal(items[2].resolved,false)
})

test('latest stop snapshot enriches outcome without rewriting earlier rounds or legacy records', () => {
  const record = {...base,issue_lifecycle:[{issue_id:'R1-V2',status:'pending',history:[]}]}
  const summary = {issue_lifecycle:[{...record.issue_lifecycle[0],stop_reason:'execution_budget'}]}
  assert.equal(reflectionDetails(record).issueLifecycle[0].outcomeText,'待处理／待复核')
  assert.equal(reflectionDetails(record,summary).issueLifecycle[0].outcomeText,'执行预算耗尽，仍未解决')
  assert.deepEqual(reflectionDetails(base).issueLifecycle,[])
  assert.deepEqual(issueLifecycleDetails(null),[])
})

test('incomplete review and no progress retain unresolved issue states', () => {
  for(const [stop, expected] of [['review_unavailable','审查未完成，仍待核验'], ['no_progress','无进展，停止时仍未解决'],
    ['timeout','任务时间预算耗尽，仍未解决'],['user_cancel','任务已终止，仍未解决'],['execution_error','执行异常中断，仍未解决']]) {
    const item = issueLifecycleDetails([{status:'pending',stop_reason:stop,history:[{round:2,outcome:'unverified'}]}])[0]
    assert.equal(item.outcomeText,expected)
    assert.equal(item.history[0].outcomeText,'尚未复核')
  }
})

test('returning an earlier candidate cannot label later repairs as applied to the delivered answer', () => {
  const details = issueLifecycleDetails([{status:'resolved', returned_report_unverified:true,
    history:[{round:2,outcome:'corrected'}]}])
  assert.equal(details[0].resolved,false)
  assert.equal(details[0].outcomeText,'后续复核通过；返回草稿仍待核对')
  assert.match(qualitySummaryText({stop_reason:'max_rounds',rounds_used:3,max_rounds:3,returned_round:1}), /最终保留第 1 轮草稿/)
})

test('legacy and specialized reviews do not invent generic checklist passes', () => {
  assert.deepEqual(reflectionDetails(base).verificationChecks,[])
  const details=reflectionDetails({...base,verification:{mode:'program_contract',checks:[],status:'passed'}})
  assert.deepEqual(details.verificationChecks,[])
  assert.equal(details.verification.mode,'program_contract')
})

test('citation integrity failure remains visible independently of model review', () => {
  const details=reflectionDetails({...base,hard_issues:['引用了不存在的来源编号：S99'],
    verification:{mode:'structured_content',checks:[],status:'not_run'},soft_review_status:'not_run'})
  assert.equal(details.items.find(item=>item.label==='引用编号').state,'failed')
  assert.equal(details.softState,'not_run')
})

test('short reports have no length gate in the quality panel', () => {
  const details=reflectionDetails({...base,require_report:true,word_count:8})
  assert.ok(details.items.every(item=>item.label!=='报告篇幅'))
  assert.equal(details.hardState,'passed')
})
