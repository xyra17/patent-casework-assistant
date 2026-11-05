import test from 'node:test'
import assert from 'node:assert/strict'
import {DEFAULT_SOURCES,normalizeSources,readSourcePreference,saveSourcePreference,sourceSummary} from '../src/sourceSelection.js'
import {submitTask} from '../src/api.js'

test('defaults enable internal sources and empty selection stays empty',()=>{
  assert.deepEqual(normalizeSources(null),DEFAULT_SOURCES)
  assert.deepEqual(normalizeSources([]),[])
  assert.deepEqual(normalizeSources(['knowledge','web_search','bad','knowledge']),['knowledge','web_search'])
})
test('stored preferences never restore web permission',()=>{
  assert.deepEqual(readSourcePreference({getItem:()=>null}),DEFAULT_SOURCES)
  assert.deepEqual(readSourcePreference({getItem:()=>JSON.stringify(['knowledge','web_search'])}),['knowledge'])
  assert.deepEqual(readSourcePreference({getItem:()=>JSON.stringify([])}),[])
  assert.deepEqual(readSourcePreference({getItem:()=>'{broken'}),DEFAULT_SOURCES)
  assert.deepEqual(readSourcePreference({getItem:()=>JSON.stringify(['bad'])}),DEFAULT_SOURCES)
  assert.deepEqual(readSourcePreference({getItem:()=>{throw Error('disabled')}}),DEFAULT_SOURCES)
  let saved
  saveSourcePreference({setItem:(_,value)=>{saved=JSON.parse(value)}},['db_query','web_search'])
  assert.deepEqual(saved,['db_query'])
})
test('compact summary makes online and no-source mode explicit',()=>{
  assert.equal(sourceSummary([]),'仅当前材料')
  assert.equal(sourceSummary(DEFAULT_SOURCES),'内部来源 · 联网关')
  assert.equal(sourceSummary(['db_query']),'业务数据库 · 联网关')
  assert.equal(sourceSummary(['web_search']),'联网')
  assert.equal(sourceSummary([...DEFAULT_SOURCES,'web_search']),'内部来源 + 联网')
})
test('request transports explicit source settings independently of query text',async()=>{
  const original=globalThis.fetch
  let body
  globalThis.fetch=async(_,request)=>{body=JSON.parse(request.body);return {ok:true,json:async()=>({})}}
  try{
    await submitTask('统计订单','task')
    assert.deepEqual(body.allowed_sources,DEFAULT_SOURCES)
    await submitTask('统计订单','task','统计订单',false,3,null,[],'manual',[])
    assert.deepEqual(body.allowed_sources,[])
    assert.equal(body.query,'统计订单')
    await submitTask('查最新消息','task','查最新消息',false,3,null,[],'manual',['web_search'])
    assert.deepEqual(body.allowed_sources,['web_search'])
  }finally{globalThis.fetch=original}
})

test('answer binds a particular question and execution without changing sources or original query',async()=>{
  const original=globalThis.fetch
  let body
  globalThis.fetch=async(_,request)=>{body=JSON.parse(request.body);return {ok:true,json:async()=>({})}}
  try {
    const answer={execution_id:'prior',question_id:'question',choice:'workload_only',text:''}
    await submitTask('量子纠错谁接','task','量子纠错谁接',false,3,null,[],'auto',DEFAULT_SOURCES,answer)
    assert.deepEqual(body.clarification_answer,answer)
    assert.deepEqual(body.allowed_sources,DEFAULT_SOURCES)
    assert.equal(body.query,'量子纠错谁接')
    await submitTask('量子纠错谁接','task')
    assert.equal('clarification_answer' in body,false)
  } finally {globalThis.fetch=original}
})
