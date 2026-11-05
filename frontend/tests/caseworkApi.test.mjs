import test from 'node:test'
import assert from 'node:assert/strict'
import {casework,casePath} from '../src/caseworkApi.js'

test('case parameters preserve zero/false and encode identifiers without adding null filters',async()=>{
  const prior=global.fetch
  try{
    const abort=new AbortController()
    global.fetch=async(url,options)=>{
      assert.equal(url,'/api/casework/cases?offset=0&confirmed=false&query=%E5%AE%A2%E6%88%B7%E7%94%B2')
      assert.equal(options.signal,abort.signal)
      assert.equal(options.body,undefined)
      return {ok:true,json:async()=>({rows:[]})}
    }
    assert.deepEqual(await casework('/cases',{params:{offset:0,confirmed:false,query:'客户甲',owner:null,domain:''},signal:abort.signal}),{rows:[]})
    assert.equal(casePath('root:DEMO-2026-001'),'/cases/root%3ADEMO-2026-001')
  }finally{global.fetch=prior}
})

test('explicit confirmed mutation sends its snapshot and surfaces stale conflicts',async()=>{
  const prior=global.fetch
  try{
    const body={expected_version:'a'.repeat(64),confirmed:true,reason:'核对后确认'}
    global.fetch=async(url,options)=>{
      assert.equal(options.method,'POST');assert.equal(options.headers['Content-Type'],'application/json')
      assert.deepEqual(JSON.parse(options.body),body)
      return {ok:false,status:409,json:async()=>({detail:'数据已变化，请刷新'})}
    }
    await assert.rejects(()=>casework('/cases/root/assign',{method:'POST',body}),/数据已变化，请刷新/)
    global.fetch=async()=>({ok:false,status:503,json:async()=>{throw Error('not json')}})
    await assert.rejects(()=>casework('/overview'),/503/)
  }finally{global.fetch=prior}
})

test('invalid casework fields display the backend validation reason',async()=>{
  const prior=global.fetch
  try{
    global.fetch=async()=>({ok:false,status:422,json:async()=>({detail:[{msg:'Value error, 请填写技术描述'},{msg:'月份格式应为YYYY-MM'}]})})
    await assert.rejects(()=>casework('/recommendations',{method:'POST',body:{query:''}}),error=>{
      assert.equal(error.message,'请填写技术描述；月份格式应为YYYY-MM')
      assert.equal(error.status,422)
      return true
    })
  }finally{global.fetch=prior}
})
