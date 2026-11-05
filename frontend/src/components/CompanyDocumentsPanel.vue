<template>
  <section class="knowledge-panel" aria-label="公司文档">
    <header class="library-toolbar"><div><h2>公司文档</h2><p>公司内部手册、制度与经验资料。</p></div><div class="actions"><el-button :disabled="!catalog?.index_exists" @click="searchVisible=true"><el-icon><Search /></el-icon>检索资料</el-button><el-button type="primary" :disabled="disabled || !catalog" @click="chooseFiles()"><el-icon><Plus /></el-icon>添加资料</el-button></div></header>
    <p v-if="error" class="error-message" role="alert">{{ error }}</p>
    <template v-if="catalog">
      <div class="index-summary" role="status" aria-live="polite"><el-tag :type="catalog.busy ? 'primary' : catalog.needs_update ? 'warning' : 'success'" effect="plain">{{ catalog.busy ? '更新中' : catalog.index_exists && !catalog.version_known ? '版本待确认' : catalog.needs_update ? '索引待更新' : '索引已同步' }}</el-tag><span>{{ catalog.documents.length }} 份资料 · {{ catalog.chunks == null ? '片段数待确认' : `${catalog.chunks} 个检索片段` }}</span><el-popover placement="bottom" width="290" trigger="click"><template #reference><el-button link>索引详情</el-button></template><p>上次更新：{{ formatTime(catalog.updated_at) }}</p><p v-if="catalog.removed_count">旧索引中有 {{ catalog.removed_count }} 份已移除资料。</p></el-popover><div class="index-actions"><el-button link :loading="loading" :disabled="acting" @click="refresh">刷新</el-button><el-button link type="primary" :loading="catalog.job.state==='running'" :disabled="disabled" @click="rebuild">更新索引</el-button></div></div>
      <el-alert v-if="catalog.busy" :title="catalog.job.state==='running'?catalog.job.message:'正在保存资料'" type="info" :closable="false" show-icon />
      <el-alert v-else-if="catalog.job.state==='failed'" title="索引更新失败，旧索引仍保留" :description="catalog.job.message" type="error" :closable="false" show-icon />
      <el-alert v-else-if="catalog.needs_update" title="资料有变更，请更新索引" :description="pendingDescription" type="warning" :closable="false" show-icon />
      <input ref="picker" class="file-picker" type="file" :multiple="!replacement" :accept="accept" aria-label="选择知识库文档" @change="onChoose" />
      <div v-if="selected.length" class="pending-upload"><strong>{{ replacement?`替换：${replacement}`:'待保存资料' }}</strong><ul><li v-for="file in selected" :key="file.name">{{ file.name }} · {{ formatSize(file.size) }}</li></ul><div class="actions"><el-button type="primary" :loading="acting" :disabled="catalog.busy" @click="save">{{ replacement?'保存替换':`保存 ${selected.length} 份资料` }}</el-button><el-button :disabled="acting" @click="clearSelection">取消</el-button></div></div>
      <div class="reference-layout">
        <div class="reference-content"><div class="reference-list-toolbar"><strong>{{ group || '全部资料' }}</strong><el-input v-model="filterText" clearable aria-label="筛选公司文档" placeholder="搜索文件名或版本" :prefix-icon="Search" /></div>
          <el-table :data="visibleDocuments" row-key="name" class="reference-table" height="100%" empty-text="没有匹配的资料">
            <el-table-column label="资料名称" min-width="240"><template #default="{row}"><button type="button" class="document-link" :aria-label="`查看资料：${row.name}`" @click="inspect(row)"><el-icon><Document /></el-icon><span>{{ basename(row.name) }}<small>{{ referenceGroup(row) }} · {{ row.name.split('.').pop().toUpperCase() }} · {{ formatSize(row.size) }}</small></span></button></template></el-table-column>
            <el-table-column label="版本与适用" min-width="145"><template #default="{row}"><span>{{ row.version_info?.version_label || '未标注版本' }}</span><small class="version-state">{{ row.version_info?.confirmed?'已确认':'待确认适用信息' }}</small></template></el-table-column>
            <el-table-column label="检索状态" width="100"><template #default="{row}"><el-tag size="small" :type="row.indexed?'success':'warning'" effect="plain">{{ row.indexed?'已入库':'待更新' }}</el-tag></template></el-table-column>
            <el-table-column label="" width="66"><template #default="{row}"><el-button link type="primary" :aria-label="`资料详情：${row.name}`" @click="inspect(row)">详情</el-button></template></el-table-column>
          </el-table>
          <footer class="reference-footer"><span>{{ visibleDocuments.length }} 份资料</span><span>支持 PDF、图片、Word、Excel、Markdown、TXT · 单份 ≤ 20 MB</span></footer>
        </div>
      </div>
      <el-drawer v-model="detailsVisible" title="资料详情" size="min(640px,96vw)" class="reference-drawer"><template v-if="inspected"><div class="detail-file"><el-icon><Document /></el-icon><h2>{{ basename(inspected.name) }}</h2></div><p class="detail-path">{{ inspected.name }}</p><dl class="document-facts"><div><dt>资料分类</dt><dd>{{ referenceGroup(inspected) }}</dd></div><div><dt>文件大小</dt><dd>{{ formatSize(inspected.size) }}</dd></div><div><dt>修改时间</dt><dd>{{ formatTime(inspected.mtime*1000) }}</dd></div><div><dt>资料类型</dt><dd>{{ inspected.version_info?.kind==='regulation'?'制度规则':'参考资料' }}</dd></div><div><dt>版本</dt><dd>{{ inspected.version_info?.version_label || '未标注' }}</dd></div><div><dt>适用信息</dt><dd>{{ inspected.version_info?.confirmed?'已确认':'待确认' }}</dd></div><div v-if="inspected.version_info?.scope_note"><dt>适用说明</dt><dd>{{ inspected.version_info.scope_note }}</dd></div><div v-if="inspected.version_info?.effective_from"><dt>生效日期</dt><dd>{{ inspected.version_info.effective_from }}</dd></div><div v-if="inspected.version_info?.effective_until"><dt>失效日期</dt><dd>{{ inspected.version_info.effective_until }}</dd></div></dl><div class="detail-actions"><el-button type="primary" :disabled="acting" @click="download(inspected.name)">下载原件</el-button><el-button :disabled="disabled" @click="editVersion(inspected)">编辑版本与适用</el-button></div><div class="detail-maintenance"><el-button link :disabled="disabled" @click="chooseFiles(inspected.name)">替换文件</el-button><el-button link type="danger" :disabled="disabled" @click="remove(inspected.name)">删除文件</el-button></div></template></el-drawer>
      <el-drawer :model-value="!!editing" title="编辑版本与适用" size="min(860px,96vw)" class="reference-drawer" @update:model-value="value=>{if(!value)editing=null}"><KnowledgeVersionEditor v-if="editing" :key="editing.name" :document="editing" :documents="catalog.documents" @close="editing=null" @saved="versionSaved" /></el-drawer>
      <el-drawer v-model="searchVisible" title="检索公司文档" size="min(860px,96vw)" class="reference-drawer">
      <section class="search-section" aria-label="检索公司文档">
        <p>查找已入库资料中的原文片段。</p>
        <p v-if="catalog.needs_update && catalog.index_exists" class="muted">当前试用仍使用上次发布的索引。请更新索引后验证新内容。</p>
        <form class="search-form" @submit.prevent="search">
          <el-input v-model="question" aria-label="检索问题" maxlength="1000" placeholder="例如：技术交底材料的内部审核流程是什么？" />
          <el-button native-type="submit" :loading="searching" :disabled="acting || searching || !catalog.index_exists || !question.trim()">检索</el-button>
        </form>
        <details class="retrieval-options"><summary>检索范围与版本</summary>
        <div class="scope-fields">
          <label>查询用途<el-select v-model="scope.purpose" aria-label="查询用途"><el-option label="现行规则" value="current"/><el-option label="历史规则" value="history"/><el-option label="版本对比" value="compare"/></el-select></label>
          <label>{{ scope.purpose === 'history' ? '业务发生日期（必填）' : '适用日期（默认今天）' }}<el-date-picker v-model="scope.as_of" type="date" value-format="YYYY-MM-DD" aria-label="适用日期" :disabled="scope.purpose==='compare'"/></label>
          <label>组织<el-input v-model="scope.organization" placeholder="与维护名称一致" aria-label="适用组织"/></label>
          <label>地区<el-input v-model="scope.region" aria-label="适用地区"/></label>
          <label>业务<el-input v-model="scope.business" aria-label="适用业务"/></label>
          <label class="scope-sources">{{ scope.purpose==='compare' ? '对比文档（至少两份）' : '限定文档（可选）' }}<el-select v-model="scope.sources" multiple aria-label="限定或对比文档"><el-option v-for="doc in catalog.documents" :key="doc.name" :label="doc.name" :value="doc.name"/></el-select></label>
        </div>
        </details>
        <p v-if="searchError" class="error-message" role="alert">{{ searchError }}</p>
        <div v-if="searchPassages.length" class="passage-list" aria-label="检索原文片段">
          <article v-for="(passage,index) in searchPassages" :key="passage.evidence_id || index" class="passage-card">
            <header><strong>{{ basename(passage.source) }}</strong><el-button link type="primary" @click="download(passage.source)">下载原件</el-button></header>
            <p class="passage-source">{{ passage.source }} · {{ passage.location }}</p>
            <p class="passage-text">{{ passage.text }}</p>
          </article>
          <p v-for="warning in searchWarnings" :key="warning" class="passage-warning">{{ warning }}</p>
        </div>
        <pre v-else-if="searchResult" class="search-result">{{ searchResult }}</pre>
      </section>
      </el-drawer>
    </template>
    <el-skeleton v-else-if="loading" :rows="7" animated />
  </section>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessageBox, ElDatePicker } from 'element-plus'
import {Document,Plus,Search} from '@element-plus/icons-vue'
import {referenceGroups,filterReferences,referenceGroup} from '../referenceLibrary'
import KnowledgeVersionEditor from './KnowledgeVersionEditor.vue'
import { getKnowledge, saveKnowledge, removeKnowledge, rebuildKnowledge, searchKnowledge } from '../api'
import { toast } from '../store'

const catalog = ref(null)
const group=ref(''),filterText=ref(''),searchVisible=ref(false),detailsVisible=ref(false),inspectedName=ref('')
const groups=computed(()=>referenceGroups(catalog.value?.documents))
defineExpose({catalog,groups,group,chooseGroup:value=>{group.value=value}})
const visibleDocuments=computed(()=>filterReferences(catalog.value?.documents,group.value,filterText.value))
const inspected=computed(()=>catalog.value?.documents.find(d=>d.name===inspectedName.value))
const basename=name=>name.split('/').pop()
function inspect(row){inspectedName.value=row.name;detailsVisible.value=true}
const loading = ref(false)
const acting = ref(false)
const error = ref('')
const picker = ref(null)
const selected = ref([])
const replacement = ref('')
const editing = ref(null)
const scope = ref({purpose:'current',as_of:null,organization:'',region:'',business:'',sources:[]})
const question = ref('')
function editVersion(row) { detailsVisible.value=false;editing.value=row }
async function versionSaved() { editing.value=null; clearSearchResult(); await refresh(); toast('success','版本信息已保存，请更新索引后检索') }
const searching = ref(false)
const searchError = ref('')
const searchResult = ref('')
const searchPassages = ref([])
const searchWarnings = ref([])
function clearSearchResult() { searchResult.value = ''; searchPassages.value = []; searchWarnings.value = [] }
const disabled = computed(() => acting.value || catalog.value?.busy || searching.value)
const accept = '.txt,.md,.pdf,.doc,.docx,.xlsx,.xls,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff'
let poll = null
let disposed = false
const pendingDescription = computed(() => {
  if (catalog.value?.index_exists && !catalog.value.version_known) {
    return '已有索引可继续检索。更新一次索引后，会开始记录文档版本、知识片段数和更新时间。'
  }
  const deleted = catalog.value?.removed_count || 0
  return `${deleted ? `有 ${deleted} 份已删除文档仍在旧索引中。` : ''}点击「更新索引」发布当前文档；更新失败时保留旧索引。`
})

async function refresh() {
  clearTimeout(poll)
  loading.value = true
  error.value = ''
  try {
    const next = await getKnowledge()
    if (disposed) return
    const wasRunning = catalog.value?.job.state === 'running'
    catalog.value = next
    if(group.value && !referenceGroups(next.documents).some(g=>g.label===group.value))group.value=''
    if(detailsVisible.value && !next.documents.some(d=>d.name===inspectedName.value))detailsVisible.value=false
    if (wasRunning && next.job.state === 'succeeded') {
      clearSearchResult()
      toast('success', next.job.message)
    }
    if (next.busy) poll = setTimeout(refresh, 2000)
  } catch (e) { error.value = `${e.message}，请点击「刷新状态」重试` }
  finally { loading.value = false }
}
function chooseFiles(name = '') {
  detailsVisible.value=false
  replacement.value = name
  selected.value = []
  picker.value.value = ''
  // multiple 是动态属性，直接同步到原生控件以便当前点击即生效。
  picker.value.multiple = !name
  picker.value.click()
}
function onChoose(event) {
  selected.value = Array.from(event.target.files || [])
}
function clearSelection() {
  selected.value = []
  replacement.value = ''
  if (picker.value) picker.value.value = ''
}
async function save() {
  if (!selected.value.length || disabled.value) return
  if (selected.value.length > 20 || selected.value.some(f => !f.size || f.size > 20 * 1024 * 1024)) {
    toast('error', '请选择 1–20 份非空文档，单个文件不超过 20 MB')
    return
  }
  if (replacement.value) {
    try {
      await ElMessageBox.confirm(`将替换“${replacement.value}”的原文档，无法恢复。更新索引后才会影响检索。`, '替换知识库文档？',
        {type: 'warning', confirmButtonText: '替换文档', cancelButtonText: '取消'})
    } catch { return }
  }
  acting.value = true
  try {
    await saveKnowledge(selected.value, replacement.value)
    clearSelection()
    toast('success', '文档已保存，请更新索引')
    await refresh()
  } catch (e) { toast('error', e.message) }
  finally { acting.value = false }
}
async function remove(name) {
  try {
    await ElMessageBox.confirm(`将永久删除“${name}”的原文档。旧索引中的片段会在下次成功更新索引后移除；已有会话回答与报告不会随之删除。`,
      '删除知识库文档？', {type: 'warning', confirmButtonText: '删除文档', cancelButtonText: '取消'})
  } catch { return }
  acting.value = true
  try {
    await removeKnowledge(name)
    clearSearchResult()
    toast('success', '文档已删除，请更新索引以移除旧片段')
    await refresh()
  } catch (e) { toast('error', e.message) }
  finally { acting.value = false }
}
async function rebuild() {
  if (disabled.value) return
  if (!catalog.value.documents.length && catalog.value.index_exists) {
    try {
      await ElMessageBox.confirm('当前没有文档。更新后会清空知识库索引中的全部旧片段。', '更新为空知识库？',
        {type:'warning', confirmButtonText:'清空并更新索引', cancelButtonText:'取消'})
    } catch { return }
  }
  acting.value = true
  try {
    await rebuildKnowledge()
    clearSearchResult()
    await refresh()
  } catch (e) { toast('error', e.message) }
  finally { acting.value = false }
}
async function search() {
  if (acting.value || searching.value || !question.value.trim()) return
  searching.value = true
  clearSearchResult()
  searchError.value = ''
  try {
    const result = await searchKnowledge(question.value, scope.value)
    searchResult.value = result.result
    searchPassages.value = result.passages || []
    searchWarnings.value = result.warnings || []
  }
  catch (e) { searchError.value = e.message }
  finally { searching.value = false }
}
function download(name) {
  const link = document.createElement('a')
  link.href = `/api/knowledge/documents/download?${new URLSearchParams({name})}`
  link.download = name.split('/').pop()
  link.click()
}
function formatSize(size) {
  return size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`
}
function formatTime(value) {
  return value == null ? '尚未记录' : new Date(value).toLocaleString('zh-CN', {hour12:false})
}
onMounted(refresh)
onUnmounted(() => { disposed = true; clearTimeout(poll) })
</script>

<style scoped>
.knowledge-panel{height:100%;min-height:0;display:flex;flex-direction:column;gap:14px;overflow:auto}.library-toolbar{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-shrink:0}h2{font-size:18px;margin:0 0 6px}h3{font-size:12px;color:var(--text-secondary);font-weight:500;margin:4px 10px 12px}p{font-size:13px;line-height:1.7;color:var(--text-secondary);margin:0}.actions{display:flex;align-items:center;gap:8px;flex-shrink:0}.actions .el-button+.el-button{margin-left:0}.actions .el-icon{margin-right:5px}.index-summary{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;font-size:12px;color:var(--text-secondary);padding:10px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);flex-shrink:0}.index-actions{margin-left:auto;display:flex;gap:14px}.reference-layout{display:flex;flex-direction:column;flex:1;min-height:280px}.document-link:focus-visible{outline:2px solid var(--primary);outline-offset:2px}.reference-content{display:flex;flex-direction:column;flex:1;min-height:0;min-width:0}.reference-list-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;font-size:14px}.reference-list-toolbar .el-input{max-width:290px}.reference-table{flex:1;min-height:180px;font-size:13px}.reference-table :deep(.el-table__cell){padding:13px 0}.document-link{display:flex;align-items:flex-start;gap:9px;background:none;border:0;padding:0;font:inherit;color:var(--text-primary);cursor:pointer;text-align:left;line-height:1.65}.document-link>.el-icon{color:var(--text-secondary);font-size:17px;margin-top:3px;flex-shrink:0}.document-link span{overflow-wrap:anywhere}.document-link:hover{color:var(--primary)}.document-link small,.version-state{display:block;color:var(--text-secondary);font-size:11px;margin-top:4px}.reference-footer{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;border-top:1px solid var(--border);padding:12px 0;font-size:11px;color:var(--text-secondary)}.file-picker{display:none}.pending-upload{padding:14px;border:1px solid var(--border);border-radius:8px;background:var(--bg);font-size:13px;flex-shrink:0}.pending-upload ul{line-height:1.8;overflow-wrap:anywhere;padding-left:18px}.detail-file{display:flex;align-items:flex-start;gap:12px}.detail-file>.el-icon{font-size:26px;color:var(--primary);flex-shrink:0}.detail-file h2{font-size:20px;overflow-wrap:anywhere;line-height:1.6}.detail-path{overflow-wrap:anywhere;margin:8px 0 20px;font-size:12px}.document-facts{margin:0 0 24px}.document-facts>div{display:grid;grid-template-columns:90px minmax(0,1fr);gap:16px;padding:12px 0;border-bottom:1px solid var(--border);font-size:13px;line-height:1.7}.document-facts dt{color:var(--text-secondary)}.document-facts dd{margin:0;overflow-wrap:anywhere}.detail-actions{display:flex;flex-wrap:wrap;gap:10px}.detail-actions .el-button+.el-button{margin-left:0}.detail-maintenance{display:flex;gap:18px;margin-top:24px}.search-form{display:flex;gap:10px;margin:20px 0}.search-section h2{font-size:18px}.retrieval-options{margin:18px 0}.retrieval-options summary{cursor:pointer;color:var(--text-secondary);font-size:13px}.scope-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:16px}.scope-fields label{display:flex;flex-direction:column;gap:7px;font-size:13px}.scope-fields :deep(.el-date-editor){width:100%}.scope-sources{grid-column:1/-1}.passage-list{display:grid;gap:16px}.passage-card{border:1px solid var(--border);border-radius:8px;padding:18px}.passage-card header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;font-size:14px}.passage-card header strong{overflow-wrap:anywhere}.passage-source{font-size:11px;overflow-wrap:anywhere;margin:6px 0 14px}.passage-text{white-space:pre-wrap;overflow-wrap:anywhere;color:var(--text-primary);line-height:1.9}.passage-warning{font-size:12px;color:var(--text-secondary)}.search-result{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:13px;line-height:1.8;padding:16px;background:var(--bg);border:1px solid var(--border);border-radius:8px}.error-message{color:var(--danger-text);font-size:13px}.knowledge-panel :deep(.version-editor){margin:0;padding:0;border:0;background:none}@media(max-width:850px){.library-toolbar{flex-wrap:wrap}}@media(max-width:600px){.reference-layout{display:flex;flex-direction:column;min-height:340px}.reference-content{min-height:300px}.reference-list-toolbar{flex-wrap:wrap}.reference-list-toolbar .el-input{max-width:none}.scope-fields{grid-template-columns:1fr}}
</style>
