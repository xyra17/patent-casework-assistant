<template>
  <el-dialog :model-value="visible" :title="title" width="min(860px,95vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @update:model-value="!busy && emit('update:visible',$event)">
    <div class="advanced-editor" v-loading="loading">
      <p>业务表：{{ table }} · 选中 {{ keys.length }} 条记录</p>
      <el-alert :title="kind==='rename_key'?'同步迁移直接关联记录，不关闭外键检查':'整批提交；任一记录冲突或校验失败，整批回滚'" type="info" :closable="false"/>
      <p v-if="kind==='rename_key'" class="muted">多层引用、自引用、子表主键关联或唯一键阻止迁移时会拒绝操作；请按错误提示分阶段处理。</p>
      <div v-if="kind!=='bulk_delete'" class="fields">
        <div v-for="c in columns" :key="c.name" class="field">
          <el-checkbox v-if="kind==='bulk_update'" v-model="fields[c.name].enabled" :disabled="busy">修改 {{ c.label || c.name }} · {{ c.name }}</el-checkbox>
          <label v-else>{{ c.label || c.name }} · {{ c.name }}（新主键）</label>
          <div class="field-value"><el-input v-model="fields[c.name].value" :disabled="busy || !fields[c.name].enabled || fields[c.name].isNull" :aria-label="`批量字段 ${c.name}`"/><el-checkbox v-if="c.nullable" v-model="fields[c.name].isNull" :disabled="busy || !fields[c.name].enabled">NULL</el-checkbox></div>
        </div>
      </div>
      <label class="reason-label">{{ kind==='bulk_delete'?'删除':'修改' }}原因<el-input v-model="reason" type="textarea" :rows="2" maxlength="500" :disabled="busy" aria-label="高级维护原因"/></label>
      <el-button :loading="busy" :disabled="loading || !reason.trim() || (kind!=='bulk_delete' && !Object.keys(changes).length)" @click="preview">预览影响范围</el-button>
      <section v-if="plan" class="review" aria-label="高级维护预览">
        <strong>将处理 {{ plan.count }} 条选中记录，另涉及 {{ plan.related_count }} 条关联记录</strong>
        <article v-for="r in plan.records" :key="JSON.stringify(r.key)"><p>主键：{{ JSON.stringify(r.key) }}</p><p v-if="!r.after">将删除整条记录：{{ JSON.stringify(r.before) }}</p><p v-for="change in differences(r)" :key="change.name">{{ change.name }}：{{ show(change.before) }} → {{ show(change.after) }}</p></article>
        <details v-if="plan.related.length"><summary>查看关联迁移明细</summary><p v-for="r in plan.related" :key="r.table+JSON.stringify(r.key)">{{ r.table }} · {{ JSON.stringify(r.key) }} → {{ JSON.stringify(r.changes) }}</p></details>
        <el-checkbox v-model="confirmed" :disabled="busy">我已核对全部记录及关联影响，确认执行</el-checkbox>
      </section>
      <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="operationId" class="muted">操作编号：{{ operationId }}。连接异常时请保留编号重试核实。</p>
    </div>
    <template #footer><el-button :disabled="busy" @click="emit('update:visible',false)">取消</el-button><el-button :type="kind==='bulk_delete'?'danger':'primary'" :disabled="!plan || !confirmed || loading" :loading="busy" @click="save">确认{{ title }}</el-button></template>
  </el-dialog>
</template>
<script setup>
import {computed,ref,watch} from 'vue'
import {getBusinessRow,previewBusinessAdvanced,applyBusinessAdvanced} from '../api'
const props=defineProps({visible:Boolean,table:String,keys:{type:Array,default:()=>[]},kind:String})
const emit=defineEmits(['update:visible','saved'])
const title=computed(()=>({rename_key:'修改主键',bulk_update:'批量编辑',bulk_delete:'批量删除'}[props.kind]))
const columns=ref([]),fields=ref({}),reason=ref(''),plan=ref(null),confirmed=ref(false),operationId=ref(''),loading=ref(false),busy=ref(false),error=ref('')
const changes=computed(()=>Object.fromEntries(Object.entries(fields.value).filter(([,f])=>f.enabled).map(([k,f])=>[k,f.isNull?null:f.value])))
let request=0
watch(()=>props.visible,async visible=>{const stamp=++request;if(!visible)return;columns.value=[];fields.value={};reason.value='';plan.value=null;confirmed.value=false;operationId.value='';error.value='';loading.value=true;try{if(props.kind!=='bulk_delete'){const detail=await getBusinessRow(props.table,props.keys[0]);if(stamp!==request)return;columns.value=detail.columns.filter(c=>props.kind==='rename_key'?c.key==='PRI':c.editable);fields.value=Object.fromEntries(columns.value.map(c=>[c.name,{enabled:props.kind==='rename_key',value:props.kind==='rename_key'?String(detail.values[c.name]):'',isNull:false}]))}}catch(e){if(stamp===request)error.value=e.message}finally{if(stamp===request)loading.value=false}})
watch([fields,reason],()=>{plan.value=null;confirmed.value=false;operationId.value=''},{deep:true,flush:'sync'})
function payload(){return {kind:props.kind,table:props.table,keys:props.keys,changes:changes.value,reason:reason.value.trim()}}
function show(v){return v===null?'NULL':String(v)}
function differences(r){return Object.keys(r.after||{}).filter(k=>JSON.stringify(r.before[k])!==JSON.stringify(r.after[k])).map(name=>({name,before:r.before[name],after:r.after[name]}))}
async function preview(){busy.value=true;error.value='';plan.value=null;confirmed.value=false;operationId.value='';try{plan.value=await previewBusinessAdvanced(payload());operationId.value=crypto.randomUUID()}catch(e){error.value=e.message}finally{busy.value=false}}
async function save(){if(!plan.value || !confirmed.value)return;busy.value=true;error.value='';try{const result=await applyBusinessAdvanced({...payload(),token:plan.value.token,operation_id:operationId.value,confirmed:true});emit('saved',result);emit('update:visible',false)}catch(e){error.value=e.message}finally{busy.value=false}}
</script>
<style scoped>
.advanced-editor{max-height:65vh;overflow:auto;font-size:14px;line-height:1.65;padding-right:8px}.fields{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}.field-value{display:flex;gap:8px}.field label,.reason-label{display:block;margin-bottom:8px}.reason-label{margin-top:16px}.review{border:1px solid var(--border);padding:16px;border-radius:8px;margin-top:16px;overflow-wrap:anywhere}.review article{border-bottom:1px solid var(--border);padding:8px 0}.review p{margin:4px 0}.muted{color:var(--text-secondary);overflow-wrap:anywhere}.error{color:var(--el-color-danger)}.advanced-editor :deep(.el-checkbox__label){white-space:normal}@media(max-width:650px){.fields{grid-template-columns:1fr}}
</style>
