<template>
  <div class="audit-panel" aria-label="业务数据修改记录">
    <div class="audit-toolbar"><div><h2>表单修改记录</h2><p>新增、编辑、删除、主键迁移和批量维护的前后差异。SQL 操作请在 SQL 控制台查看执行记录。</p></div><el-button :loading="loading" @click="load">刷新</el-button></div>
    <div class="audit-filters"><el-select v-model="tableFilter" :disabled="loading || !records.length" clearable placeholder="全部业务表" aria-label="修改记录业务表"><el-option v-for="table in tableOptions" :key="table" :label="table" :value="table"/></el-select><el-select v-model="operationFilter" :disabled="loading || !records.length" clearable placeholder="全部操作" aria-label="修改记录操作类型"><el-option v-for="kind in operationOptions" :key="kind" :label="operationName(kind)" :value="kind"/></el-select><span>最近 {{ records.length }} 条中匹配 {{ filteredRecords.length }} 条</span></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <el-empty v-if="!filteredRecords.length && !loading && !error" :description="records.length?'没有匹配的修改记录':'暂无修改记录'" :image-size="70"/>
    <div class="audit-list" v-loading="loading"><article v-for="item in filteredRecords" :key="item.operation_id"><header><strong>{{ operationName(item.operation) }} · {{ item.table_name }}</strong><span>{{ new Date(item.created_at).toLocaleString('zh-CN',{hour12:false}) }}</span></header><p>主键：{{ JSON.stringify(item.key) }} · 操作者：本地共享（未登录）</p><p>原因：{{ item.reason }}</p><details><summary>查看修改前后</summary><table v-resizable-columns class="audit-diff"><thead><tr><th>字段</th><th>修改前</th><th>{{ item.operation==='delete'?'删除后':'修改后' }}</th></tr></thead><tbody><tr v-for="change in changesFor(item)" :key="change.field"><td>{{ item.field_labels?.[change.field] || change.field }}</td><td>{{ change.before }}</td><td>{{ change.after }}</td></tr></tbody></table></details><small>操作编号：{{ item.operation_id }}</small></article></div>
  </div>
</template>
<script setup>
import {computed,ref,watch} from 'vue'
import {getBusinessAudit} from '../api'
const props=defineProps({refreshKey:Number})
const loading=ref(false),error=ref(''),records=ref([]),tableFilter=ref(''),operationFilter=ref('')
const tableOptions=computed(()=>[...new Set(records.value.map(r=>r.table_name))])
const operationOptions=computed(()=>[...new Set(records.value.map(r=>r.operation))])
const filteredRecords=computed(()=>records.value.filter(r=>(!tableFilter.value || r.table_name===tableFilter.value) && (!operationFilter.value || r.operation===operationFilter.value)))
function operationName(kind){return {insert:'新增',update:'编辑',delete:'删除',rename_key:'主键变更',rekey_child:'关联主键迁移',rekey_total:'主键变更汇总',bulk_update:'批量编辑汇总',bulk_delete:'批量删除汇总'}[kind] || kind}
function changesFor(item){return [...new Set([...Object.keys(item.before||{}),...Object.keys(item.after||{})])].filter(field=>JSON.stringify(item.before?.[field])!==JSON.stringify(item.after?.[field])).map(field=>({field,before:display(item.before?.[field]),after:item.operation==='delete'?'记录已删除':display(item.after?.[field])}))}
function display(value){return value===null?'NULL':typeof value==='object'?JSON.stringify(value):String(value ?? '—')}
async function load(){loading.value=true;error.value='';try{records.value=(await getBusinessAudit()).records}catch(e){error.value=e.message}finally{loading.value=false}}
watch(()=>props.refreshKey,load,{immediate:true})
</script>
<style scoped>
.audit-panel{padding-top:4px;gap:14px;height:100%;min-height:0;display:flex;flex-direction:column;font-size:14px}.audit-toolbar{flex-shrink:0;display:flex;align-items:center;justify-content:space-between;gap:14px}.audit-toolbar h2{font-size:17px;margin:0}.audit-toolbar p{margin:6px 0 0}.audit-filters{display:flex;align-items:center;gap:10px;flex-wrap:wrap;flex-shrink:0}.audit-filters .el-select{width:180px}.audit-filters>span{font-size:13px;color:var(--text-secondary)}.audit-toolbar p,.audit-list p,small{color:var(--text-secondary);line-height:1.6}.audit-list{flex:1;min-height:0;overflow:auto}.audit-list article{padding:18px 0;border-bottom:1px solid var(--border)}.audit-list header{display:flex;justify-content:space-between;gap:10px}.audit-list p{margin:7px 0}summary{cursor:pointer;color:var(--primary)}.audit-diff{width:100%;border-collapse:collapse;margin:12px 0;table-layout:fixed}.audit-diff th,.audit-diff td{padding:10px;text-align:left;border:1px solid var(--border);overflow-wrap:anywhere}.audit-diff th{color:var(--text-secondary)}.audit-compare{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:13px}.error{color:var(--el-color-danger)}small{overflow-wrap:anywhere}@media(max-width:650px){.audit-compare{grid-template-columns:1fr}.audit-toolbar{flex-shrink:0;flex-wrap:wrap}}
</style>
