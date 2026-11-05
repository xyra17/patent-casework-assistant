<template>
  <el-dialog :model-value="visible" :title="mode==='create'?'新增业务记录':mode==='delete'?'删除业务记录':'编辑业务记录'" width="min(760px,94vw)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" @update:model-value="!saving && emit('update:visible',$event)">
    <div class="row-editor" v-loading="loading">
      <p class="record-key">表：{{ table }} <template v-if="mode!=='create'"> · 主键：{{ JSON.stringify(record?.key || rowKey) }}</template></p>
      <p class="local-notice">本地共享维护模式，未区分登录用户；操作会记录原因与修改前后内容。TIMESTAMP 按 UTC 显示和保存。</p>
      <el-alert v-if="mode==='delete' && record?.referenced_by?.length" title="这条记录仍有关联，不能删除" :description="`被 ${record.referenced_by.map(x=>x.table).join('、')} 引用。请先处理这些关联记录，不会自动级联删除。`" type="warning" :closable="false" show-icon/>
      <el-alert v-if="mode==='create' && record?.dedupe_rule" :title="record.dedupe_rule.columns.length ? `新增前按「${record.dedupe_rule.label}」及数据库唯一约束查重` : record.dedupe_rule.notice" :type="record.dedupe_rule.columns.length?'info':'warning'" :closable="false" show-icon/>
      <el-form v-if="record" label-position="top" @submit.prevent>
        <div class="field-grid">
          <el-form-item v-for="c in record.columns" :key="c.name" :label="`${c.label || c.comment || c.name} · ${c.name}${c.key==='PRI'?'（主键）':''}`">
            <el-input v-if="mode==='delete' || (mode==='create' ? !c.insertable : !c.editable)" :model-value="mode==='create'?'由数据库自动生成':display(record.values[c.name])" disabled />
            <template v-else>
              <el-input v-model="fields[c.name].value" :disabled="fields[c.name].isNull || fields[c.name].useDefault || saving" :type="/text|json/i.test(c.type)?'textarea':'text'" :rows="2" :aria-label="`编辑 ${c.name}`" :placeholder="placeholder(c)"/>
              <div class="field-caption"><span>{{ c.type }}{{ !c.nullable?' · 不可为空':'' }}</span><el-checkbox v-if="mode==='create' && (c.has_default || c.nullable) && !record.dedupe_rule?.columns.includes(c.name)" v-model="fields[c.name].useDefault" :disabled="saving" :aria-label="`${c.name} 使用默认值`">{{ c.has_default?'使用数据库默认值':'留空不填写' }}</el-checkbox><el-checkbox v-if="c.nullable && !fields[c.name].useDefault" v-model="fields[c.name].isNull" :disabled="saving" :aria-label="`${c.name} 设为NULL`">设为 NULL</el-checkbox></div>
            </template>
          </el-form-item>
        </div>
        <p v-if="mode==='create'" class="local-notice">自增编号自动生成。勾选默认值的字段不提交；取消后可填写具体值或显式选择 NULL。数量与金额不会自动重算。</p>
        <p v-if="mode==='edit'" class="local-notice">主键、自动生成及系统更新时间字段不可修改。数量、单价、总额分别保存，不会自动推断折扣或重算其他字段。</p>
        <div v-if="mode==='edit' && Object.keys(changes).length" class="change-review"><strong>本次修改 {{ Object.keys(changes).length }} 个字段</strong><p v-for="(value,name) in changes" :key="name">{{ name }}：{{ display(record.values[name]) }} → {{ display(value) }}</p></div>
        <el-form-item :label="mode==='create'?'新增原因':mode==='delete'?'删除原因':'修改原因'"><el-input v-model="reason" type="textarea" :rows="2" maxlength="500" :disabled="saving" aria-label="维护原因"/></el-form-item>
        <el-checkbox v-model="confirmed" :disabled="saving">{{ mode==='create'?'我已核对字段与查重依据，确认新增一条记录':mode==='delete'?'我已核对主键和记录内容，确认只删除这一条记录':'我已核对本次字段变化，确认保存' }}</el-checkbox>
      </el-form>
      <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="operationId" class="operation-id">操作编号：{{ operationId }}。网络异常时保留编号重试，不会重复修改。</p>
    </div>
    <template #footer><el-button :disabled="saving" @click="emit('update:visible',false)">取消</el-button><el-button :type="mode==='delete'?'danger':'primary'" :disabled="!canSave" :loading="saving" @click="save">{{ mode==='create'?'确认新增记录':mode==='delete'?'确认删除这条记录':'保存修改' }}</el-button></template>
  </el-dialog>
</template>
<script setup>
import {computed,ref,watch} from 'vue'
import {editableFields,rowChanges,operationIdentity,creationValues} from '../businessRows'
import {getBusinessRow,getBusinessRowTemplate,mutateBusinessRow} from '../api'
const props=defineProps({visible:Boolean,table:String,rowKey:Object,mode:{type:String,default:'edit'}})
const emit=defineEmits(['update:visible','saved'])
const record=ref(null),fields=ref({}),loading=ref(false),saving=ref(false),error=ref(''),reason=ref(''),confirmed=ref(false),operationId=ref('')
let request=0,lastPayload=''
const changes=computed(()=>props.mode==='create'?creationValues(record.value,fields.value):rowChanges(record.value,fields.value))
const canSave=computed(()=>record.value && !loading.value && !saving.value && reason.value.trim() && confirmed.value && (props.mode==='delete'?!record.value.referenced_by.length:Object.keys(changes.value).length))
watch(()=>props.visible,async visible=>{const token=++request;if(!visible)return;record.value=null;fields.value={};reason.value='';confirmed.value=false;operationId.value='';lastPayload='';error.value='';loading.value=true;try{const detail=await (props.mode==='create'?getBusinessRowTemplate(props.table):getBusinessRow(props.table,props.rowKey));if(token!==request)return;record.value=detail;fields.value=editableFields(detail);if(props.mode==='create')for(const c of detail.columns)Object.assign(fields.value[c.name],{isNull:false,useDefault:(c.has_default || c.nullable) && !detail.dedupe_rule?.columns.includes(c.name)})}catch(e){if(token===request)error.value=e.message}finally{if(token===request)loading.value=false}})
watch([fields,reason],()=>{confirmed.value=false},{deep:true})
function display(v){if(v===null)return 'NULL';if(typeof v==='object')return '[二进制/复杂值，只读]';return String(v ?? '')}
function placeholder(c){return /^date$/i.test(c.type)?'YYYY-MM-DD':/datetime|timestamp/i.test(c.type)?'YYYY-MM-DD HH:mm:ss':'输入字段值'}
async function save(){if(!canSave.value)return;saving.value=true;error.value='';const body={table:props.table,...(props.mode==='create'?{values:changes.value}:{key:record.value.key,expected_version:record.value.version,changes:props.mode==='delete'?{}:changes.value}),reason:reason.value.trim(),confirmed:true};const identity=operationIdentity(body,{payload:lastPayload,id:operationId.value});operationId.value=identity.id;lastPayload=identity.payload;try{const result=await mutateBusinessRow(props.mode,{...body,operation_id:operationId.value});emit('saved',result);emit('update:visible',false)}catch(e){error.value=e.message}finally{saving.value=false}}
</script>
<style scoped>
.row-editor{max-height:65vh;overflow:auto;padding-right:8px;font-size:14px;line-height:1.6}.record-key{font-weight:600;margin-top:0;overflow-wrap:anywhere}.local-notice,.field-caption,.operation-id{font-size:13px;color:var(--text-secondary)}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 16px;margin-top:14px}.field-caption{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%}.change-review{padding:12px;border:1px solid var(--border);background:var(--bg);border-radius:8px;margin:12px 0;overflow-wrap:anywhere}.change-review p{margin:6px 0}.error{color:var(--el-color-danger)}.operation-id{overflow-wrap:anywhere}.row-editor :deep(.el-checkbox__label){white-space:normal}@media(max-width:650px){.field-grid{grid-template-columns:1fr}}
</style>
