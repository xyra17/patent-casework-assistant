import test from 'node:test'
import assert from 'node:assert/strict'
import {listRuns,uploadFiles,cancelTask,listExecutions,getExecution} from '../src/api.js'
import {sessionOutputFiles} from '../src/sessionFiles.js'
import {buildExecutionTrace} from '../src/executionTrace.js'

test('history paging, tracked summary and execution requests remain explicit',async()=>{
 const previous=globalThis.fetch,urls=[]
 globalThis.fetch=async(url)=>{urls.push(url);return new Response('{}')}
 try{
  await listRuns(50,100,['first','second'])
  const page=new URL(urls[0],'http://local');assert.equal(page.searchParams.get('offset'),'100');assert.equal(page.searchParams.get('tracked'),'first,second')
  await listExecutions('session',50);assert.match(urls[1],/executions\?offset=50$/)
  await getExecution('session','version');assert.match(urls[2],/executions\/version$/)
 }finally{globalThis.fetch=previous}
})
test('attachment replacement requires an explicit list; cancellation returns authoritative server status',async()=>{
 const previous=globalThis.fetch;let body
 globalThis.fetch=async(url,options)=>{body=options.body;return new Response(JSON.stringify({status:'completed'}))}
 try{
  await uploadFiles('session',[],['same.txt']);assert.equal(body.get('replace'),'["same.txt"]')
  await uploadFiles('session',[]);assert.equal(body.get('replace'),'[]')
  assert.equal((await cancelTask('session')).status,'completed')
 }finally{globalThis.fetch=previous}
})
test('input snapshots never appear as generated reports even after attachment deletion',()=>{
 const root='/session/executions/current/work'
 const result=sessionOutputFiles([{type:'file',path:`${root}/attachments/report.md`},{type:'file',path:`${root}/report.md`}],[],root)
 assert.deepEqual(result.map(f=>f.path),[`${root}/report.md`])
})
test('cancel terminal event interrupts pending tools and is visible in the trace',()=>{
 const trace=buildExecutionTrace([{event:'tool_start',message:'running',data:{run_id:'tool',tool_name:'query'}},{event:'task_cancelled',message:'任务已终止',data:{stop_reason:'user_cancel'}}])
 assert.match(JSON.stringify(trace),/任务已终止/);assert.match(JSON.stringify(trace),/interrupted/)
})
test('global history counts include unloaded rows while tracking local starts, completion and deletion',async()=>{
 globalThis.localStorage={getItem:()=>null,setItem:()=>{}}
 const {store,setHistorySummary,sessionStats,sessionTotal}=await import('../src/store.js')
 store.sessions.splice(0,store.sessions.length,{thread_id:'a',status:'completed'},{thread_id:'new',status:'running'})
 setHistorySummary({total:60,stats:{running:0,completed:59,failed:1,degraded:0,cancelled:0},tracked:{a:'completed'}})
 assert.equal(sessionTotal(),61);assert.equal(sessionStats().running,1);assert.equal(sessionStats().completed,59)
 store.sessions[1].status='completed';assert.equal(sessionStats().completed,60)
 store.sessions.splice(0,1);assert.equal(sessionTotal(),60);assert.equal(sessionStats().completed,59)
 store.historyStats=null;store.historyBaseline={};store.sessions.splice(0)
})
