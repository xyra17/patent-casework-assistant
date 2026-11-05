import test from 'node:test'
import assert from 'node:assert/strict'
import {needsStatisticsValidation,statisticsNotice} from '../src/statisticalValidation.js'
import {reflectionDetails} from '../src/reflection.js'

test('old database projections must be checked before display; other sources keep their behavior',()=>{
 assert.equal(needsStatisticsValidation({query_result:{content:'100万元'},evidence:[{type:'database'}]}),true)
 assert.equal(needsStatisticsValidation({query_result:{statistical_validation_version:1},evidence:[{type:'database'}]}),false)
 assert.equal(needsStatisticsValidation({query_result:{},evidence:[{type:'knowledge'}]}),false)
})
test('failed and unavailable statistical checks explain why a short answer is hidden',()=>{
 assert.match(statisticsNotice({statistics_check:{status:'failed',issues:['时间不符']}}),/时间不符/)
 assert.match(statisticsNotice({statistics_check:{status:'unverified',detail:'服务不可用'}}),/服务不可用/)
 assert.equal(statisticsNotice({statistics_check:{status:'passed'}}),'')
})
test('sealed fixed business results display without requesting a second statistics extraction',()=>{
 const session={result:'当前核对结果，日期未核实。',evidence:[{type:'database'}],query_result:{
  content:'当前核对结果，日期未核实。',mode:'verified_business',status:'ready',business_validation_version:1,business_check:{status:'passed'}}}
 assert.equal(needsStatisticsValidation(session),false)
})
test('a business marker cannot hide missing proof, changed content or degraded state',()=>{
 const session={result:'完整结果，未核实。',evidence:[{type:'database'}],query_result:{
  content:'完整结果，未核实。',mode:'verified_business',status:'ready',business_validation_version:1,business_check:{status:'passed'}}}
 for(const patch of [{business_check:null},{status:'unavailable'},{content:'改写后删掉限制'},{mode:'extractive'}]){
  assert.equal(needsStatisticsValidation({...session,query_result:{...session.query_result,...patch}}),true)
 }
 assert.equal(needsStatisticsValidation({...session,degraded:true}),true)
})
test('reflection separates deterministic checks from model review and old history',()=>{
 const item=reflectionDetails({hard_issues:[],statistics_check:{status:'passed',checked_claims:2,issues:[]}}).items.find(i=>i.label==='关键统计核验')
 assert.equal(item.state,'passed');assert.match(item.detail,/2 项/)
 assert.equal(reflectionDetails({statistics_check:{status:'unverified',checked_claims:0,issues:['提取失败']}}).items.find(i=>i.label==='关键统计核验').status,'未核验')
 assert.equal(reflectionDetails({}).items.find(i=>i.label==='关键统计核验').state,'unknown')
})
