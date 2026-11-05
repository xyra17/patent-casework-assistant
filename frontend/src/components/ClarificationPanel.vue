<template>
  <section class="clarification" aria-labelledby="clarification-title">
    <div class="clarification-heading"><el-tag type="warning" effect="plain">待你确认</el-tag><h2 id="clarification-title">{{ question.title }}</h2></div>
    <p v-if="question.kind !== 'recommendation_evidence' && question.message">{{ question.message }}</p>
    <dl class="search-summary">
      <template v-if="question.technical_query"><dt>查找内容</dt><dd>{{ question.technical_query }}</dd></template>
      <dt>{{ ['archive_scope','task_definition'].includes(question.kind)?'口径说明':'查找结果' }}</dt><dd>{{ question.detail }}</dd>
    </dl>
    <section v-if="question.kind==='archive_scope'" class="next-steps" aria-labelledby="scope-next-title">
      <h3 id="scope-next-title">{{ question.question }}</h3>
      <div class="supplement-actions">
        <el-button v-for="option in question.options" :key="option.id" :disabled="session.preparing" @click="answer(option.id)">{{ option.label }}</el-button>
      </div>
      <p class="scope-note">选择仅用于本次任务；确认前暂停案件计数、技术经历判断和接案推荐。</p>
    </section>
    <template v-else>
    <p v-if="question.kind==='recommendation_evidence'" class="lookup-note">本次只查了系统目前可查询的资料，不能据此认定某位同事没有这类经验。</p>
    <section class="next-steps" aria-labelledby="clarification-next-title">
      <header class="next-steps-heading">
        <h3 id="clarification-next-title"><label :for="`clarification-${session.thread_id}`">{{ question.kind==='task_definition'?'补充统计口径':'补充线索' }}</label></h3>
        <span :id="`clarification-hint-${session.thread_id}`" class="resume-note">{{ question.kind==='task_definition'?question.question:'提供技术点、历史案号，或上传相关文件，任选一种即可。' }}</span>
      </header>
      <div class="supplement-form">
        <el-input :id="`clarification-${session.thread_id}`" v-model="text" :aria-describedby="`clarification-hint-${session.thread_id}`" type="textarea" :rows="3" maxlength="4000" show-word-limit :placeholder="question.kind==='task_definition'?'填写本次统计范围或字段含义':'例如：技术点是向量数据库；参考案号是……；或已上传相关文件，请查看第……节。'" />
        <div class="supplement-actions">
          <el-button link type="primary" @click="emit('show-files')"><el-icon aria-hidden="true"><Paperclip /></el-icon>上传本会话附件</el-button>
          <el-button type="primary" :disabled="!text.trim() || session.preparing" @click="answer('supplement')">{{ question.kind==='task_definition'?'补充后继续':'补充后继续查找' }}</el-button>
        </div>
        <p class="scope-note">补充内容仅用于本次任务，查询范围保持不变。</p>
      </div>
      <div v-if="canViewWorkload" class="alternative-action">
        <div class="alternative-copy"><h4>也可以先了解当前工作量</h4><p>查看在办案件和到期期限，暂不推荐接案人。</p></div>
        <el-button :disabled="session.preparing" @click="answer('workload_only')">查看在办案件<el-icon aria-hidden="true"><ArrowRight /></el-icon></el-button>
      </div>
    </section>
    </template>
    <button v-if="question.lookup_evidence_ids?.length" type="button" class="lookup-sources" @click="emit('show-evidence',question.lookup_evidence_ids[0])">查看找到的相关材料（{{ question.lookup_evidence_ids.length }} 条来源）</button>
  </section>
</template>
<script setup>
import {computed} from 'vue'
import {Paperclip,ArrowRight} from '@element-plus/icons-vue'
import {clarificationPresentation} from '../clarificationPresentation.js'
const props=defineProps({session:{type:Object,required:true}})
const emit=defineEmits(['answer','show-files','show-evidence'])
const question=computed(()=>clarificationPresentation(props.session.clarification))
const canViewWorkload=computed(()=>question.value.options?.some(x=>x.id==='workload_only'))
const text=computed({
  get:()=>props.session.clarification_draft?.question_id===question.value?.id ? props.session.clarification_draft.text : '',
  set:value=>{props.session.clarification_draft={question_id:question.value.id,text:value}},
})
function answer(choice){emit('answer',{execution_id:props.session.execution_id,question_id:question.value.id,choice,text:choice==='supplement'?text.value.trim():''})}
</script>
<style scoped>
.clarification {height:100%;overflow:auto;box-sizing:border-box;padding:18px 14px 24px 0;font-size:14px;line-height:1.7;}
.clarification-heading {display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:16px;}
h2 {font-size:18px;line-height:1.4;margin:0;color:var(--text);}
h3 {font-size:15px;line-height:1.5;margin:0;color:var(--text);}
p {margin:8px 0;overflow-wrap:anywhere;}
.lookup-note,.supplement-actions {color:var(--text-secondary);font-size:13px;}
.search-summary {display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 14px;margin:14px 0 8px;padding:12px 14px;border-radius:8px;background:var(--bg);}
.search-summary dt {color:var(--chart-muted);}
.search-summary dd {margin:0;color:var(--text);overflow-wrap:anywhere;white-space:pre-wrap;}
.next-steps {border-top:1px solid var(--border);margin-top:22px;padding-top:20px;}
.next-steps-heading {display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:4px 16px;margin-bottom:18px;}
.resume-note {font-size:12px;color:var(--text-secondary);}
label {display:block;font-weight:600;margin:0;color:var(--text);}
.supplement-form :deep(.el-textarea__inner) {font:inherit;line-height:1.65;padding:10px 12px;}
.supplement-actions {display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-top:12px;}
.supplement-actions :deep(.el-button + .el-button) {margin-left:0;}
.supplement-actions :deep(.el-icon) {margin-right:5px;}
.scope-note {font-size:12px;color:var(--text-secondary);margin:8px 0 0;}
.alternative-action {display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px 16px;margin-top:18px;padding:16px 0;border-top:1px solid var(--border);}
.alternative-copy {flex:1 1 200px;}
.alternative-copy h4 {font-size:14px;font-weight:500;color:var(--text);margin:0;}
.alternative-copy p {font-size:13px;color:var(--text-secondary);margin:3px 0 0;}
.alternative-action :deep(.el-icon) {margin-left:6px;}
.lookup-sources {padding:0;border:0;background:transparent;color:var(--primary);cursor:pointer;font:inherit;}
.lookup-sources:focus-visible {outline:2px solid var(--primary);outline-offset:4px;}
@media (max-width:640px) {.search-summary {gap:7px 10px;padding:10px;}.next-steps-heading {align-items:flex-start;flex-direction:column;}.supplement-actions {align-items:center;}.alternative-action {align-items:flex-start;}}
</style>
