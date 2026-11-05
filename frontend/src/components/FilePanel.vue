<template>
  <div class="file-panel" :class="{embedded}">
    <div v-if="!embedded" class="panel-title">
      <el-icon><FolderOpened /></el-icon>
      <span>文件</span>
      <span v-if="session" class="file-count">{{ resultFiles.length }} 个生成文件</span>
    </div>

    <div v-if="session" class="session-context">
      <span>当前会话</span>
      <p :title="session.query">{{ session.query }}</p>
    </div>
    <el-tabs v-model="activeTab" class="tabs">
      <!-- Tab1：上传文件 -->
      <el-tab-pane label="上传附件" name="upload">
        <p class="attachment-hint">上传供本会话分析的文件，不会写入知识库或业务数据库。</p>
        <p v-if="busy" class="attachment-hint">任务运行中，请先终止任务再上传或删除附件。</p>
        <el-upload
          v-if="session"
          v-model:file-list="chooserFiles"
          :disabled="busy || uploading"
          class="upload-zone"
          drag
          multiple
          accept=".csv,.xlsx,.xls,.txt,.md,.doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff"
          :auto-upload="false"
          :show-file-list="false"
          :on-change="onFileChange"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖入文件，或<em>选择文件</em></div>
          <div class="upload-help">每个文件 ≤20MB，每批 ≤20 个 / 100MB</div>
        </el-upload>
        <div v-else class="empty-block">
          <div class="empty-icon"><el-icon :size="22"><UploadFilled /></el-icon></div>
          <p>可在下方任务输入区先添加附件</p>
          <span>选好文件与分析任务，一次提交即可</span>
        </div>

        <div v-if="uploadList.length" class="upload-list">
          <div v-for="(f, i) in uploadList" :key="i" class="upload-item">
            <div class="file-card">
              <el-icon :color="fileIconColor(f.name)"><Document /></el-icon>
              <span class="fname">{{ f.name }}</span>
              <span class="fsize">{{ formatSize(f.size) }}</span>
              <el-button link size="small" type="danger" @click="removeFile(i)">
                <el-icon><Close /></el-icon>
              </el-button>
            </div>
            <div v-if="excelPreviews[f.name]" class="excel-preview">
              <div class="preview-head">
                <span>{{ excelPreviews[f.name].sheetName }} · {{ excelPreviews[f.name].totalRows }} 行 × {{ excelPreviews[f.name].totalCols }} 列</span>
                <span v-if="excelPreviews[f.name].truncated" class="preview-hint">仅预览前 5 行 / 8 列</span>
              </div>
              <div v-if="excelPreviews[f.name].error" class="preview-error">{{ excelPreviews[f.name].error }}</div>
              <div v-else class="preview-table-wrap">
                <table v-resizable-columns class="preview-table">
                  <thead>
                    <tr><th v-for="(h, hi) in excelPreviews[f.name].headers" :key="hi">{{ h || '—' }}</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, ri) in excelPreviews[f.name].rows" :key="ri">
                      <td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          <div v-if="previewing" class="preview-loading">正在解析表格预览…</div>
          <el-button class="upload-btn" type="primary" size="small" :loading="uploading" :disabled="busy" @click="doUpload">上传 {{ uploadList.length }} 个文件</el-button>
        </div>
        <template v-if="session">
          <div v-if="attachments.length" class="attachment-action">
            <p class="attachment-hint">附件会保留在本会话中；上传后需重新运行，已有回答不会自动更新。</p>
            <el-button type="primary" plain size="small" :disabled="busy || uploading || refreshing || Boolean(deleting) || session.preparing" @click="$emit('rerun', session.thread_id)">使用附件重新运行</el-button>
          </div>
          <div class="result-toolbar attachment-toolbar">
            <span>已上传附件 · {{ attachments.length }} 个</span>
            <el-button link size="small" :loading="refreshing" @click="refresh">刷新</el-button>
          </div>
          <p v-if="attachmentError" class="preview-error" role="alert">{{ attachmentError }}</p>
          <p v-else-if="!attachments.length && !refreshing" class="attachment-hint">尚未上传文件</p>
          <div class="upload-list">
            <div v-for="f in attachments" :key="f.name" class="file-card">
              <el-icon :color="fileIconColor(f.name)"><Document /></el-icon>
              <div class="file-main">
                <span class="fname" :title="f.name">{{ f.name }}</span>
                <span class="file-meta">{{ formatSize(f.size) }} · {{ formatTime(f.mtime) }}</span>
              </div>
              <el-button link size="small" type="danger" :disabled="busy || uploading || Boolean(deleting)"
                :aria-label="`删除附件 ${f.name}`" @click="removeAttachment(f)">
                {{ deleting === f.name ? '删除中…' : '删除' }}
              </el-button>
            </div>
          </div>
          <section v-if="attachments.length >= 2" class="attachment-comparison" aria-label="对比会话附件">
            <h3>对比会话附件</h3>
            <label>基准文件<el-select v-model="compareLeft" placeholder="选择基准文件" :disabled="busy || uploading" aria-label="附件对比基准文件">
              <el-option v-for="file in attachments" :key="file.name" :label="file.name" :value="file.name" :disabled="file.name===compareRight" />
            </el-select></label>
            <label>对比文件<el-select v-model="compareRight" placeholder="选择对比文件" :disabled="busy || uploading" aria-label="附件对比目标文件">
              <el-option v-for="file in attachments" :key="file.name" :label="file.name" :value="file.name" :disabled="file.name===compareLeft" />
            </el-select></label>
            <p class="attachment-hint">文档对比内容变化；案件表按案号核对增删与字段变化。</p>
            <el-button type="primary" plain size="small" :disabled="busy || uploading || refreshing || Boolean(deleting) || !compareLeft || !compareRight" @click="compareFiles">开始对比</el-button>
          </section>
        </template>
      </el-tab-pane>

      <!-- Tab2：会话成果 -->
      <el-tab-pane label="生成文件" name="result">
        <div v-if="session" class="result-toolbar">
          <span>{{ resultFiles.length }} 个文件 · 最新在前</span>
          <el-button link size="small" :loading="refreshing" @click="refresh">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
        </div>
        <p class="attachment-hint">下载本会话生成的报告等文件。问答内容请到「结果预览」查看。</p>
        <p v-if="resultError" class="preview-error" role="alert">{{ resultError }}</p>
        <div v-else-if="refreshing" class="attachment-hint" role="status">正在加载文件…</div>
        <div v-else-if="!session || !resultFiles.length" class="empty-block">
          <div class="empty-icon"><el-icon :size="22"><FolderOpened /></el-icon></div>
          <p>暂无生成文件</p>
          <span>需要保存文件时，请在提问中要求生成报告；普通问答可能不生成文件。</span>
        </div>
        <div v-else class="result-list">
          <div v-for="f in resultFiles" :key="f.path" class="file-card result-card">
            <el-icon class="file-icon" :color="f.type === 'file' && f.name.endsWith('.pdf') ? '#F56C6C' : '#165DFF'">
              <Document />
            </el-icon>
            <div class="file-main">
              <span class="fname" :title="f.name">{{ f.name }}</span>
              <span class="file-meta">
                <el-tag v-if="isLatest(f)" size="small" type="success" effect="plain" class="latest-tag">最新</el-tag>
                <span>{{ f.type === 'dir' ? '目录' : formatSize(f.size) }}</span>
                <span>{{ formatTime(f.mtime) }}</span>
              </span>
            </div>
            <el-button v-if="f.type === 'file'" link size="small" type="primary" class="download-btn" @click="download(f)">
              <el-icon><Download /></el-icon><span>下载</span>
            </el-button>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { uploadFiles, listFiles, downloadFile, listAttachments, deleteAttachment } from '../api'
import { ElMessageBox } from 'element-plus'
import { toast } from '../store'
import { sessionOutputFiles, comparisonQuery } from '../sessionFiles'

const props = defineProps({ session: Object, embedded: Boolean })
const emit=defineEmits(['rerun','compare'])
const compareLeft=ref('')
const compareRight=ref('')
const activeTab = ref('upload')
const uploadList = ref([])
const chooserFiles = ref([])
const attachments = ref([])
watch(attachments,files=>{
  const names=new Set(files.map(file=>file.name))
  if (!names.has(compareLeft.value)) compareLeft.value=''
  if (!names.has(compareRight.value)) compareRight.value=''
})
const attachmentError = ref('')
const resultError = ref('')
const refreshing = ref(false)
const deleting = ref('')
const busy = computed(() => props.session?.status === 'running' || props.session?.preparing)
const uploading = ref(false)
const workFiles = ref([])
const resultFiles = computed(() => sessionOutputFiles(workFiles.value, attachments.value, props.session?.session_dir))
const latestArtifacts = computed(() => {
  const files = resultFiles.value.filter((f) => f.type === 'file')
  const newest = (exts) => files
    .filter((f) => exts.some((ext) => f.name.toLowerCase().endsWith(ext)))
    .sort((a, b) => (b.mtime || 0) - (a.mtime || 0))[0]
  return { pdf: newest(['.pdf']), md: newest(['.md', '.markdown']) }
})
const excelPreviews = ref({})
const previewing = ref(false)
let previewToken = 0
const PREVIEW_EXT = /\.(xlsx|xls|csv)$/i
const PREVIEW_MAX_BYTES = 5 * 1024 * 1024

watch(
  () => [props.session?.thread_id, props.session?.session_dir, props.session?.status],
  () => {
    workFiles.value = []
    attachments.value = []
    refresh()
  },
  { immediate: true }
)
watch(
  () => props.session?.thread_id,
  () => {
    uploadList.value = []
    chooserFiles.value = []
    excelPreviews.value = {}
    previewToken++
    compareLeft.value=compareRight.value=''
  }
)

function compareFiles() {
  try {emit('compare',{threadId:props.session.thread_id,query:comparisonQuery(compareLeft.value,compareRight.value)})}
  catch(error) {toast('warning',error.message)}
}

async function refresh() {
  const id = props.session?.thread_id
  if (!id) return
  refreshing.value = true
  attachmentError.value = ''
  resultError.value = ''
  const directory = props.session.session_dir
  const [uploaded, results] = await Promise.allSettled([
    listAttachments(id),
    directory ? listFiles(directory, id, true) : Promise.resolve({ files: [] }),
  ])
  if (id !== props.session?.thread_id) return
  refreshing.value = false
  if (uploaded.status === 'fulfilled') attachments.value = uploaded.value.files || []
  else attachmentError.value = `附件列表加载失败：${uploaded.reason.message}，请刷新重试`
  if (results.status === 'fulfilled') workFiles.value = results.value.files || []
  else resultError.value = `生成文件加载失败：${results.reason.message}，请刷新重试`
}

async function removeAttachment(file) {
  const id = props.session?.thread_id
  if (!id || busy.value) return
  try {
    await ElMessageBox.confirm(
      `将删除附件“${file.name}”及其工作目录副本。已有回答、证据和报告可能仍包含该文件的内容；如需清理全部内容，请删除会话。`,
      '删除附件？',
      { type: 'warning', confirmButtonText: '删除附件', cancelButtonText: '取消' }
    )
  } catch { return }
  deleting.value = file.name
  try {
    await deleteAttachment(id, file.name)
    toast('success', '附件及工作目录副本已删除')
    if (id === props.session?.thread_id) await refresh()
  } catch (error) {
    toast('error', error.message)
  } finally {
    deleting.value = ''
  }
}

function onFileChange(file, fileList) {
  const valid = fileList.filter(f => /\.(csv|xlsx|xls|txt|md|docx|pdf)$/i.test(f.name) && f.size > 0 && f.size <= 20 * 1024 * 1024)
  if (valid.length !== fileList.length) toast('warning', '已移除不支持、为空或超过 20MB 的文件')
  chooserFiles.value = valid.slice(0,20)
  uploadList.value = chooserFiles.value.map((f) => f.raw)
  buildExcelPreviews(uploadList.value)
}

async function buildExcelPreviews(files) {
  const token = ++previewToken
  const next = {}
  const excelFiles = files.filter((f) => PREVIEW_EXT.test(f.name))
  if (!excelFiles.length) {
    previewing.value = false
    excelPreviews.value = {}
    return
  }
  previewing.value = true
  for (const f of excelFiles) {
    if (f.size > PREVIEW_MAX_BYTES) {
      next[f.name] = { error: '文件超过 5MB，上传后由后端解析' }
      continue
    }
    try {
      let rows
      let sheetName = 'Sheet1'
      if (/\.csv$/i.test(f.name)) {
        const { default: Papa } = await import('papaparse')
        const parsed = Papa.parse(await f.text(), { skipEmptyLines: true })
        rows = parsed.data
      } else if (/\.xlsx$/i.test(f.name)) {
        const { default: readXlsxFile } = await import('read-excel-file')
        rows = await readXlsxFile(f)
      } else {
        next[f.name] = { error: '旧版 .xls 请上传后由后端解析' }
        continue
      }
      if (!rows || !rows.length) {
        next[f.name] = { error: '文件中没有可预览的数据' }
        continue
      }
      const cellText = (v) => {
        if (v == null) return ''
        if (v instanceof Date) return v.toLocaleDateString('zh-CN')
        return String(v)
      }
      const headers = (rows[0] || []).slice(0, 8).map(cellText)
      const body = rows.slice(1, 6).map((row) => {
        const cells = (row || []).slice(0, 8).map(cellText)
        while (cells.length < headers.length) cells.push('')
        return cells
      })
      next[f.name] = {
        sheetName,
        headers,
        rows: body,
        totalRows: Math.max(rows.length - 1, 0),
        totalCols: rows[0]?.length || 0,
        truncated: rows.length > 6 || (rows[0]?.length || 0) > 8,
      }
    } catch (e) {
      next[f.name] = { error: `表格预览失败：${e.message || e}` }
    }
  }
  if (token === previewToken) {
    excelPreviews.value = next
    previewing.value = false
  }
}

function removeFile(i) {
  chooserFiles.value.splice(i, 1)
  uploadList.value.splice(i, 1)
  buildExcelPreviews(uploadList.value)
}

async function doUpload() {
  if (!props.session?.thread_id || !uploadList.value.length || busy.value) return
  const id = props.session.thread_id
  uploading.value = true
  try {
    let res
    try { res = await uploadFiles(id, uploadList.value) }
    catch (error) {
      if (error.detail?.code !== 'attachment_conflict') throw error
      try {
        await ElMessageBox.confirm(`将替换以下同名附件：${error.detail.files.join('、')}。已有回答不会自动更新，替换后需重新运行。`, '替换附件？', {type:'warning',confirmButtonText:'替换附件',cancelButtonText:'取消'})
      } catch { return }
      res = await uploadFiles(id, uploadList.value, error.detail.files)
    }
    toast('success', `已上传 ${(res.files || []).length} 个文件`)
    if (id === props.session?.thread_id) {
      uploadList.value = []
      chooserFiles.value = []
      excelPreviews.value = {}
      previewToken++
      await refresh()
    }
    if (id === props.session?.thread_id) toast('info', '附件已保存，点击「使用附件重新运行」开始分析')
  } catch (e) {
    toast('error', e.message || '上传失败')
    if (id === props.session?.thread_id) await refresh()
  } finally {
    uploading.value = false
  }
}

async function download(f) {
  // 越权拦截：文件必须位于当前会话目录
  if (props.session?.session_dir && !f.path.startsWith(props.session.session_dir + '/')) {
    toast('error', '越权访问拦截：非当前会话文件')
    return
  }
  try {
  const res = await downloadFile(f.path, props.session.thread_id)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = f.name
  a.click()
  URL.revokeObjectURL(url)
  } catch (error) { toast('error', error.message) }
}

function isLatest(f) {
  return Boolean(f && f.type === 'file' && (
    f.path === latestArtifacts.value.pdf?.path || f.path === latestArtifacts.value.md?.path
  ))
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function formatSize(size) {
  if (size == null) return ''
  if (size < 1024) return size + ' B'
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB'
  return (size / 1024 / 1024).toFixed(1) + ' MB'
}
function fileIconColor(name) {
  if (name.endsWith('.pdf')) return '#F56C6C'
  if (name.endsWith('.md') || name.endsWith('.txt')) return '#165DFF'
  if (name.endsWith('.xlsx') || name.endsWith('.xls')) return '#67C23A'
  return '#909399'
}
</script>

<style scoped>
.attachment-comparison {display:flex;flex-direction:column;gap:10px;margin-top:16px;padding:12px;border:1px solid var(--border);border-radius:8px;}
.attachment-comparison h3 {font-size:13px;margin:0;}
.attachment-comparison label {display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--text-secondary);}
.attachment-comparison .el-select {width:100%;min-width:0;}
.attachment-hint {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
  margin: 6px 0;
}
.attachment-toolbar { margin-top: 14px; }
.session-context { padding: 0 0 10px; }
.session-context > span { color: var(--text-secondary); font-size: 12px; }
.session-context p { margin: 4px 0 0; color: var(--text); font-size: 13px; line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.attachment-action { margin-top: 12px; }
.upload-help { margin-top: 4px; font-size: 12px; color: var(--text-secondary); }


.file-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.panel-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 8px;
}
.file-count {
  margin-left: auto;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  background: var(--bg);
  padding: 2px 7px;
  border-radius: 999px;
}
.result-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  padding: 0 2px 6px;
}
.empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 34px 8px;
  text-align: center;
  color: var(--text-secondary);
}
.empty-block .empty-icon {
  width: 44px;
  height: 44px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  color: var(--primary);
  background: var(--primary-soft);
  margin-bottom: 6px;
}
.empty-block p {
  font-size: 14px;
  color: var(--text);
}
.empty-block span {
  font-size: 13px;
  line-height: 1.5;
}
.tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.tabs :deep(.el-tabs__header) { flex-shrink: 0; margin-bottom: 10px; }
.tabs :deep(.el-tabs__content) { flex: 1; min-height: 0; overflow: hidden; }
.tabs :deep(.el-tab-pane) { height: 100%; overflow: auto; overscroll-behavior: contain; padding-right: 4px; scrollbar-gutter: stable; }
.embedded .upload-zone :deep(.el-upload-dragger) { padding: 12px 8px; }
.embedded .upload-zone :deep(.el-icon--upload) { font-size: 24px; margin-bottom: 3px; }
.embedded .empty-block { padding: 20px 8px; }
.upload-list,
.result-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}
.file-card {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 12px;
  background: var(--card-bg);
  transition: border-color 0.2s, background 0.2s;
}
.upload-zone :deep(.el-upload) {
  width: 100%;
}
.upload-zone :deep(.el-upload-dragger) {
  padding: 22px 10px;
}
.upload-zone :deep(.el-icon--upload) {
  font-size: 36px;
  margin-bottom: 6px;
  color: var(--text-secondary);
}
.upload-btn {
  align-self: flex-end;
  min-width: 96px;
}
.file-icon {
  margin-top: 2px;
  flex-shrink: 0;
}
.file-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.file-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  font-size: 12px;
  color: var(--text-secondary);
}
.result-card .fname {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.45;
  word-break: break-all;
}
.download-btn {
  flex-shrink: 0;
  margin-top: 1px;
}
.file-card:hover {
  border-color: var(--primary);
  background: var(--primary-soft);
}
.upload-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.excel-preview {
  margin-left: 22px;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--bg);
}
.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 5px 8px;
  font-size: 13px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border);
}
.preview-hint {
  white-space: nowrap;
}
.preview-error {
  padding: 8px;
  font-size: 13px;
  color: var(--warning);
}
.preview-table-wrap {
  max-height: 180px;
  overflow: auto;
}
.preview-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.preview-table th,
.preview-table td {
  border-right: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  padding: 3px 6px;
  text-align: left;
  white-space: nowrap;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.preview-table th {
  position: sticky;
  top: 0;
  background: var(--card-bg);
  color: var(--text);
  font-weight: 600;
}
.preview-loading {
  font-size: 13px;
  color: var(--text-secondary);
  padding: 2px 0;
}
.fname {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.result-card .fname {
  white-space: normal;
}
.fsize {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
}
.latest-tag {
  flex-shrink: 0;
  transform: scale(0.9);
  transform-origin: left center;
}
</style>
