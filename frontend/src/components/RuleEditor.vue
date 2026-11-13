<template>
  <el-dialog :model-value="visible" :title="draft?.id ? '编辑长期规则' : '确认长期规则'" width="min(720px, 94vw)" :close-on-click-modal="false" @update:model-value="emit('update:visible', $event)">
    <div class="rule-form" v-if="form">
      <p class="scope-notice">启用后用于匹配范围内的新任务；本次任务的明确要求优先。</p>
      <p v-if="sourceThread" class="muted">来源会话：{{ sourceQuery || sourceThread.slice(0, 8) }} · {{ sourceTurn ? '已保存对话轮次' : `已保存修订 #${sourceRevision}` }}</p>
      <details v-if="sourceSnapshot"><summary>查看来源修订</summary><p class="source-copy">{{ sourceSnapshot }}</p></details>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="规则名称"><el-input v-model="form.title" maxlength="80" aria-label="规则名称" /></el-form-item>
        <div class="form-row">
          <el-form-item label="规则类型"><el-select v-model="form.category" aria-label="规则类型" @change="changeCategory"><el-option v-for="(label, key) in RULE_CATEGORIES" :key="key" :label="label" :value="key" /></el-select></el-form-item>
          <el-form-item label="规则主题（同一概念使用相同名称）"><el-input v-model="form.subject" maxlength="80" placeholder="例如：案件称呼、结果结构" aria-label="规则主题" /></el-form-item>
        </div>
        <el-form-item label="规则内容"><el-input v-model="form.content" type="textarea" :rows="3" maxlength="1200" show-word-limit aria-label="规则内容" /></el-form-item>
        <el-form-item label="适用任务"><el-radio-group v-model="form.match_mode" aria-label="适用任务"><el-radio-button value="keywords">匹配关键词的任务</el-radio-button><el-radio-button value="all_tasks" :disabled="form.category !== 'preference'">所有任务（仅回答偏好）</el-radio-button></el-radio-group></el-form-item>
        <el-form-item v-if="form.match_mode !== 'all_tasks'" label="适用关键词（用逗号分隔）"><el-input v-model="terms" placeholder="例如：技术对比, 方案差异" aria-label="适用关键词" /><span class="muted">关键词和别名用于精确召回；系统也会按语义寻找候选，再核对适用范围。</span></el-form-item>
        <el-form-item label="术语别名（用逗号分隔，可选）"><el-input v-model="aliases" aria-label="术语别名" placeholder="例如：技术比较, 对照分析" /></el-form-item>
        <el-form-item label="适用范围说明"><el-input v-model="form.scope_note" maxlength="240" placeholder="描述在哪种任务或业务范围下使用" aria-label="适用范围说明" /></el-form-item>
        <section v-if="form.category === 'business'" class="binding-section">
          <h3>可执行业务定义</h3><p class="muted">只选择已有指标和字段；文字描述必须与这里的定义一致。支持逐行公式与关联明细先汇总；缺失的税费、退货等字段需先补充。</p>
          <el-form-item label="绑定语义指标"><el-select v-model="form.binding.metric_key" aria-label="绑定语义指标" filterable @change="metricChanged"><el-option v-for="metric in catalog.metrics" :key="metric.key" :value="metric.key" :label="`${metric.label} · ${metric.key} = ${metric.expression}`" /></el-select></el-form-item>
          <el-button :disabled="!form.binding.metric_key" :loading="drafting" @click="suggestBinding">根据规则内容整理定义</el-button>
          <el-form-item label="计算定义"><el-radio-group :model-value="form.binding.formula ? 'formula' : 'simple'" @update:model-value="setFormulaMode"><el-radio-button value="simple">已有指标 / 单字段</el-radio-button><el-radio-button value="formula">组合公式</el-radio-button></el-radio-group></el-form-item>
          <div v-if="form.binding.formula"><el-form-item label="计算顺序"><el-radio-group v-model="form.binding.formula_mode"><el-radio-button value="row">逐行计算后汇总</el-radio-button><el-radio-button value="aggregate">各项先汇总再计算</el-radio-button></el-radio-group></el-form-item><RuleFormulaNode v-model="form.binding.formula" :fields="catalog.fields" :base-table="selectedMetric?.table"/><p class="muted">{{ formulaLabel(form.binding.formula) }}</p><el-form-item label="字段汇总方式"><el-select v-model="form.binding.formula_aggregation" aria-label="公式汇总方式"><el-option v-for="key in ['sum','avg','min','max']" :key="key" :value="key" :label="aggregations[key]" /></el-select></el-form-item></div>
          <div v-else class="form-row">
            <el-form-item label="聚合方式（留空沿用指标定义）"><el-select v-model="form.binding.aggregation" clearable aria-label="聚合方式"><el-option v-for="(label, key) in aggregations" :key="key" :value="key" :label="label" /></el-select></el-form-item>
            <el-form-item label="计算字段"><el-select v-model="form.binding.field" clearable filterable aria-label="计算字段"><el-option v-for="field in metricFields" :key="field.key" :value="field.key" :label="field.label" /></el-select></el-form-item>
          </div>
          <el-form-item label="时间口径（留空沿用语义层）"><el-select v-model="form.binding.time_field" clearable aria-label="时间口径"><el-option v-for="f in metricFields.filter(f=>f.role==='time')" :key="f.key" :value="f.key" :label="f.label"/></el-select></el-form-item>
          <div v-for="(filter, index) in form.binding.filters" :key="index" class="filter-row">
            <el-select v-model="filter.field" filterable :aria-label="`过滤字段 ${index + 1}`"><el-option v-for="field in catalog.fields" :key="field.key" :value="field.key" :label="field.label" /></el-select>
            <el-select v-model="filter.op" :aria-label="`过滤操作 ${index + 1}`"><el-option v-for="(label,key) in operators" :key="key" :label="label" :value="key" /></el-select>
            <el-input v-model="filter.valueText" :aria-label="`过滤值 ${index + 1}`" placeholder="值；多个值用逗号分隔" />
            <el-button text type="danger" :aria-label="`移除过滤 ${index + 1}`" @click="form.binding.filters.splice(index,1)">移除</el-button>
          </div>
          <el-button size="small" :disabled="form.binding.filters.length >= 8" @click="form.binding.filters.push({field:'',op:'eq',valueText:''})">添加默认过滤</el-button>
          <div class="preview-action"><el-button :loading="previewing" @click="previewBinding">校验并预览定义</el-button><span class="muted">保存前请核对；预览不执行查询。</span></div>
          <div v-if="preview" class="binding-preview"><strong>校验通过 · 基础表 {{ preview.base_table }}</strong><p v-for="note in preview.notes" :key="note" class="muted">{{ note }}</p><p>时间口径：{{ form.binding.time_field || '沿用语义层' }}；默认过滤 {{ form.binding.filters.length }} 项。</p><details><summary>查看生成的查询与参数</summary><pre>{{ preview.sql }}
参数：{{ JSON.stringify(preview.params) }}</pre></details></div>
        </section>
        <el-form-item label="是否启用"><el-switch v-model="form.active" active-text="启用" inactive-text="停用" aria-label="保存后启用规则" /></el-form-item>
        <el-checkbox v-model="confirmed" class="confirm-check">我已核对内容、适用范围{{ form.category === 'business' ? '和可执行定义' : '' }}，确认保存为项目长期规则</el-checkbox>
      </el-form>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </div>
    <template #footer><el-button :disabled="saving" @click="emit('update:visible',false)">取消</el-button><el-button type="primary" :disabled="!confirmed || !valid || (form?.category === 'business' && !preview)" :loading="saving" @click="save">确认保存长期规则</el-button></template>
  </el-dialog>
</template>
<script setup>
import {computed, ref, watch} from 'vue'
import {getRuleCatalog,saveLongTermRule,previewRuleBinding,draftRuleBinding} from '../api'
import RuleFormulaNode from './RuleFormulaNode.vue'
import {RULE_CATEGORIES, rulePayload,bindingPayload,formulaLabel} from '../ruleForms'
const props=defineProps({visible:Boolean,draft:Object,sourceThread:String,sourceRevision:Number,sourceTurn:String,sourceSnapshot:String,sourceQuery:String})
const emit=defineEmits(['update:visible','saved'])
const aliases=ref(''),preview=ref(null),previewing=ref(false),drafting=ref(false)
const form=ref(null),terms=ref(''),confirmed=ref(false),saving=ref(false),error=ref('')
const catalog=ref({metrics:[],fields:[]})
const aggregations={sum:'求和',avg:'平均',count:'计数',count_distinct:'去重计数',min:'最小',max:'最大'}
const operators={eq:'等于',ne:'不等于',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于',like:'匹配',in:'属于多个值'}
const selectedMetric=computed(()=>catalog.value.metrics.find(m=>m.key===form.value?.binding?.metric_key))
const metricFields=computed(()=>catalog.value.fields.filter(f=>f.table===catalog.value.metrics.find(m=>m.key===form.value?.binding?.metric_key)?.table))
const valid=computed(()=>form.value && form.value.title.trim() && form.value.subject.trim() && form.value.content.trim() && (form.value.match_mode==='all_tasks' || terms.value.trim()) && form.value.scope_note.trim() && (form.value.category!=='business' || form.value.binding?.metric_key))
watch(()=>props.visible,async visible=>{
  if(!visible)return
  form.value=JSON.parse(JSON.stringify(props.draft))
  form.value.match_mode ||= 'keywords'
  if(form.value.binding)form.value.binding.formula_mode ||= 'row'
  if(form.value.category==='business'&&!form.value.binding)form.value.binding={metric_key:'',aggregation:null,field:null,filters:[]}
  if(form.value.binding)form.value.binding.filters=(form.value.binding.filters||[]).map(f=>({...f,valueText:Array.isArray(f.value)?f.value.join(','):String(f.value)}))
  aliases.value=(form.value.aliases||[]).join(', ');preview.value=null;terms.value=form.value.match_terms.join(', ');confirmed.value=false;error.value=''
  try {catalog.value=await getRuleCatalog()} catch(e){error.value=e.message}
},{immediate:true})
watch(form,()=>{confirmed.value=false;preview.value=null},{deep:true})
watch([terms,aliases],()=>{confirmed.value=false})
function changeCategory(value){if(value!=='preference')form.value.match_mode='keywords';form.value.binding=value==='business'?{metric_key:'',aggregation:null,field:null,filters:[]}:null;confirmed.value=false}
function metricChanged(){form.value.binding={metric_key:form.value.binding.metric_key,aggregation:null,field:null,filters:[],formula:null,formula_aggregation:'sum',time_field:null};confirmed.value=false}
function setFormulaMode(mode){form.value.binding.field=null;form.value.binding.aggregation=null;form.value.binding.formula=mode==='formula'?{op:'field',field:''}:null;form.value.binding.formula_aggregation='sum';form.value.binding.formula_mode='row'}
async function previewBinding(){previewing.value=true;error.value='';try{preview.value=await previewRuleBinding(bindingPayload(form.value.binding))}catch(e){error.value=e.message}finally{previewing.value=false}}
async function suggestBinding(){drafting.value=true;error.value='';try{const result=await draftRuleBinding(form.value.content,form.value.binding.metric_key);if(!result.binding){error.value=[result.explanation,...result.missing].join('；');return}form.value.binding={...result.binding,filters:(result.binding.filters||[]).map(f=>({...f,valueText:Array.isArray(f.value)?f.value.join(','):String(f.value)}))};confirmed.value=false;await previewBinding()}catch(e){error.value=e.message}finally{drafting.value=false}}

async function save(){
  if(!confirmed.value||!valid.value)return
  saving.value=true;error.value=''
  try {
    const payload=rulePayload(form.value)
    payload.aliases=aliases.value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean)
    payload.match_terms=terms.value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean)
    if(payload.binding){payload.binding=bindingPayload(payload.binding);preview.value=await previewRuleBinding(payload.binding)}
    const body={...payload,confirmed:true}
    if(form.value.id)body.expected_revision=form.value.revision
    else if(props.sourceThread){body.source_thread_id=props.sourceThread;if(props.sourceTurn)body.source_turn_id=props.sourceTurn;else body.source_revision_id=props.sourceRevision}
    const result=await saveLongTermRule(body,form.value.id)
    emit('saved',result);emit('update:visible',false)
  }catch(e){error.value=e.message}finally{saving.value=false}
}
</script>
<style scoped>
.rule-form{max-height:66vh;overflow:auto;padding-right:8px;font-size:14px;line-height:1.6}.scope-notice{margin-top:0;color:var(--text-secondary)}.muted{font-size:13px;color:var(--text-secondary)}.source-copy{white-space:pre-wrap;overflow-wrap:anywhere}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.form-row .el-select,.rule-form :deep(.el-select){width:100%}.binding-section{padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;margin-bottom:16px}.preview-action{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px}.binding-preview{border-top:1px solid var(--border);margin-top:12px;padding-top:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}h3{font-size:15px;margin:0}.filter-row{display:grid;grid-template-columns:1.2fr 100px 1fr auto;gap:6px;margin-bottom:8px}.confirm-check{display:flex;white-space:normal;align-items:flex-start;margin:14px 0}.confirm-check :deep(.el-checkbox__label){white-space:normal}.error{color:var(--el-color-danger)}summary{cursor:pointer;font-size:13px;color:var(--text-secondary)}@media(max-width:650px){.form-row{grid-template-columns:1fr}.filter-row{grid-template-columns:1fr 1fr}}
</style>
