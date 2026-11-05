import test from 'node:test'
import assert from 'node:assert/strict'
import {editableFields,rowChanges,operationIdentity} from '../src/businessRows.js'
import {getBusinessRow,mutateBusinessRow} from '../src/api.js'

test('row forms preserve exact money and large IDs, distinguish NULL from empty text, and exclude immutable fields',()=>{
 const record={columns:[{name:'id',editable:false},{name:'amount',editable:true},{name:'note',editable:true}],values:{id:'9007199254740993',amount:'900719925474099.01',note:null}}
 const fields=editableFields(record);assert.equal(fields.amount.value,'900719925474099.01');assert.deepEqual(rowChanges(record,fields),{})
 fields.id.value='2';fields.amount.value='900719925474099.02';fields.note.isNull=false
 assert.deepEqual(rowChanges(record,fields),{amount:'900719925474099.02',note:''})
 fields.note.isNull=true;assert.deepEqual(rowChanges(record,fields),{amount:'900719925474099.02'})
})
test('network retries preserve operation ID but changed data creates a new operation',()=>{
 const body={table:'demo',changes:{name:'A'},reason:'更正'}
 const first=operationIdentity(body,null,()=> 'first')
 assert.equal(operationIdentity({...body},first,()=>{throw Error('must reuse')}).id,'first')
 assert.equal(operationIdentity({...body,reason:'新原因'},first,()=> 'second').id,'second')
})
test('maintenance APIs use primary-key payloads and separate explicit mutation endpoints',async()=>{
 const original=globalThis.fetch;const calls=[]
 globalThis.fetch=async(url,opts)=>{calls.push({url,method:opts.method,body:JSON.parse(opts.body)});return new Response('{}',{status:200})}
 try{await getBusinessRow('demo',{id:'9007199254740993'});await mutateBusinessRow('edit',{confirmed:true});await mutateBusinessRow('delete',{confirmed:true});assert.equal(calls[0].body.key.id,'9007199254740993');assert.equal(calls[1].method,'PATCH');assert.equal(calls[2].url,'/api/business-database/delete-row')}finally{globalThis.fetch=original}
})

test('new records distinguish omitted defaults, explicit NULL, blank text and exact amounts',async()=>{
 const {creationValues}=await import('../src/businessRows.js')
 const record={columns:[{name:'id',insertable:false},{name:'note',insertable:true},{name:'empty',insertable:true},{name:'amount',insertable:true},{name:'created_at',insertable:true}]}
 assert.deepEqual(creationValues(record,{id:{value:'9007199254740993'},note:{value:'',isNull:true},empty:{value:'',isNull:false},amount:{value:'900719925474099.01'},created_at:{value:'CURRENT_TIMESTAMP',useDefault:true}}),{note:null,empty:'',amount:'900719925474099.01'})
 const original=globalThis.fetch
 globalThis.fetch=async(url,opts)=>{assert.equal(url,'/api/business-database/create-row');assert.equal(opts.method,'POST');return new Response('{}',{status:200})}
 try{await mutateBusinessRow('create',{table:'demo',values:{amount:'900719925474099.01'}})}finally{globalThis.fetch=original}
})
