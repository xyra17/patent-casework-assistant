import test from 'node:test'
import assert from 'node:assert/strict'
import {reactive} from 'vue'
import {businessRowsWithKeys,selectedBusinessKeys} from '../src/businessRows.js'
test('selection keeps exact server primary keys across Vue row proxies and reversed selection',()=>{
 const rows=businessRowsWithKeys({columns:['id','label'],rows:[['9007199254740993','a'],[2,'b']],row_keys:[{id:'9007199254740993'},{id:2}]})
 const selected=reactive([rows[1],rows[0]])
 assert.notEqual(selected[0],rows[1])
 assert.deepEqual(selectedBusinessKeys(selected),[{id:2},{id:'9007199254740993'}])
 assert.equal(JSON.stringify(rows[0]),'{"id":"9007199254740993","label":"a"}')
})
test('selection without an exact server key fails before an API request',()=>{
 assert.throws(()=>selectedBusinessKeys([{id:1}]),/缺少主键/)
})
