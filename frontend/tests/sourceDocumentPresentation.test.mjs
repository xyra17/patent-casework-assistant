import test from 'node:test'
import assert from 'node:assert/strict'
import {sourceFieldRows} from '../src/sourceDocumentPresentation.js'

test('complete source rows keep exact values, including internal separators',()=>{
  assert.deepEqual(sourceFieldRows('我方案号 | DEMO-2026-012\n备注 | A | B <原文>'),[
    {label:'我方案号',value:'DEMO-2026-012'}, {label:'备注',value:'A | B <原文>'},
  ])
})
test('mixed prose and partial tables stay as the original text',()=>{
  for(const text of ['这是原文段落。','字段 | 值','技术说明\n字段 | 值','A | B\n继续上一行说明','| A | B |\n| C | D |',''])assert.equal(sourceFieldRows(text),null)
})
test('empty and whitespace-bearing field values are preserved',()=>{
  assert.deepEqual(sourceFieldRows('备注 | \n说明 | 保留尾空格  '),[
    {label:'备注',value:''},{label:'说明',value:'保留尾空格  '},
  ])
})
