import test from 'node:test'
import assert from 'node:assert/strict'
import {executionResultContent,answerSourceItems} from '../src/previewState.js'

test('ordinary results use the final body, including legacy, failed and degraded results, without an excerpt',()=>{
 const session={result:'结论未核实[S1]。\n\n完整表格与细节[S2]',query_result:{content:'旧摘录',evidence_ids:['S1']},plan:{task_type:'analysis'},evidence:[{id:'S1'},{id:'S2'},{id:'S3'}]}
 for(const change of [{},{query_result:null},{query_result:{status:'unavailable'}},{degraded:true},{status:'failed'}]){
  assert.equal(executionResultContent({...session,...change}),session.result)
 }
 assert.deepEqual(answerSourceItems(session),session.evidence.slice(0,2))
 assert.equal(executionResultContent(null),'')
})

test('report and PDF tasks show their complete body directly in execution results',()=>{
 for(const change of [{plan:{require_report:true}},{plan:{require_pdf:true}},{require_pdf:true}]){
  const session={result:'报告全文[S1]\n\n## 详细分析\n完整内容与表格[S2]',...change}
  assert.equal(executionResultContent(session),session.result)
 }
 assert.equal(executionResultContent({plan:{require_report:true}}),'')
})

test('result header describes actual artifacts rather than calling every non-PDF result text',async()=>{
 const {resultOutputSummary}=await import('../src/previewState.js')
 assert.equal(resultOutputSummary({}), '')
 assert.equal(resultOutputSummary({chartCount:3}), '含 3 张图表')
 assert.equal(resultOutputSummary({pdfRequired:true,status:'failed'}),'暂无 PDF 文件')
 assert.equal(resultOutputSummary({pdfRequired:true,status:'running'}),'PDF 待生成')
 assert.equal(resultOutputSummary({pdfRequired:true,pdfUrl:'/current.pdf',chartCount:2}),'已生成 PDF · 含 2 张图表')
})
