<template>
  <section class="execution-history" aria-label="执行记录">
    <div class="history-toolbar">
      <el-select v-model="selected" placeholder="当前执行" aria-label="选择执行记录" @change="inspect">
        <el-option value="" label="当前执行" />
        <el-option v-for="item in executions" :key="item.execution_id" :value="item.execution_id" :label="`${formatTime(item.created_at)} · ${statusLabel(item.status,item.degraded)}`" />
      </el-select>
      <el-checkbox v-if="selected" v-model="compare" :disabled="detailLoading">与当前对比</el-checkbox>
      <div class="toolbar-actions">
        <el-tooltip content="重跑会保留此前的回答、计划和来源。删除会话会清理所有执行文件；删除附件会清理各轮输入副本。" placement="bottom" :show-after="200">
          <button type="button" class="help-button" aria-label="执行记录与文件保留说明"><el-icon><QuestionFilled /></el-icon></button>
        </el-tooltip>
        <el-button :icon="Refresh" :loading="loading" size="small" @click="load(false)">刷新</el-button>
      </div>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="detailLoading" class="history-hint" role="status">正在读取执行记录…</p>
    <template v-else-if="!selected || historical">
      <article class="execution-detail">
        <header class="execution-heading">
          <h3>{{ historical ? '历史执行' : '当前执行' }}</h3>
          <el-tag size="small" :type="activeExecution.status === 'failed' ? 'danger' : activeExecution.degraded ? 'warning' : activeExecution.status === 'completed' ? 'success' : 'info'">{{ statusLabel(activeExecution.status,activeExecution.degraded) }}</el-tag>
          <time>{{ formatTime(historical ? historical.created_at : session?.started_at) }}</time>
        </header>
        <p class="history-hint">{{ assessmentText(activeExecution) }}</p>
        <div v-if="activeExecution.result" class="result-markdown" v-html="renderResult(activeExecution.result)"></div>
        <p v-else class="history-hint">{{ activeExecution.status === 'failed' ? '本次执行失败，未生成回答。' : activeExecution.status === 'running' ? '执行中，尚未生成回答。' : '本次执行没有回答。' }}</p>
        <details v-if="activeExecution.error" class="failure-detail"><summary>{{ failureLabel(activeExecution.error) }} · 查看详情</summary><p class="error">{{ activeExecution.error }}</p></details>
      </article>
      <article v-if="compare && historical" class="execution-detail compare-current">
        <header class="execution-heading"><h3>当前执行</h3><el-tag size="small" :type="session?.status === 'failed' ? 'danger' : session?.degraded ? 'warning' : session?.status === 'completed' ? 'success' : 'info'">{{ statusLabel(session?.status,session?.degraded) }}</el-tag><time>{{ formatTime(session?.started_at) }}</time></header>
        <p class="history-hint">{{ assessmentText(session) }}</p>
        <div v-if="session?.result" class="result-markdown" v-html="renderResult(session.result)"></div>
        <p v-else class="history-hint">当前执行尚无回答。</p>
      </article>
      <div class="execution-supplements">
        <p v-if="compare && historical" class="history-hint">以下为所选历史执行的资料</p>
        <details><summary>任务与计划<span>{{ activeExecution.plan?.steps?.length || 0 }} 个步骤</span></summary>
          <div class="supplement-body"><p class="task-query">{{ activeExecution.execution_query || activeExecution.task_query || activeExecution.query || '未记录任务问题' }}</p><p class="history-hint">来源设置：{{ activeExecution.allowed_sources == null ? '旧执行未记录' : sourceSummary(activeExecution.allowed_sources) }}</p><p>{{ activeExecution.plan?.intent_summary || '未记录任务理解' }}</p><ol v-if="activeExecution.plan?.steps?.length"><li v-for="(step,index) in activeExecution.plan.steps" :key="index">{{ step }}</li></ol></div>
        </details>
        <details><summary>来源<span>{{ activeExecution.evidence?.length || 0 }} 条</span></summary>
          <div class="supplement-body"><p v-if="!activeExecution.evidence?.length" class="history-hint">本次没有来源记录。</p><div v-for="item in activeExecution.evidence" :key="item.id" class="source"><strong>{{ item.id }} · {{ evidenceTitle(item) }}</strong><p>{{ item.snippet || '未记录来源摘录' }}</p></div></div>
        </details>
        <details><summary>文件清单<span>{{ (manifest?.inputs?.length || 0) + (manifest?.artifacts?.length || 0) }} 个</span></summary>
          <div class="supplement-body">
            <p v-if="manifest?.legacy" class="history-hint">旧执行仅展示现存文件，无法恢复当时完整的输入版本。</p>
            <p v-if="!manifest" class="history-hint">文件清单暂不可用，可刷新重试。</p>
            <p v-else-if="!manifest.inputs?.length && !manifest.artifacts?.length" class="history-hint">本次没有附件或生成文件。</p>
            <div v-for="file in manifest?.inputs || []" :key="`input-${file.name}`" class="manifest-row"><span>输入 · {{ file.name }}{{ file.deleted ? '（副本已删除）' : '' }}</span><code :title="file.sha256">{{ file.sha256?.slice(0,12) }}</code></div>
            <div v-for="file in manifest?.artifacts || []" :key="`output-${file.name}`" class="manifest-row"><span>{{ manifest.legacy ? '历史文件' : '生成' }} · {{ file.name }}{{ file.deleted ? '（已删除）' : '' }}</span><el-button link type="primary" :disabled="file.deleted" @click="download(file)">下载</el-button></div>
          </div>
        </details>
      </div>
    </template>
    <footer class="history-footer"><span v-if="!executions.length && !loading">重跑后，可在此查看此前的执行。</span><el-button v-if="nextOffset !== null" :loading="loading" size="small" @click="load(true)">加载更早记录</el-button></footer>
  </section>
</template>
<script setup>
import {computed,ref,watch} from 'vue'
import {Refresh,QuestionFilled} from '@element-plus/icons-vue'
import {evidenceTitle} from '../sourcePresentation.js'
import {parseResultMarkdown} from '../resultMarkdown'
import DOMPurify from 'dompurify'
import {sourceSummary} from '../sourceSelection'
import {resultAssessmentItems} from '../resultAssessment'
import {listExecutions,getExecution,downloadFile} from '../api'
const props=defineProps({session:Object})
const executions=ref([]),selected=ref(''),historical=ref(null),manifest=ref(null),nextOffset=ref(null),loading=ref(false),error=ref('')
const compare=ref(false),detailLoading=ref(false)
const activeExecution=computed(()=>historical.value || props.session || {})
const renderResult=value=>DOMPurify.sanitize(parseResultMarkdown(value))
const assessmentText=value=>resultAssessmentItems(value).map(item=>`${item.label}：${item.status}`).join('；')
const failureLabel=value=>/insufficient.?balance|余额不足/i.test(value) ? '模型服务余额不足' : /timeout|超时/i.test(value) ? '执行超时' : '失败原因'
let generation=0,detailGeneration=0
const formatTime=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false}):''
const statusLabel=(value,degraded=false)=>value==='completed'&&degraded?'未达标':({running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消',awaiting_input:'待你确认'}[value]||value)
watch(()=>[props.session?.thread_id,props.session?.execution_id,props.session?.status],()=>load(false),{immediate:true})
async function load(more){
 const id=props.session?.thread_id;if(!id)return
 const token=++generation;if(!more)++detailGeneration;loading.value=true;error.value=''
 if(!more){selected.value='';historical.value=null;manifest.value=null;executions.value=[];compare.value=false;detailLoading.value=false}
 try{
  const data=await listExecutions(id,more?(nextOffset.value||0):0)
  if(token!==generation)return
  executions.value=more?[...executions.value,...data.executions]:data.executions
  nextOffset.value=data.next_offset??null
  if(!more&&data.current?.execution_id){const detailToken=detailGeneration;const detail=await getExecution(id,data.current.execution_id);if(token===generation&&detailToken===detailGeneration)manifest.value=detail.manifest}
 }catch(e){if(token===generation)error.value=e.message}finally{if(token===generation)loading.value=false}
}
async function inspect(){
 const token=++detailGeneration,id=props.session?.thread_id,execution=selected.value || props.session?.execution_id
 error.value='';historical.value=null;manifest.value=null;detailLoading.value=true
 if(!selected.value)compare.value=false
 try{
  if(!execution)return
  const data=await getExecution(id,execution)
  if(token===detailGeneration&&id===props.session?.thread_id){
   if(selected.value)historical.value=data
   manifest.value=data.manifest
  }
 }catch(e){if(token===detailGeneration)error.value=e.message}
 finally{if(token===detailGeneration)detailLoading.value=false}
}
async function download(file){
 try{const response=await downloadFile(file.path,props.session.thread_id);const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=file.name.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){error.value=e.message}
}
</script>
<style scoped>
.execution-history{height:100%;overflow:auto;padding:4px 0 16px;box-sizing:border-box;font-size:14px;line-height:1.65;container-type:inline-size}
.history-toolbar{display:flex;align-items:center;gap:12px;padding:8px 0 14px;border-bottom:1px solid var(--el-border-color-lighter);flex-wrap:wrap}
.history-toolbar .el-select{width:280px;max-width:100%;flex:0 1 280px}
.history-toolbar .el-checkbox{margin-right:0;height:32px}
.toolbar-actions{display:flex;align-items:center;gap:8px;margin-left:auto}
.help-button{border:0;background:transparent;color:var(--el-text-color-secondary);display:flex;align-items:center;justify-content:center;width:28px;height:28px;cursor:help;font-size:16px;border-radius:5px}
.help-button:focus-visible{outline:2px solid var(--el-color-primary);outline-offset:2px}
.execution-detail{padding:18px 0 12px;min-width:0}
.execution-heading{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.execution-heading h3{font-size:15px;margin:0;line-height:1.5}
.execution-heading time{font-size:12px;color:var(--el-text-color-secondary);margin-left:auto}
.result-markdown{font-size:14px;color:var(--el-text-color-primary);line-height:1.8;overflow-wrap:anywhere}
.result-markdown :deep(p){margin:0 0 12px}.result-markdown :deep(h1),.result-markdown :deep(h2),.result-markdown :deep(h3){font-size:16px;margin:18px 0 10px;line-height:1.5}
.result-markdown :deep(pre){overflow:auto;padding:12px;background:var(--el-fill-color-light);border-radius:6px;font-size:12px}
.result-markdown :deep(img){max-width:100%}.result-markdown :deep(blockquote){margin:12px 0;padding-left:12px;border-left:3px solid var(--el-border-color);color:var(--el-text-color-secondary)}
.compare-current{border-top:1px dashed var(--el-border-color);margin-top:4px}
.execution-supplements{border-top:1px solid var(--el-border-color-lighter)}
.execution-supplements details{border-bottom:1px solid var(--el-border-color-lighter)}
summary{cursor:pointer;padding:12px 0;font-size:13px;font-weight:500}summary span{font-size:12px;font-weight:400;color:var(--el-text-color-secondary);margin-left:10px}
.supplement-body{padding:0 0 12px;overflow-wrap:anywhere}.supplement-body p{margin:0 0 10px}.task-query{white-space:pre-wrap}.source+.source{border-top:1px solid var(--el-border-color-lighter);padding-top:10px}
.manifest-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 0;overflow-wrap:anywhere}.manifest-row>span{min-width:0}.manifest-row code{font-size:11px;color:var(--el-text-color-secondary);flex-shrink:0}
.failure-detail summary{padding:4px 0 8px;color:var(--el-color-danger)}
.history-hint,.history-footer{color:var(--el-text-color-secondary);font-size:13px}.history-footer{display:flex;justify-content:flex-end;align-items:center;gap:12px;margin-top:14px}.error{color:var(--el-color-danger);font-size:13px;overflow-wrap:anywhere}
@container(max-width:480px){.history-toolbar{gap:8px}.history-toolbar .el-select{flex:1 1 220px}.execution-heading time{width:100%;margin-left:0}.toolbar-actions{gap:4px}}
</style>
