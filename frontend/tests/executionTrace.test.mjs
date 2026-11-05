import test from 'node:test'
import assert from 'node:assert/strict'
import {buildExecutionTrace, EXECUTION_FILTER_OPTIONS} from '../src/executionTrace.js'
const ev=(event,data={},n=0)=>({event,data,timestamp:`2026-10-23T12:00:${String(n).padStart(2,'0')}Z`})
const main={agent_name:'主智能体',agent_run_id:'main:session:1',round:1}
const child=(id,name='内部知识库助手')=>({agent_name:name,agent_run_id:id,round:1})
const sequence=[
  ev('agent_activity',{...main,phase:'start',run_id:'m1'},0),
  ev('agent_activity',{...main,phase:'end',run_id:'m1'},1),
  ev('assistant_call',{...main,run_id:'delegate',assistant_name:'内部知识库助手',args:'完整问题'},2),
  ev('agent_activity',{...child('delegate'),phase:'start',run_id:'m2'},3),
  ev('agent_activity',{...child('delegate'),phase:'end',run_id:'m2'},4),
  ev('tool_start',{...child('delegate'),run_id:'t1',tool_name:'search_knowledge_base',args:{query:'原始问题'}},5),
  ev('retrieval_hop',{hop:1,query:'制度',evidence_ids:['S1']},6),
  ev('tool_end',{...child('delegate'),run_id:'t1',summary:'原文'},7),
  ev('tool_end',{...main,run_id:'delegate',tool_name:'task',summary:'检索结果'},8),
  ev('agent_activity',{...main,phase:'start',run_id:'m3'},9),
  ev('agent_activity',{...main,phase:'end',run_id:'m3'},10),
]

test('material evidence gap is a visible waiting step, never a completion',()=>{
 const events=[ev('task_clarification',{clarification:{message:'相关技术经历尚未确认'}})]
 const trace=buildExecutionTrace(events)
 const steps=trace.children.flatMap(x=>x.children)
 assert.equal(steps.length,1)
 assert.match(steps[0].label,/等待你确认/)
 assert.equal(steps[0].severity,'warning')
 assert.equal(steps.some(x=>x.type==='result'),false)
 assert.equal(buildExecutionTrace(events,{filter:'assistant'}).children.length,0)
})
test('main → child → main follows actual event order and preserves tools and retrieval',()=>{
  const trace=buildExecutionTrace(sequence)
  assert.equal(trace.type,'timeline')
  assert.deepEqual(trace.children.map(s=>s.actor),['主智能体','内部知识库助手','主智能体'])
  assert.ok(trace.children[1].children.some(n=>n.type==='tool'))
  assert.ok(trace.children[1].children.some(n=>n.label.startsWith('知识库第 1 跳')))
  assert.equal(trace.children[1].children.find(n=>n.type==='tool').status,'done')
  assert.ok(trace.children[2].children.some(n=>n.type==='return' && n.summary==='检索结果'))
})
test('assistant and tools filters are applied after end events have been correlated',()=>{
  const onlyChild=buildExecutionTrace(sequence,{filter:'assistant'})
  assert.equal(onlyChild.children.length,1)
  assert.equal(onlyChild.children[0].children.find(n=>n.type==='tool').status,'done')
  const tools=buildExecutionTrace(sequence,{filter:'tools'})
  assert.ok(tools.children.every(s=>s.children.every(n=>n.type==='tool')))
  assert.ok(!tools.children.flatMap(s=>s.children).some(n=>['dispatch','return'].includes(n.type)))
})
test('concurrent delegates of same name retain separate run ownership',()=>{
  const trace=buildExecutionTrace([
    ev('assistant_call',{...main,run_id:'a',assistant_name:'内部知识库助手'}),
    ev('assistant_call',{...main,run_id:'b',assistant_name:'内部知识库助手'}),
    ev('tool_start',{...child('a'),run_id:'ta',tool_name:'search_knowledge_base'}),
    ev('tool_start',{...child('b'),run_id:'tb',tool_name:'search_knowledge_base'}),
    ev('tool_end',{run_id:'ta',summary:'a'}), ev('tool_end',{run_id:'tb',summary:'b'}),
  ])
  assert.equal(trace.children[1].actorKey,'a')
  assert.equal(trace.children[2].actorKey,'b')
  assert.equal(trace.children[1].children[0].summary,'a')
  assert.equal(trace.children[2].children[0].summary,'b')
})
test('repeated delegation is a new stage rather than attaching to the last assistant name',()=>{
  const trace=buildExecutionTrace([...sequence,ev('assistant_call',{...main,run_id:'second',assistant_name:'内部知识库助手'}),ev('tool_start',{...child('second'),run_id:'second-tool',tool_name:'search_knowledge_base'})])
  assert.equal(trace.children.at(-1).actorKey,'second')
  assert.equal(trace.children[1].actorKey,'delegate')
})
test('legacy delegates can be replayed without inventing model processing events',()=>{
  const trace=buildExecutionTrace([
    ev('assistant_call',{run_id:'old',assistant_name:'内部知识库助手'}),
    ev('tool_start',{run_id:'oldtool',tool_name:'search_knowledge_base'}),
    ev('tool_end',{run_id:'oldtool',summary:'原文'}),
    ev('tool_end',{run_id:'old',summary:'结果'}),
  ])
  assert.deepEqual(trace.children.map(s=>s.actor),['主智能体','内部知识库助手','主智能体'])
  assert.equal(trace.children.flatMap(s=>s.children).filter(n=>n.type==='activity').length,0)
})
test('ambiguous legacy ownership is labelled, not assigned to the newest delegate',()=>{
  const trace=buildExecutionTrace([
    ev('assistant_call',{run_id:'a',assistant_name:'内部知识库助手'}),
    ev('assistant_call',{run_id:'b',assistant_name:'内部知识库助手'}),
    ev('tool_start',{run_id:'oldtool',tool_name:'search_knowledge_base'}),
  ])
  assert.match(trace.children.at(-1).actor,/历史调用未关联/)
})
test('missing tool end events are not retrospectively marked successful',()=>{
  const trace=buildExecutionTrace([ev('tool_start',{...main,run_id:'t',tool_name:'generate_markdown'}),ev('task_result',{})])
  assert.equal(trace.children[0].children[0].status,'unknown')
})
test('errors preserve failed child tool state and interrupt other outstanding work',()=>{
  const trace=buildExecutionTrace([ev('tool_start',{...child('a'),run_id:'ta',tool_name:'search_knowledge_base'}),ev('tool_error',{run_id:'ta',error:'失败'}),ev('tool_start',{...main,run_id:'m',tool_name:'generate_markdown'}),ev('error',{})],{filter:'errors'})
  assert.equal(trace.children[0].children[0].status,'error')
  assert.equal(trace.children.at(-1).children.at(-1).type,'error')
})

test('stages remain visible while tool and return details start collapsed',()=>{
  const trace=buildExecutionTrace(sequence)
  const details=trace.children.flatMap(s=>s.children).filter(n=>['tool','dispatch','return'].includes(n.type))
  assert.ok(details.length >= 3)
  assert.ok(details.every(n=>n.initialExpanded===false))
  assert.ok(trace.children.every(s=>s.stage))
})

const rows=trace=>trace.children.flatMap(stage=>stage.children)
const extraEvents=[
  ev('session_created'),
  ev('plan_created',{task_type:'qa',required_sources:['knowledge'],steps:['检索']}),
  ...sequence,
  ev('source_gap',{id:'G1',missing:'事实待核对',reason:'max_hops'}),
  ev('routing_decision',{status:'approved',reason:'允许补查',add_sources:['db_query']}),
  ev('reflection',{round:1,hard_issues:[],soft_review_status:'passed'}),
  ev('task_result',{degraded:false}),
]
test('all five supported selections have a matching label and accurate record count',()=>{
  assert.deepEqual(EXECUTION_FILTER_OPTIONS.map(option=>option.value),['all','main','assistant','tools','errors'])
  for (const option of EXECUTION_FILTER_OPTIONS) {
    const trace=buildExecutionTrace(extraEvents,{filter:option.value})
    assert.equal(trace.label,option.traceLabel)
    assert.equal(trace.recordCount,rows(trace).length)
  }
  assert.ok(rows(buildExecutionTrace(extraEvents)).some(n=>n.type==='result'))
})
test('main-only keeps orchestration and its own calls while excluding all child execution',()=>{
  const mainTrace=buildExecutionTrace(extraEvents,{filter:'main'})
  assert.ok(mainTrace.children.length >= 2)
  assert.ok(mainTrace.children.every(stage=>stage.type==='main'))
  assert.ok(rows(mainTrace).some(n=>n.type==='dispatch'))
  assert.ok(rows(mainTrace).some(n=>n.type==='return'))
  assert.ok(rows(mainTrace).some(n=>n.type==='result'))
  assert.ok(!rows(mainTrace).some(n=>n.label==='search_knowledge_base' || n.label.startsWith('知识库第')))
})
test('child-only excludes global planning, routing, checks, result and parent delegation',()=>{
  const trace=buildExecutionTrace(extraEvents,{filter:'assistant'})
  assert.ok(trace.children.every(stage=>stage.type==='assistant'))
  assert.ok(rows(trace).some(n=>n.label==='search_knowledge_base'))
  assert.ok(rows(trace).some(n=>n.label.startsWith('知识库第')))
  assert.ok(!rows(trace).some(n=>['dispatch','return','session','result'].includes(n.type)))
  assert.ok(!rows(trace).some(n=>/任务计划|路由评估|质量校验|证据缺口/.test(n.label)))
  assert.ok(trace.recordCount < buildExecutionTrace(extraEvents).recordCount)
})
test('tool-only strictly contains tool records and their completed output',()=>{
  const trace=buildExecutionTrace(extraEvents,{filter:'tools'})
  assert.equal(trace.recordCount,1)
  assert.equal(rows(trace)[0].type,'tool')
  assert.equal(rows(trace)[0].summary,'原文')
  assert.equal(rows(trace)[0].duration,'2.0s')
})
test('normal completed task has no anomalies and emits no empty actor group',()=>{
  const trace=buildExecutionTrace(extraEvents,{filter:'errors'})
  assert.equal(trace.recordCount,0)
  assert.deepEqual(trace.children,[])
})
test('error-only captures failures, warning outcomes and degraded results without normal work',()=>{
  const trace=buildExecutionTrace([
    ev('agent_activity',{...main,phase:'start',run_id:'ok'}),
    ev('agent_activity',{...main,phase:'end',run_id:'ok'}),
    ev('tool_start',{...child('d'),run_id:'failed',tool_name:'search_knowledge_base'}),
    ev('tool_end',{run_id:'failed',summary:'知识库检索失败：服务不可用'}),
    ev('retrieval_summary',{...child('d'),stop_reason:'max_hops',strategy:'multi',missing:['条款缺失']}),
    ev('routing_decision',{status:'unavailable',reason:'规划服务失败'}),
    ev('reflection',{round:1,hard_issues:[],soft_review_status:'skipped'}),
    ev('task_result',{degraded:true}),
  ],{filter:'errors'})
  assert.equal(trace.recordCount,5)
  assert.ok(rows(trace).some(n=>n.type==='tool' && n.status==='error'))
  assert.ok(rows(trace).some(n=>n.type==='result' && n.severity==='warning'))
  assert.ok(rows(trace).every(n=>n.status==='error' || n.severity==='warning'))
  assert.ok(!rows(trace).some(n=>n.type==='activity'))
})
test('anomalies include interrupted and unclosed calls instead of normal success',()=>{
  for (const ending of ['error','task_result']) {
    const trace=buildExecutionTrace([ev('tool_start',{...main,run_id:'unfinished',tool_name:'read_file_content'}),ev(ending)],{filter:'errors'})
    assert.ok(rows(trace).some(n=>n.type==='tool' && n.status===(ending==='error' ? 'interrupted' : 'unknown')))
  }
})
test('error text mentioned in successful evidence is not itself an execution failure',()=>{
  const trace=buildExecutionTrace([ev('tool_start',{...child('d'),run_id:'ok',tool_name:'search_knowledge_base'}),ev('tool_end',{run_id:'ok',summary:'[证据 S1] 排障文档解释“知识库检索失败”的原因'})],{filter:'errors'})
  assert.equal(trace.recordCount,0)
})
test('unattributed legacy records appear in all/tools but not in either actor-only view',()=>{
  const events=[
    ev('assistant_call',{run_id:'a',assistant_name:'内部知识库助手'}),
    ev('assistant_call',{run_id:'b',assistant_name:'内部知识库助手'}),
    ev('tool_start',{run_id:'unknown',tool_name:'search_knowledge_base'}),
    ev('tool_end',{run_id:'unknown',summary:'返回原文'}),
  ]
  const all=buildExecutionTrace(events)
  assert.ok(all.children.some(stage=>stage.type==='unknown'))
  assert.equal(buildExecutionTrace(events,{filter:'assistant'}).recordCount,0)
  assert.ok(!rows(buildExecutionTrace(events,{filter:'main'})).some(n=>n.type==='tool'))
  assert.equal(buildExecutionTrace(events,{filter:'tools'}).recordCount,1)
})
test('repeated filters do not modify source events or contaminate other selections',()=>{
  const snapshot=JSON.stringify(extraEvents)
  const initial=buildExecutionTrace(extraEvents)
  for (const filter of ['assistant','main','errors','tools','all','assistant']) buildExecutionTrace(extraEvents,{filter})
  assert.deepEqual(buildExecutionTrace(extraEvents),initial)
  assert.equal(JSON.stringify(extraEvents),snapshot)
  assert.equal(buildExecutionTrace(extraEvents,{filter:'unrecognised'}).recordCount,0)
})
