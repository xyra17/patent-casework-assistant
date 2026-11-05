<template>
  <div class="understanding" tabindex="0" aria-label="任务理解，可上下滚动">
    <section class="original-task">
      <h3>原始问题</h3>
      <p class="task-copy">{{ session?.query || '尚未提交问题' }}</p>
    </section>
    <section v-if="applied" class="applied-task">
      <h3>本次执行采用的修订</h3>
      <p class="task-copy">{{ applied.revised_query }}</p>
    </section>
    <section v-if="session?.clarification_response" class="applied-task">
      <h3>本次采用的缺口处理选择</h3>
      <p class="task-copy">{{ clarificationChoiceText(session.clarification_response) }}</p>
      <p class="muted">仅本次执行生效，原问题与选择前的执行记录保留，不写入长期规则。</p>
    </section>
    <section class="source-access">
      <div class="section-head"><h3>数据源权限</h3><SourceSelector v-model="sourceDraft" :disabled="session?.status === 'running' || !session?.loaded" /></div>
      <p class="muted">本次允许：{{ session?.allowed_sources == null ? '旧执行未记录来源设置，重跑默认内部来源' : sourceLabels(session.allowed_sources) || '仅当前问题与会话附件' }}。模型在允许范围内按需检索，问题中的禁止要求仍有效。</p>
      <el-button v-if="sourcesChanged" size="small" type="primary" :disabled="session?.status === 'running'" @click="rerunWithSources">应用来源设置并重跑</el-button>
    </section>
    <section class="model-section">
      <div class="section-head"><h3>模型理解</h3><span class="muted">可核对的任务摘要与计划</span></div>
      <p v-if="session?.plan?.intent_summary" class="task-copy">{{ session.plan.intent_summary }}</p>
      <p v-else class="muted">{{ session?.plan ? '当前计划未提供意图摘要，请核对下方执行计划。' : session?.preparing ? '正在核对长期规则，确认后再生成执行计划。' : session?.status === 'running' ? '正在理解任务，计划生成后将在这里显示。' : '尚无执行计划。' }}</p>
      <details class="adopted-rules" v-if="session?.rule_context?.candidate_count">
        <summary>本次长期规则：采用 {{ session.rule_context.applied?.length || 0 }} 条，跳过 {{ session.rule_context.overridden?.length || 0 }} 条</summary>
        <article v-for="rule in session.rule_context.applied || []" :key="rule.id"><h4>{{ rule.title }} · v{{ rule.revision }}</h4><p class="task-copy">{{ rule.content }}</p><p class="muted">范围：{{ rule.scope_note }} · 采用原因：{{ rule.reason }}</p><p v-if="rule.recall" class="muted">召回：{{ rule.recall.channel === 'semantic' ? '语义相似' : rule.recall.channel === 'fallback_review' ? '语义服务不可用，扩大核对' : '关键词或别名' }}</p><p v-if="rule.local_overrides?.length" class="muted">本次局部调整（不修改长期规则）：{{ rule.local_overrides.map(x=>`${x.part} → ${typeof x.after==='object'?JSON.stringify(x.after):x.after}`).join('；') }}<br>当前要求：{{ rule.current_quote }}</p><p v-if="rule.binding" class="muted">结构化口径：{{ rule.binding.metric_key }}<span v-if="rule.binding.formula"> · {{ formulaLabel(rule.binding.formula) }}</span><span v-if="rule.binding.time_field"> · 时间 {{ rule.binding.time_field }}</span> · {{ rule.binding.filters.length }} 项默认过滤</p></article>
        <article v-for="rule in session.rule_context.overridden || []" :key="rule.id"><h4>本次跳过：{{ rule.title }} · v{{ rule.revision }}</h4><p class="muted">{{ rule.reason }}</p></article>
        <p class="muted">当前任务优先；规则不授予额外数据源权限。此处保留执行时的版本快照。</p>
      </details>
      <template v-if="session?.plan">
        <dl class="plan-facts">
          <div><dt>任务类型</dt><dd>{{ taskTypes[session.plan.task_type] || session.plan.task_type }}</dd></div>
          <div v-if="session.plan.round_budget"><dt>轮次预算</dt><dd>{{ session.plan.round_budget.mode === 'auto' ? '自动推荐' : '手动设置' }} · 最多 {{ session.plan.round_budget.max_rounds }} 轮<span v-if="session.plan.round_budget.mode === 'manual'">（参考推荐 {{ session.plan.round_budget.recommended_rounds }} 轮）</span><br />{{ session.plan.round_budget.reason }}</dd></div>
          <div><dt>计划使用</dt><dd>{{ sourceLabels(session.plan.required_sources) || '直接回答或使用会话附件' }}</dd></div>
          <div><dt>禁止使用</dt><dd>{{ sourceLabels(session.plan.forbidden_sources) || '未指定' }}</dd></div>
          <div v-if="session.plan.output_artifacts?.length"><dt>预期交付</dt><dd>{{ session.plan.output_artifacts.join('、') }}</dd></div>
        </dl>
        <ol v-if="session.plan.steps?.length" class="steps"><li v-for="(step, i) in planDisplaySteps(session.plan)" :key="i">{{ step }}</li></ol>
        <div v-if="session.plan.assumptions?.length" class="assumptions"><h4>待核对的假设</h4><ul><li v-for="(item, i) in session.plan.assumptions" :key="i">{{ item }}</li></ul></div>
      </template>
    </section>
    <section class="correction-section">
      <div class="section-head"><h3>理解有偏差？</h3><el-button v-if="!editing" size="small" @click="beginEdit">纠正理解</el-button></div>
      <p class="muted">修订随本会话保存，重跑时重新规划；不会自动应用到其他会话或写入知识库。</p>
      <p v-if="pending" class="pending" role="status">已有修订等待重跑；上方计划和结果仍属于当前执行版本。</p>
      <template v-if="editing">
        <label :for="`intent-${session.thread_id}`">修订后的完整任务</label>
        <p class="muted">请核对业务范围和交付要求，数据源通过上方设置选择；文字中已有的禁止要求仍会生效。重跑沿用原任务模式及 PDF 选项，并更新当前结果；原始问题、附件和修订记录保留。</p>
        <el-input :id="`intent-${session.thread_id}`" v-model="draft" type="textarea" :rows="5" maxlength="10000" show-word-limit placeholder="完整描述你希望执行的任务" />
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <p v-if="notice" class="muted" role="status">{{ notice }}</p>
        <el-button v-if="conflict" size="small" :loading="saving" @click="reloadLatest">加载最新版本，保留草稿</el-button>
        <div class="actions">
          <el-button :disabled="saving" @click="editing = false">取消</el-button>
          <el-button :disabled="!canSave" :loading="saving" @click="save(false)">保存修订</el-button>
          <el-button type="primary" :disabled="!canSave || session.status === 'running'" :loading="saving" @click="save(true)">保存并重新执行</el-button>
        </div>
      </template>
      <el-button v-else-if="pending" type="primary" size="small" :disabled="session.status === 'running'" @click="rerunWithSources">按最新修订重新执行</el-button>
      <div v-if="revisions.length" class="long-rule-entry"><el-button size="small" :loading="draftingRule" @click="draftRule(revisions[0])">另存为长期规则</el-button><span class="muted">需单独核对并确认保存，默认不会自动记住。</span></div>
      <p v-if="ruleError" class="error" role="alert">{{ ruleError }}</p>
      <p v-if="session?.status === 'running'" class="muted">当前任务执行中。可先保存修订，待执行结束或手动终止后重跑。</p>
      <details v-if="revisions.length" class="revision-history">
        <summary>修订记录（{{ revisions.length }}）</summary>
        <article v-for="(revision, index) in revisions" :key="revision.id">
          <div class="section-head"><h4>修订 {{ revisions.length - index }}</h4><span class="muted">{{ formatTime(revision.created_at) }} · {{ revision.id === session.applied_intent_id ? '本次已采用' : index === 0 ? '等待下次重跑' : '历史版本' }}</span></div>
          <p class="task-copy">{{ revision.revised_query }}</p>
          <details v-if="revision.plan"><summary>保存时的模型理解与计划</summary><p class="task-copy">{{ revision.plan.intent_summary || '当时未保存意图摘要' }}</p><ol class="steps"><li v-for="(step, i) in planDisplaySteps(revision.plan)" :key="i">{{ step }}</li></ol></details>
        </article>
      </details>
    </section>
    <RuleEditor v-model:visible="ruleEditorVisible" :draft="ruleDraft?.draft" :source-thread="ruleDraft?.source_thread_id" :source-revision="ruleDraft?.source_revision_id" :source-snapshot="ruleDraft?.source_snapshot" :source-query="ruleDraft?.source_query" @saved="toast('success','长期规则已保存，将用于后续匹配的任务')" />
  </div>
</template>
<script setup>
import {clarificationChoiceText} from '../clarificationPresentation.js'
import {formulaLabel} from '../ruleForms'
import {planDisplaySteps} from '../taskPresentation.js'
import { computed, ref, watch } from 'vue'
import SourceSelector from './SourceSelector.vue'
import {normalizeSources} from '../sourceSelection'
import RuleEditor from './RuleEditor.vue'
import { saveIntentRevision, getRun, draftLongTermRule } from '../api'
import { toast } from '../store'
const props = defineProps({ session: Object })
const emit = defineEmits(['rerun', 'refresh'])
const sourceDraft = ref(normalizeSources(props.session?.allowed_sources))
watch(()=>[props.session?.thread_id,props.session?.execution_id,props.session?.allowed_sources],()=>{
  sourceDraft.value=normalizeSources(props.session?.allowed_sources)
},{deep:true})
const sourcesChanged=computed(()=>JSON.stringify(normalizeSources(sourceDraft.value))!==JSON.stringify(normalizeSources(props.session?.allowed_sources)))
function rerunWithSources(){emit('rerun',props.session.thread_id,[...sourceDraft.value])}
const editing = ref(false)
const saving = ref(false)
const draft = ref('')
const error = ref('')
const conflict = ref(false)
const notice = ref('')
const draftingRule=ref(false), ruleEditorVisible=ref(false),ruleDraft=ref(null),ruleError=ref('')
const revisions = computed(() => props.session?.intent_revisions || [])
const applied = computed(() => revisions.value.find(r => r.id === props.session?.applied_intent_id))
const pending = computed(() => revisions.value.length > 0 && revisions.value[0].id !== props.session?.applied_intent_id)
const currentTask = computed(() => revisions.value[0]?.revised_query || props.session?.query || '')
const canSave = computed(() => !saving.value && draft.value.trim() && draft.value.trim() !== currentTask.value.trim())
const taskTypes = {qa: '直接问答', analysis: '数据分析', report: '报告生成'}
const sources = {knowledge: '知识库', db_query: '业务数据库', web_search: '联网检索'}
const sourceLabels = list => (list || []).map(s => sources[s] || s).join('、')
const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : ''
let expectedRevision = null
function beginEdit() {
  draft.value = currentTask.value
  expectedRevision = revisions.value[0]?.id ?? null
  error.value = ''
  conflict.value = false
  notice.value = ''
  editing.value = true
}
async function save(reexecute) {
  if (!canSave.value) return
  const target = props.session
  saving.value = true
  error.value = ''
  try {
    const run = await saveIntentRevision(target.thread_id, draft.value.trim(), expectedRevision)
    target.intent_revisions = run.intent_revisions
    target.applied_intent_id = run.applied_intent_id
    editing.value = false
    toast('success', '修订已保存，重跑时生效')
    if (reexecute && target.status !== 'running') {
      if (run.intent_revisions?.[0]?.id === run.saved_revision_id) emit('rerun', target.thread_id, [...sourceDraft.value])
      else toast('warning', '修订已保存，但其他页面已有更新版本；请核对后再重跑')
    }
  } catch (e) {
    error.value = e.message
    conflict.value = e.status === 409
  } finally { saving.value = false }
}
async function draftRule(revision) {
  const target=props.session
  draftingRule.value=true;ruleError.value=''
  try {
    ruleDraft.value=await draftLongTermRule(target.thread_id,revision.id)
    ruleEditorVisible.value=true
    if(ruleDraft.value.status==='manual_required')toast('warning','暂时无法自动归纳，请手动编辑内容与适用关键词')
  } catch(e){ruleError.value=e.message}finally{draftingRule.value=false}
}
async function reloadLatest() {
  const target = props.session
  saving.value = true
  try {
    const run = await getRun(target.thread_id)
    target.intent_revisions = run.intent_revisions || []
    target.applied_intent_id = run.applied_intent_id
    expectedRevision = target.intent_revisions[0]?.id ?? null
    conflict.value = false
    error.value = ''
    notice.value = '已加载最新修订，当前草稿已保留。请对照下方修订记录核对后再保存。'
    emit('refresh', target.thread_id)
  } catch (e) { error.value = e.message }
  finally { saving.value = false }
}
</script>
<style scoped>
.understanding {
  --intent-original:#245dcc;
  --intent-source:#147580;
  --intent-model:#7941b5;
  --intent-applied:#207351;
  --intent-review:#966318;
  height:100%; overflow:auto; overscroll-behavior:contain; scrollbar-gutter:stable; padding:0 8px 20px 0; font-size:14px; line-height:1.7;}
.understanding > section {--section-accent:var(--intent-original); padding:12px 0 18px;}
.original-task {--section-accent:var(--intent-original);}
.understanding > .source-access {--section-accent:var(--intent-source);}
.understanding > .model-section {--section-accent:var(--intent-model);}
.understanding > .applied-task {--section-accent:var(--intent-applied);}
.understanding > .correction-section {--section-accent:var(--intent-review);}
:global([data-theme="dark"] .understanding) {
  --intent-original:#8bb5ff;
  --intent-source:#79d0dc;
  --intent-model:#c8a3ff;
  --intent-applied:#80d8ae;
  --intent-review:#e7c17c;
}
.understanding > section:first-child {padding-top:0;}
.understanding > section + section {border-top:1px solid var(--border);}
.source-access :deep(.source-trigger) {height:26px;padding:0 8px;background:transparent;border-radius:5px;font-size:12px;}
.source-access .section-head {align-items:flex-start;}
.section-head > .muted {margin-top:0;font-size:12px;}
h3,h4,p {margin:0;}
h3 {display:flex; align-items:center; gap:8px; color:var(--section-accent); font-size:14px; font-weight:600;line-height:26px;}
h3::before {content:""; width:3px; height:14px; border-radius:2px; background:var(--section-accent); flex-shrink:0;}
h4 {font-size:14px; font-weight:500;}
.task-copy {white-space:pre-wrap; overflow-wrap:anywhere; margin-top:8px; color:var(--text);}
section > .task-copy {padding:10px 12px; border:1px solid color-mix(in srgb,var(--section-accent) 12%,var(--border)); border-radius:7px; background:color-mix(in srgb,var(--section-accent) 4%,var(--card-bg));}
.source-access > .muted {padding:8px 12px; border-radius:7px; background:color-mix(in srgb,var(--section-accent) 4%,var(--card-bg)); color:var(--text-secondary);}
.section-head {display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;}
.muted {font-size:13px; color:var(--text-secondary); margin-top:6px;}
.plan-facts {margin:12px 0; padding:4px 12px; border:1px solid var(--border); border-radius:7px; display:grid; background:var(--card-bg);}
.plan-facts > div {display:grid; grid-template-columns:72px minmax(0,1fr); gap:12px; padding:7px 0;}
.plan-facts > div + div {border-top:1px solid var(--border);}
dt {color:var(--section-accent); font-weight:500;} dd {margin:0; overflow-wrap:anywhere;}
.steps {margin:10px 0; padding-left:24px;}
.steps li::marker {color:var(--section-accent); font-weight:600;}
li + li {margin-top:5px;}
.assumptions {background:color-mix(in srgb,var(--intent-review) 5%,var(--card-bg)); border:1px solid color-mix(in srgb,var(--intent-review) 20%,var(--border)); padding:10px 12px; border-radius:8px;}
.assumptions h4 {color:var(--intent-review); font-weight:600;} .assumptions ul {margin:6px 0; padding-left:20px;}
.pending {color:var(--el-color-warning); margin:8px 0;}
label {display:block; margin-top:14px; font-weight:500;}
.actions {display:flex; justify-content:flex-end; gap:8px; flex-wrap:wrap; margin-top:12px;}
.actions :deep(.el-button + .el-button) {margin-left:0;}
.error {color:var(--el-color-danger); margin-top:8px;}
.adopted-rules{margin-top:12px;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:8px}.long-rule-entry{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:14px 0}.revision-history {margin-top:16px;} summary {cursor:pointer; color:var(--text-secondary); font-size:13px;} summary:focus-visible {outline:2px solid var(--primary); outline-offset:3px;}
article {border-top:1px solid var(--border); padding:12px 0;} article:first-of-type {margin-top:10px;}
</style>
