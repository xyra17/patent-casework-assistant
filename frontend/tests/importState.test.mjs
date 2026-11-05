import test from 'node:test'
import assert from 'node:assert/strict'
import {importFileError} from '../src/importState.js'
import {getDataImport,commitImport,validateImport} from '../src/api.js'

test('import file validation handles both drop and picker limits',()=>{
 assert.equal(importFileError({name:'业务.XLSX',size:20*1024*1024}),'')
 for(const name of ['a.xls','a.csv'])assert.equal(importFileError({name,size:2}),'')
 assert.match(importFileError({name:'a.pdf',size:2}),/请选择/)
 assert.match(importFileError({name:'a.csv',size:0}),/空文件/)
 assert.match(importFileError({name:'a.csv',size:20*1024*1024+1}),/20 MB/)
})
test('catalog, validation, and commit pass the chosen connection explicitly',async()=>{
 const original=globalThis.fetch,calls=[]
 globalThis.fetch=async(url,opts={})=>{calls.push({url,body:opts.body?JSON.parse(opts.body):null});return new Response('{}',{status:200})}
 try{
  await getDataImport('branch_a');await validateImport('batch',{connection_id:'branch_a'});await commitImport('batch','token','branch_a')
  assert.equal(calls[0].url,'/api/data-import?connection_id=branch_a')
  assert.equal(calls[1].body.connection_id,'branch_a');assert.equal(calls[2].body.connection_id,'branch_a')
  assert.equal(calls[2].body.validation_token,'token')
 }finally{globalThis.fetch=original}
})
