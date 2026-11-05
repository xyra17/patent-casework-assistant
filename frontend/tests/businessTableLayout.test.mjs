import test from 'node:test'
import assert from 'node:assert/strict'
import {businessColumnWidths} from '../src/businessTableLayout.js'

test('a rare long value does not crowd typical business columns, while headers remain readable',()=>{
  const rows=Array.from({length:10},(_,i)=>[i+1,i===9?'x'.repeat(1000):'阿莫西林胶囊'])
  const result=businessColumnWidths(['id','name'],rows,n=>n==='id'?'药品ID':'通用名',s=>s.length*14)
  assert.equal(result.id,84)
  assert.equal(result.name,112)
  assert.ok(result.id<result.name)
  assert.ok(businessColumnWidths(['name'],[],()=> '非常长的业务字段标题',s=>s.length*14).name>=168)
})
test('width measurement preserves exact IDs and zero, handles null and caps long text',()=>{
  const measured=[]
  const result=businessColumnWidths(['id','zero','empty','note'],[['900719925474099312345',0,null,'文'.repeat(10000)]],n=>n,s=>{measured.push(s);return s.length*14})
  assert.ok(measured.includes('900719925474099312345'))
  assert.ok(measured.includes('0'))
  assert.equal(result.empty,98)
  assert.equal(result.note,320)
  assert.ok(measured.every(s=>s.length<=2048))
})
