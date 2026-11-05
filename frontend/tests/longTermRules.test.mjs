import test from 'node:test'
import assert from 'node:assert/strict'
import {rulePayload,blankRule} from '../src/ruleForms.js'
import {submitTask,saveLongTermRule} from '../src/api.js'
import {buildExecutionTrace} from '../src/executionTrace.js'

test('rule payload does not automatically promote correction history or grant permissions',()=>{
 const rule={...blankRule(),title:'回答结构',content:'先给结论',match_terms:['库存'],source_snapshot:'本次时间和金额',allowed_sources:['web_search'],id:'R1',revision:4}
 const payload=rulePayload(rule)
 assert.equal(payload.source_snapshot,undefined);assert.equal(payload.allowed_sources,undefined)
 assert.equal(payload.id,undefined);assert.equal(payload.revision,undefined)
 assert.equal(payload.binding,null)
})
test('task submission pins skipped rule versions explicitly',async()=>{
 const original=globalThis.fetch;let sent
 globalThis.fetch=async(_url,options)=>{sent=JSON.parse(options.body);return new Response('{}',{status:200})}
 try{await submitTask('库存','thread','库存',false,1,9,[{id:'R1',revision:2}]);assert.equal(sent.expected_revision_id,9);assert.deepEqual(sent.skip_rule_versions,[{id:'R1',revision:2}])}finally{globalThis.fetch=original}
})
test('API preserves structured rule conflict details for the confirmation UI',async()=>{
 const original=globalThis.fetch
 globalThis.fetch=async()=>new Response(JSON.stringify({detail:{code:'rule_conflict',message:'需要核对',rule_context:{conflicts:[{id:'R1',revision:3}]}}}),{status:409})
 try{await assert.rejects(submitTask('库存','thread'),error=>error.message==='需要核对'&&error.detail.code==='rule_conflict'&&error.detail.rule_context.conflicts[0].revision===3)}finally{globalThis.fetch=original}
})
test('long-term rule validation errors expose the editable reason',async()=>{
 const original=globalThis.fetch
 globalThis.fetch=async()=>new Response(JSON.stringify({detail:[{msg:'Value error, 业务口径必须绑定指标'}]}),{status:422})
 try{await assert.rejects(saveLongTermRule({}),/业务口径必须绑定指标/)}finally{globalThis.fetch=original}
})
test('adopted rule events belong to main orchestration and respect every filter',()=>{
 const events=[{event:'rules_context',timestamp:'2026-10-24T00:00:00Z',data:{applied:[{id:'R1',title:'回答顺序',revision:1,content:'先给结论'}],overridden:[]}}]
 assert.equal(buildExecutionTrace(events).recordCount,1)
 assert.equal(buildExecutionTrace(events,{filter:'main'}).recordCount,1)
 for(const filter of ['assistant','tools','errors'])assert.equal(buildExecutionTrace(events,{filter}).recordCount,0)
})
test('rule preparation is counted separately from running task tools',async()=>{
 globalThis.localStorage={getItem:()=>null,setItem:()=>{}}
 const {store,sessionStats}=await import('../src/store.js')
 store.sessions.splice(0,store.sessions.length,{thread_id:'prepare',status:'running',preparing:true})
 assert.deepEqual(sessionStats(),{running:0,completed:0,failed:0,degraded:0,preparing:1,cancelled:0,awaiting_input:0})
 store.sessions[0].preparing=false
 assert.equal(sessionStats().running,1);assert.equal(sessionStats().preparing,0)
 store.sessions[0].status='cancelled';assert.equal(sessionStats().failed,0);assert.equal(sessionStats().cancelled,1)
 store.sessions.splice(0)
})

test('formula payload preserves expressions and converts only editable filter text',async()=>{
 const {bindingPayload,formulaLabel}=await import('../src/ruleForms.js')
 const formula={op:'sub',args:[{op:'field',field:'sales.amount'},{op:'related_sum',field:'refunds.amount',filters:[{field:'refunds.status',op:'eq',value:'完成'}]}]}
 const payload=bindingPayload({metric_key:'net_sales',formula,filters:[{field:'sales.region',op:'in',valueText:'华东,华西'}],time_field:'sales.paid_at'})
 assert.deepEqual(payload.formula,formula);assert.deepEqual(payload.filters[0].value,['华东','华西']);assert.equal(payload.filters[0].valueText,undefined)
 assert.match(formulaLabel(formula),/关联求和/);assert.equal(payload.time_field,'sales.paid_at')
})
test('aliases are explicit rule data, not inferred task permissions',()=>{
 const payload=rulePayload({...blankRule(),aliases:['卖了多少钱'],allowed_sources:['db_query']})
 assert.deepEqual(payload.aliases,['卖了多少钱']);assert.equal(payload.allowed_sources,undefined)
})


test('session statistics separate degraded completion and keep categories mutually exclusive',async()=>{
 globalThis.localStorage={getItem:()=>null,setItem:()=>{}}
 const {store,sessionStats}=await import('../src/store.js')
 store.sessions.splice(0,store.sessions.length,
  {status:'completed',degraded:false},
  {status:'completed',degraded:true},
  {status:'running',degraded:true},
  {status:'failed',degraded:true},
  {status:'cancelled'},
  {status:'running',preparing:true})
 assert.deepEqual(sessionStats(),{running:1,completed:1,failed:1,degraded:1,preparing:1,cancelled:1,awaiting_input:0})
 assert.equal(Object.values(sessionStats()).reduce((sum,count)=>sum+count,0),store.sessions.length)
 store.sessions.splice(0)
})
