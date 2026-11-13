import test from 'node:test'
import assert from 'node:assert/strict'
import {submitTask,getConversation,getConversationTurn,draftTurnRule} from '../src/api.js'

test('follow-up carries parent execution and turn tokens without expanding sources',async()=>{
  const before=globalThis.fetch,requests=[]
  globalThis.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({status:'started',turn_id:'turn-2'})}}
  try{
    const followup={text:'第二个案件的依据呢？',expected_execution_id:'exec-1',expected_turn_id:'turn-1',mode:'continue'}
    const result=await submitTask('原任务','chat','原任务',false,3,null,[],'manual',['knowledge'],null,null,followup)
    assert.equal(result.turn_id,'turn-2')
    assert.deepEqual(requests[0].body.followup,followup)
    assert.deepEqual(requests[0].body.allowed_sources,['knowledge'])
  }finally{globalThis.fetch=before}
})

test('conversation pagination and turn reads encode identifiers and preserve snapshot response',async()=>{
  const before=globalThis.fetch,urls=[]
  const snapshot={turn_id:'old',snapshot:{result:'原回答',evidence:[{id:'S1',revision:'v1'}]}}
  globalThis.fetch=async url=>{urls.push(url);return {ok:true,json:async()=>snapshot}}
  try{
    await getConversation('chat/name',21)
    assert.equal(urls[0],'/api/runs/chat%2Fname/conversation?limit=20&before=21')
    assert.deepEqual(await getConversationTurn('chat/name','old/id'),snapshot)
    assert.equal(urls[1],'/api/runs/chat%2Fname/turns/old%2Fid')
  }finally{globalThis.fetch=before}
})

test('rule drafting from a conversation turn does not send confirmation or a revision',async()=>{
  const before=globalThis.fetch
  globalThis.fetch=async(url,options)=>{
    assert.equal(url,'/api/long-term-rules/draft')
    assert.deepEqual(JSON.parse(options.body),{thread_id:'chat',turn_id:'turn-3'})
    return {ok:true,json:async()=>({status:'suggested'})}
  }
  try{await draftTurnRule('chat','turn-3')}finally{globalThis.fetch=before}
})

test('clarification responses retain the error code for safe UI recovery',async()=>{
  const before=globalThis.fetch
  globalThis.fetch=async()=>({ok:false,status:409,json:async()=>({detail:{code:'conversation_clarification',message:'请提供具体案号'}})})
  try{
    await assert.rejects(submitTask('任务','chat'),error=>error.status===409 && error.detail.code==='conversation_clarification' && error.message==='请提供具体案号')
  }finally{globalThis.fetch=before}
})
