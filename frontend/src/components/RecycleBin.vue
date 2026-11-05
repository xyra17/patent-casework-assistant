<template>
  <el-drawer class="session-trash" :model-value="modelValue" title="会话回收站" size="min(560px, 100vw)" @update:model-value="emit('update:modelValue', $event)">
    <template #header><div class="trash-heading"><h2>会话回收站</h2><span class="trash-count">{{ total }} 个会话</span></div></template>
    <div class="trash-toolbar">
      <div class="retention-summary"><el-icon aria-hidden="true"><Clock /></el-icon><span>保留 30 天，到期自动清理</span><el-popover placement="bottom-start" :width="280" trigger="click"><template #reference><button class="retention-help" type="button" aria-label="回收站说明"><el-icon aria-hidden="true"><InfoFilled /></el-icon></button></template><div class="retention-details"><p>恢复时一并还原会话记录、附件和报告；永久删除后无法恢复。</p><p>业务数据库、知识库及已另存的长期规则不受影响。</p></div></el-popover></div>
      <el-button size="small" :loading="loading" @click="load()"><el-icon v-if="!loading" aria-hidden="true"><Refresh /></el-icon>刷新</el-button>
    </div>
    <div v-if="error" class="trash-error" role="alert">{{ error }}<el-button link type="primary" @click="load()">重试</el-button></div>
    <el-empty v-else-if="!items.length && !loading" description="回收站为空" :image-size="70" />
    <article v-for="item in items" :key="item.thread_id" class="trash-item" :aria-label="item.query">
      <h3>{{ item.query }}</h3>
      <div class="trash-meta"><span>删除于 {{ formatDate(item.deleted_at) }}</span><span>{{ remaining(item) }}</span></div>
      <div class="trash-actions">
        <el-button size="small" :disabled="!!busy || expired(item) || !!item.purge_started_at" :loading="busy === `restore-${item.thread_id}`" @click="restore(item)">恢复会话</el-button>
        <el-button link type="danger" :disabled="!!busy" @click="purge(item)">永久删除</el-button>
      </div>
    </article>
    <el-button v-if="items.length < total" class="trash-more" text :loading="loading" @click="load(true)">加载更多</el-button>
  </el-drawer>
</template>

<script setup>
import {ref, watch} from 'vue'
import {ElMessageBox} from 'element-plus'
import {Clock,InfoFilled,Refresh} from '@element-plus/icons-vue'
import {listTrash, restoreRun, purgeRun} from '../api'
import {toast} from '../store'
const props = defineProps({modelValue: Boolean})
const emit = defineEmits(['update:modelValue', 'restored'])
const items = ref([]), total = ref(0), loading = ref(false), busy = ref(''), error = ref('')
watch(() => props.modelValue, value => {if (value) load()}, {immediate: true})
const formatDate = value => new Date(value).toLocaleString('zh-CN', {hour12:false})
const expired = item => Date.parse(item.expires_at) <= Date.now()
function remaining(item) {
  if (item.purge_started_at) return '清理未完成，可重试永久删除'
  const days = Math.ceil((Date.parse(item.expires_at) - Date.now()) / 86400000)
  return days > 0 ? `剩余 ${days} 天` : '已到期，等待清理'
}
async function load(more = false) {
  if (loading.value) return
  loading.value = true; error.value = ''
  try {
    const result = await listTrash(more ? items.value.length : 0)
    items.value = more ? [...items.value, ...result.runs] : result.runs
    total.value = result.total
  } catch (e) {error.value = e.message}
  finally {loading.value = false}
}
async function restore(item) {
  busy.value = `restore-${item.thread_id}`
  try {
    await restoreRun(item.thread_id)
    emit('restored', item.thread_id)
    await load()
    toast('success', '会话已恢复，附件和执行记录已保留')
  } catch (e) {toast('error', e.message)}
  finally {busy.value = ''}
}
async function purge(item) {
  try {await ElMessageBox.confirm('将永久删除该会话的记录、附件、报告和全部执行文件，无法恢复。', '永久删除会话？', {type:'warning', confirmButtonText:'永久删除', cancelButtonText:'取消'})}
  catch {return}
  busy.value = `purge-${item.thread_id}`
  try {await purgeRun(item.thread_id); await load(); toast('success', '会话及全部工作文件已永久删除')}
  catch (e) {toast('error', e.message)}
  finally {busy.value = ''}
}
</script>

<style scoped>
.trash-heading {display:flex;align-items:center;gap:12px;min-width:0;}
.trash-heading h2 {margin:0;color:var(--text);font-size:20px;font-weight:600;line-height:28px;}
.trash-count {padding:3px 8px;background:var(--bg);border:1px solid var(--border);border-radius:5px;color:var(--text-secondary);font-size:12px;line-height:20px;white-space:nowrap;font-variant-numeric:tabular-nums;}
.trash-toolbar {display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 14px;color:var(--text-secondary);font-size:13px;}
.retention-summary {display:flex;align-items:center;gap:7px;min-width:0;line-height:1.5;}
.retention-summary>.el-icon {flex-shrink:0;font-size:15px;}
.retention-help {display:grid;place-items:center;flex-shrink:0;width:28px;height:28px;padding:0;border:0;border-radius:5px;background:transparent;color:var(--text-secondary);cursor:pointer;}
.retention-help:hover {color:var(--primary);background:var(--primary-soft);}
.retention-help:focus-visible {outline:2px solid var(--primary);outline-offset:1px;}
.retention-details {font-size:13px;color:var(--text-secondary);line-height:1.7;}
.retention-details p {margin:0;}
.retention-details p+p {margin-top:8px;}
.trash-toolbar>.el-button {flex-shrink:0;gap:5px;}
.trash-item {padding:18px 0;border-top:1px solid var(--border);}
.trash-item h3 {font-size:14px;font-weight:500;line-height:1.6;color:var(--text);overflow-wrap:anywhere;margin:0 0 8px;}
.trash-meta {display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;color:var(--text-secondary);font-size:12px;}
.trash-actions {display:flex;align-items:center;gap:16px;margin-top:14px;}
.trash-actions :deep(.el-button + .el-button) {margin-left:0;}
.trash-error {font-size:13px;color:var(--danger);}
.trash-more {width:100%;}
@media(max-width:420px){.trash-heading{gap:8px}.trash-heading h2{font-size:18px}.trash-toolbar{gap:8px;font-size:12px}.retention-summary{gap:5px}}
</style>

<style>
.session-trash .el-drawer__header {margin-bottom:0;padding:20px 24px 16px;border-bottom:1px solid var(--border);}
.session-trash .el-drawer__body {padding:14px 24px 24px;}
@media(max-width:420px){.session-trash .el-drawer__header{padding:16px}.session-trash .el-drawer__body{padding:12px 16px 20px}}
</style>
