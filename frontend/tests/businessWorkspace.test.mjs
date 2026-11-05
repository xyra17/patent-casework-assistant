import test from 'node:test'
import assert from 'node:assert/strict'
import {restoreBrowse,mergeStructure,readWorkspace,saveWorkspace} from '../src/businessWorkspace.js'
test('restores applied filters separately from draft and rejects deleted fields and oversized offsets',()=>{
 const tables=[{name:'sales',columns:[{name:'region'}]}]
 const saved={table:'sales',field:'region',op:'eq',value:'draft',filters:[{field:'region',op:'eq',value:'actual'}],page:3,pageSize:20}
 const restored=restoreBrowse(saved,tables);assert.equal(restored.value,'draft');assert.equal(restored.filters[0].value,'actual');assert.equal(restored.page,3)
 assert.equal(restoreBrowse({...saved,filters:[{field:'deleted',op:'eq',value:'x'}]},tables).page,1)
 assert.equal(restoreBrowse({...saved,page:10000,pageSize:100},tables).page,1)
 assert.equal(restoreBrowse({...saved,table:'deleted'},tables),null)
})
test('physical metadata stays distinct from semantic definitions and missing objects remain visible',()=>{
 const semantic={items:{columns:{id:{label:'编号'},removed:{label:'旧字段'}}},old:{columns:{id:{}}}}
 const result=mergeStructure(semantic,{tables:[{name:'items',columns:[{name:'id',type:'bigint',semantic_configured:true},{name:'fresh',type:'int',default:'0',semantic_configured:false}]}]})
 assert.equal(result.items.columns.id.physical.type,'bigint');assert.equal(result.items.columns.fresh.configured,false);assert.equal(result.items.columns.removed.exists,false);assert.equal(result.old.exists,false)
 assert.equal(mergeStructure(semantic,null).old.exists,null)
})
test('storage corruption and unavailable storage do not break navigation',()=>{
 globalThis.sessionStorage={getItem:()=>'{broken',setItem:()=>{throw Error('disabled')}}
 assert.equal(readWorkspace('browse'),null);assert.doesNotThrow(()=>saveWorkspace('tab','browse'))
 const data=new Map();globalThis.sessionStorage={getItem:k=>data.get(k),setItem:(k,v)=>data.set(k,v)}
 saveWorkspace('tab','semantic');saveWorkspace('browse',{table:'items'});assert.equal(readWorkspace('tab'),'semantic');assert.equal(readWorkspace('browse').table,'items')
 delete globalThis.sessionStorage
})
