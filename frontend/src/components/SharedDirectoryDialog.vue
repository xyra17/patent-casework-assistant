<template>
  <el-dialog v-model="visible" class="directory-config-dialog" title="共享目录配置" width="min(680px, calc(100vw - 32px))" top="min(8vh, 60px)" append-to-body :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
    <template #header><div class="directory-heading"><h2>{{ form.id ? '共享目录配置' : '接入共享目录' }}</h2><p>设置读取位置与同步规则</p></div></template>
    <div ref="formElement" class="directory-form" @submit.prevent="save">
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="directory-error"/>
      <el-form label-position="top">
        <el-form-item label="目录名称" required>
          <el-input v-model="form.label" aria-label="目录名称" placeholder="例如：代理部门案件共享盘" maxlength="100"/>
        </el-form-item>
        <el-form-item label="服务器目录" required>
          <div v-if="form.id" class="directory-path" aria-label="服务器目录">{{ form.path }}</div>
          <el-select v-else v-model="form.path" aria-label="服务器目录" filterable allow-create default-first-option placeholder="选择或填写服务器目录" popper-class="directory-path-options">
            <el-option v-for="path in allowedRoots" :key="path" :label="path" :value="path"><span>{{ path }}</span></el-option>
          </el-select>
          <p class="directory-help">{{ form.id ? '已绑定当前目录，如需更换路径，请另行接入新目录。' : '目录须在服务器允许的范围内；共享盘原件保持只读。' }}</p>
        </el-form-item>
        <el-form-item label="资料分类">
          <div v-if="form.id" class="directory-classification"><span>{{ form.classification === 'synthetic' ? '独立模拟资料' : '私密资料 · 仅限本地' }}</span><small>接入后固定</small></div>
          <el-select v-else v-model="form.classification" aria-label="资料分类"><el-option label="私密资料 · 仅限本地处理" value="local_only"/><el-option label="独立模拟资料 · 须有目录标记" value="synthetic"/></el-select>
        </el-form-item>
        <div class="directory-enabled"><div><label for="directory-enabled-switch">启用目录</label><p>控制该目录是否参与同步和案件查询。</p></div><div class="directory-switch"><span>{{ form.enabled ? '已启用' : '已停用' }}</span><el-switch id="directory-enabled-switch" v-model="form.enabled" aria-label="启用目录"/></div></div>
        <details :key="openSequence" class="directory-advanced">
          <summary><span>高级设置</span><small>{{ mappings.length ? `${mappings.length} 条自定义字段映射` : '字段映射与目录核对' }}</small></summary>
          <div class="directory-advanced-body">
            <div class="directory-mapping-heading"><h3>表头别名</h3><el-button link type="primary" @click="mappings.push({field:'',aliases:[]})">＋ 添加映射</el-button></div>
            <p class="directory-help">系统已识别常用表头。识别不到时，可为对应业务字段补充别名。</p>
            <p v-if="!mappings.length" class="directory-no-mapping">使用默认识别规则，暂未添加自定义映射。</p>
            <div v-else class="directory-mapping-table">
              <div class="directory-mapping-labels"><span>业务字段</span><span>表头别名 · 可添加多个</span><span/></div>
              <div v-for="(row,index) in mappings" :key="index" class="directory-mapping-row">
                <el-select v-model="row.field" filterable placeholder="选择业务字段" :aria-label="`第 ${index+1} 条映射的业务字段`"><el-option v-for="(label,key) in fields" :key="key" :value="key" :label="label" :disabled="mappings.some((item,i)=>i!==index&&item.field===key)"/></el-select>
                <el-select v-model="row.aliases" multiple filterable allow-create default-first-option :reserve-keyword="false" placeholder="输入并选择表头名称" :aria-label="`第 ${index+1} 条映射的表头别名`"/>
                <el-button link type="danger" :aria-label="`移除第 ${index+1} 条映射`" @click="mappings.splice(index,1)">移除</el-button>
              </div>
            </div>
            <div v-if="form.id" class="directory-reattach"><el-checkbox v-model="form.reattach">重新确认目录位置</el-checkbox><p class="directory-help">共享盘重新挂载或目录身份变化后，核对路径并勾选。</p></div>
          </div>
        </details>
      </el-form>
    </div>
    <template #footer><div class="directory-footer"><span>保存后{{ form.enabled ? '需同步生效' : '该目录不参与查询' }}</span><div><el-button :disabled="saving" @click="visible=false">取消</el-button><el-button type="primary" :loading="saving" @click="save">{{ form.id ? '保存配置' : '接入目录' }}</el-button></div></div></template>
  </el-dialog>
</template>

<script setup>
import {ref, reactive, nextTick} from 'vue'
import {ElMessage} from 'element-plus'
import {casework} from '../caseworkApi'

const emit=defineEmits(['saved'])
const visible=ref(false),saving=ref(false),error=ref(''),formElement=ref(null)
const allowedRoots=ref([]),fields=ref({}),mappings=ref([]),openSequence=ref(0)
const form=reactive({id:null,label:'',path:'',classification:'local_only',enabled:true,version:null,reattach:false})

function open(root,options={}) {
  allowedRoots.value=options.allowedRoots||[];fields.value=options.fields||{}
  Object.assign(form,{id:null,label:'',path:allowedRoots.value[0]||'',classification:'local_only',enabled:true,version:null},root||{}, {enabled:root ? !!root.enabled : true,reattach:false})
  mappings.value=Object.entries(root?.mapping||{}).map(([field,aliases])=>({field,aliases:[...aliases]}))
  error.value='';openSequence.value++;visible.value=true
}

async function save() {
  if(saving.value)return
  error.value=''
  try {
    if(!form.label.trim())throw new Error('请填写目录名称。')
    if(!form.path.trim())throw new Error('请选择或填写服务器目录。')
    const mapping={}
    for(const row of mappings.value) {
      if(!row.field || !row.aliases.length)throw new Error('请为每条映射选择业务字段，并添加至少一个表头别名；不需要的映射可移除。')
      if(Object.hasOwn(mapping,row.field))throw new Error('同一个业务字段请合并为一条映射。')
      if(row.aliases.some(alias=>!alias.trim()))throw new Error('表头别名不能为空，请移除空白别名。')
      mapping[row.field]=[...new Set(row.aliases.map(alias=>alias.trim()))]
    }
    saving.value=true
    const body={label:form.label.trim(),path:form.path.trim(),classification:form.classification,enabled:form.enabled,mapping,expected_version:form.version,reattach:form.reattach}
    await casework('/roots'+(form.id?'/'+form.id:''),{method:form.id?'PUT':'POST',body})
    visible.value=false;emit('saved')
    ElMessage.success(form.enabled?'配置已保存，请执行同步使其生效':'配置已保存，目录已停用')
  } catch(e) {
    error.value=e.message||'保存失败，请重试。'
    await nextTick();formElement.value?.parentElement.scrollTo({top:0})
  } finally {saving.value=false}
}
defineExpose({open})
</script>

<style scoped>
:global(.directory-config-dialog) {max-height:calc(100dvh - min(8vh, 60px) - 20px);display:flex;flex-direction:column;padding:0;border-radius:12px;overflow:hidden;margin-bottom:20px;}
:global(.directory-config-dialog .el-dialog__header) {padding:22px 24px 18px;margin:0;border-bottom:1px solid var(--border);}
:global(.directory-config-dialog .el-dialog__headerbtn) {top:12px;right:12px;}
:global(.directory-config-dialog .el-dialog__body) {padding:20px 24px;overflow:auto;overscroll-behavior:contain;min-height:0;}
:global(.directory-config-dialog .el-dialog__footer) {padding:16px 24px;border-top:1px solid var(--border);}
.directory-heading h2 {font-size:18px;line-height:1.5;font-weight:600;margin:0;color:var(--text);}
.directory-heading p {font-size:13px;line-height:1.6;color:var(--text-secondary);margin:4px 0 0;}
.directory-form {font-size:13px;color:var(--text);}
.directory-error {margin-bottom:18px;}
.directory-form :deep(.el-form-item) {margin-bottom:18px;}
.directory-form :deep(.el-form-item__label) {font-size:13px;line-height:20px;margin-bottom:8px;color:var(--text);}
.directory-form :deep(.el-select) {width:100%;}
.directory-form :deep(.el-form-item__content) {line-height:1.7;}
.directory-path {width:100%;box-sizing:border-box;white-space:pre-wrap;overflow-wrap:anywhere;padding:10px 12px;border:1px solid var(--border);background:var(--bg);border-radius:7px;font-size:13px;line-height:1.7;user-select:text;}
.directory-help {width:100%;font-size:12px;line-height:1.75;color:var(--text-secondary);margin:7px 0 0;}
.directory-classification {display:flex;align-items:center;gap:12px;font-size:13px;line-height:1.7;}
.directory-classification small {font-size:12px;color:var(--text-secondary);}
.directory-enabled {display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 0;border-top:1px solid var(--border);}
.directory-enabled label {font-size:13px;font-weight:500;cursor:pointer;}
.directory-enabled p {font-size:12px;line-height:1.7;color:var(--text-secondary);margin:5px 0 0;}
.directory-switch {display:flex;align-items:center;gap:9px;white-space:nowrap;}
.directory-switch span {font-size:12px;color:var(--text-secondary);}
.directory-advanced {border-top:1px solid var(--border);}
.directory-advanced summary {padding:16px 0 0;font-size:13px;line-height:1.7;cursor:pointer;}
.directory-advanced summary small {font-size:12px;color:var(--text-secondary);margin-left:12px;}
.directory-advanced-body {padding:16px 0 0;}
.directory-mapping-heading {display:flex;align-items:center;justify-content:space-between;gap:12px;}
.directory-mapping-heading h3 {font-size:13px;font-weight:600;margin:0;}
.directory-no-mapping {font-size:12px;color:var(--text-secondary);line-height:1.7;background:var(--bg);border-radius:6px;padding:10px 12px;margin:12px 0 0;}
.directory-mapping-table {margin-top:12px;}
.directory-mapping-labels,.directory-mapping-row {display:grid;grid-template-columns:minmax(130px, .85fr) minmax(0,1.5fr) 36px;gap:10px;align-items:start;}
.directory-mapping-labels {font-size:12px;color:var(--text-secondary);margin-bottom:8px;}
.directory-mapping-row {margin-bottom:10px;}
.directory-mapping-row > .el-button {height:32px;margin:0;}
.directory-reattach {margin-top:16px;padding-top:14px;border-top:1px solid var(--border);}
.directory-reattach .directory-help {margin:0;}
.directory-footer {display:flex;align-items:center;justify-content:space-between;gap:12px;}
.directory-footer > span {font-size:12px;color:var(--text-secondary);text-align:left;}
.directory-footer > div {display:flex;gap:10px;flex-shrink:0;}
.directory-footer .el-button + .el-button {margin-left:0;}
:global(.directory-path-options .el-select-dropdown__item) {height:auto;min-height:34px;white-space:normal;overflow-wrap:anywhere;line-height:1.7;padding-top:6px;padding-bottom:6px;max-width:min(620px, 85vw);}
@media (max-width:560px) {
  :global(.directory-config-dialog .el-dialog__header),:global(.directory-config-dialog .el-dialog__body),:global(.directory-config-dialog .el-dialog__footer) {padding-left:18px;padding-right:18px;}
  .directory-mapping-labels {display:none;}
  .directory-mapping-row {grid-template-columns:minmax(0,1fr) 36px;gap:8px;padding:12px 0;border-bottom:1px solid var(--border);}
  .directory-mapping-row > .el-select:nth-child(2) {grid-column:1;grid-row:2;}
  .directory-mapping-row > .el-button {grid-column:2;grid-row:1;}
  .directory-advanced summary small {display:block;margin:3px 0 0 16px;}
  .directory-enabled {gap:10px;}
}
</style>
