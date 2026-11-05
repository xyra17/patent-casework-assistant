import test from 'node:test'
import assert from 'node:assert/strict'
import {evidenceScopeText} from '../src/evidenceScope.js'

test('zero rows and unknown base count remain explicit',()=>{
 const text=evidenceScopeText({completeness:{returned_rows:0,query_rows:0,has_more:false,evidence_rows:0,model_rows:0}})
 assert.match(text,/已取回 0 行/);assert.match(text,/本次查询范围 0 行/);assert.match(text,/基础记录总数未知/)
})
test('group truncation, SQL top N, model and preserved ranges stay distinct',()=>{
 const text=evidenceScopeText({completeness:{scope:'semantic_groups',returned_rows:100,has_more:true,sql_limited:true,evidence_rows:100,model_rows:30,model_truncated:true}})
 for(const expected of ['100 组','还有未返回结果','SQL 含 LIMIT','模型展示 30 组（已截断）']) assert.ok(text.includes(expected))
 assert.doesNotMatch(text,/全量/)
})
test('legacy evidence and fallback recovery cannot imply completeness',()=>{
 assert.match(evidenceScopeText({type:'database',rows:[[1]]}),/未记录/)
 assert.equal(evidenceScopeText({type:'knowledge'}),'')
 assert.match(evidenceScopeText({completeness:{returned_rows:100,evidence_rows:30,evidence_truncated:true}}),/来源仅保存部分输出/)
})
