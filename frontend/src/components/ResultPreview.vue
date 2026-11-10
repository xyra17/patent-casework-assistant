<template>
  <div class="result-preview glass-card">
    <el-descriptions v-if="session?.result" :column="1" border size="small" title="分项结果（内部审查）">
      <el-descriptions-item v-for="item in assessmentItems" :key="item.key" :label="item.label">
        <el-tag size="small" :type="item.type">{{ item.status }}</el-tag>
        <span> {{ item.detail }}</span>
      </el-descriptions-item>
    </el-descriptions>
    <el-alert v-if="boundary" :title="`业务结果：${boundary.label}`" :type="boundary.type" :closable="false" show-icon>
      <p v-for="issue in boundary.issues" :key="issue">{{ issue }}</p>
      <p v-if="boundary.professional_judgments.length">专业结论尚未经人工确认；支持证据和Agent初步分析须分别阅读。</p>
    </el-alert>
    <el-alert
      v-if="session?.degraded"
      class="degraded-alert"
      type="warning"
      :closable="false"
      show-icon
      :title="qualitySummaryText(session.quality_summary) || '当前结果尚未通过完整质量校验，已保留可用结果和未达标原因。'"
    />
    <div class="tabs-shell" :class="{'has-session-files':hasSessionFiles,'execution-expanded':executionExpanded}">
      <div class="preview-head-actions">
      <button v-if="hasSessionFiles" type="button" class="session-files-button" @click="emit('show-files')"><el-icon aria-hidden="true"><FolderOpened /></el-icon>会话附件与报告</button>
      <button id="execution-details-toggle" type="button" class="execution-details-toggle" :class="{attention:session?.status==='failed' || session?.degraded}"
        :aria-expanded="executionExpanded" aria-controls="execution-details" @click="emit('toggle-execution')">
        <span v-if="session?.status==='running'" class="execution-running-dot" aria-hidden="true"></span>
        <el-icon v-else-if="session?.status==='failed' || session?.degraded" aria-hidden="true"><Warning /></el-icon>
        <el-icon v-else aria-hidden="true"><Monitor /></el-icon>
        <span>{{ executionExpanded ? '收起详情' : session?.status==='failed' || session?.degraded ? '查看执行异常' : '查看执行详情' }}</span>
        <el-icon aria-hidden="true"><ArrowRight v-if="executionExpanded"/><ArrowLeft v-else/></el-icon>
      </button>
      </div>

    <el-tabs v-model="activeTab" class="tabs">
      <el-tab-pane label="执行结果" name="answer">
        <ClarificationPanel v-if="session?.status==='awaiting_input' && session.clarification" :session="session" @answer="emit('clarify',$event)" @show-files="emit('show-files')" @show-evidence="emit('show-evidence',$event)" />
        <el-empty v-else-if="!session?.result" :description="session?.status==='running'?'任务执行中，等待执行结果':'暂无结果'" :image-size="80"/>
        <div v-else class="answer-wrap">
            <div v-if="activeTab==='answer' && session?.result" class="answer-actions" aria-label="执行结果操作">
              <button v-if="evidence.length" type="button" class="all-evidence" @click="emit('show-evidence','')"><el-icon aria-hidden="true"><Link /></el-icon>查看来源（{{ evidence.length }}）</button>
              <span v-if="evidence.length && answerContent" class="answer-action-divider" aria-hidden="true"></span>
              <el-button v-if="answerContent" class="copy-answer" link size="small" type="primary" @click="copyAnswer"><el-icon aria-hidden="true"><CopyDocument /></el-icon>复制结果</el-button>
            </div>

          <div class="answer-content" tabindex="0" aria-label="执行结果正文，可滚动">
          <el-alert v-if="validationNotice" :title="validationNotice" type="warning" :closable="false" show-icon />
          <section v-if="boundary?.objective_comparison" aria-label="程序文字比较">
            <h3>自动文字比较 · {{ boundary.comparisonVerified ? '已核验解析文字' : '未完成' }}</h3>
            <p>{{ boundary.objective_comparison.reason }}；不表示技术机制相同、替换或可借鉴。</p>
            <div class="boundary-table-scroll" v-if="boundary.comparisonVerified">
              <table class="boundary-table"><thead><tr><th>文字关系</th><th>基准原句与位置</th><th>修订原句与位置</th></tr></thead><tbody>
                <tr v-for="(record,index) in boundary.comparisonRows" :key="index">
                  <td>{{ {unchanged:'未变化',modified:'文字修改',added:'新增文字',deleted:'删除文字'}[record.kind] || record.kind }}</td>
                  <td v-for="side in ['left','right']" :key="side">
                    <span>{{ boundary.objective_comparison[side]?.file }} [{{ boundary.objective_comparison[side]?.evidence_id }}]</span>
                    <p v-for="(line,i) in record[side]?.lines || []" :key="i">{{ line.location }}：{{ line.text || '（空行）' }}</p>
                    <span v-if="!record[side]">该侧无对应文字</span>
                  </td>
                </tr>
              </tbody></table>
            </div>
          </section>
          <section v-if="boundary?.professional_judgments.length" aria-label="专业判断待确认">
            <h3>待人工确认的具体判断</h3>
            <ul><li v-for="item in boundary.professional_judgments" :key="item.judgment">{{ item.judgment }}：待人工确认。{{ item.reason }}</li></ul>
            <p>未记录人工审核身份及确认结果。</p>
          </section>
          <section v-if="boundary?.sources.length" aria-label="原文与候选依据">
            <h3>{{ boundary.profile === 'disclosure-search' ? '检索候选与原文依据（检索排序不代表专业结论）' : '原文依据' }}</h3>
            <article v-for="source in boundary.sources" :key="source.id">
              <strong>[{{ source.id }}] {{ source.case_id }} {{ source.title }}</strong>
              <p>来源：{{ source.source || source.title }} · {{ source.valid ? '已保存来源记录' : '来源绑定未完成' }}</p>
              <p>版本：{{ source.revision || source.content_sha256 }} · 定位：{{ typeof source.location === 'string' ? source.location : JSON.stringify(source.location) }}</p>
              <blockquote>{{ source.excerpt }}</blockquote>
            </article>
          </section>
          <h3 v-if="boundary">{{ boundary.candidateLabel }}</h3>
          <div v-if="answerContent" class="md-body answer-body" v-html="renderContent(answerContent)" @click="onMarkdownClick"></div>
          <div v-if="session.query_result?.knowledge_applicability?.length" class="answer-version-notes">
            <p v-for="check in session.query_result.knowledge_applicability" :key="check.evidence_id">
              {{ check.evidence_id }} · {{ check.version?.version_label || '版本未命名' }} · {{ {current:'现行查询',history:'历史查询',compare:'版本对比（不表示现行有效）'}[check.purpose] }} · 适用日期 {{ check.as_of }}
              <span v-if="!check.verified"> · 适用待核实：{{ check.warnings?.join('；') }}</span>
            </p>
          </div>
          </div>
        </div>
      </el-tab-pane>
      <el-tab-pane label="任务理解" name="understanding">
        <TaskUnderstanding :key="session?.thread_id" :session="session" @rerun="(id, sources) => emit('rerun', id, sources)" @refresh="emit('refresh', $event)" />
      </el-tab-pane>

      <el-tab-pane label="执行记录" name="executions" lazy><ExecutionHistory :session="session" /></el-tab-pane>

      <!-- 图表 -->
      <el-tab-pane v-if="charts.length" :label="`图表 (${charts.length})`" name="charts" class="charts-pane">
        <div class="chart-toolbar">
          <div class="chart-overview"><strong>{{ charts.length }} 张图表</strong><span>上下滚动查看 · 点击图形查看明细</span></div>
          <div class="chart-tools">
            <el-select v-model="selectedChart" class="chart-picker" size="small" aria-label="跳转图表" placeholder="跳转到指定图表" @change="jumpToChart">
              <el-option v-for="(item, index) in charts" :key="item.id" :value="item.id" :label="`${index + 1}. ${item.chart.title}`" />
            </el-select>
            <el-select v-model="chartPalette" size="small" class="palette-select" title="图表配色" aria-label="图表配色" popper-class="chart-palette-menu">
              <template #label="{label}"><span class="palette-label"><span>{{ label }}</span><span class="palette-swatches" aria-hidden="true"><i v-for="color in (CHART_PALETTES[chartPalette]?.colors || []).slice(0,3)" :key="color" :style="{backgroundColor:color}"></i></span></span></template>
              <el-option v-for="(palette, key) in CHART_PALETTES" :key="key" :label="palette.label" :value="key"><span class="palette-option"><span>{{ palette.label }}</span><span class="palette-swatches" aria-hidden="true"><i v-for="color in palette.colors.slice(0,5)" :key="color" :style="{backgroundColor:color}"></i></span></span></el-option>
            </el-select>
            <el-button size="small" type="primary" plain :loading="exporting" @click="exportChartReport">
              <el-icon><Download /></el-icon>导出全部 PDF
            </el-button>
          </div>
        </div>
        <div ref="chartScroll" class="chart-grid" tabindex="0" aria-label="图表列表，可上下滚动">
          <ChartView v-for="(item, index) in charts" :id="`chart-${item.id}`" :key="item.id" :spec="item.chart" :position="`图表 ${index + 1}/${charts.length}`" @drilldown="openDrilldown" @show-evidence="emit('show-evidence', $event)" />
        </div>
      </el-tab-pane>

      <!-- PDF 预览（仅 PDF 任务） -->
      <el-tab-pane v-if="pdfRequired" label="PDF 预览" name="pdf">
        <div class="pdf-toolbar">
          <el-button link size="small" @click="zoom(0.8)"><el-icon><ZoomOut /></el-icon></el-button>
          <el-button link size="small" @click="zoom(1.2)"><el-icon><ZoomIn /></el-icon></el-button>
          <el-button link size="small" @click="zoom(1)">重置</el-button>
        </div>
        <el-empty v-if="!pdfUrl" description="PDF 生成中或不存在" :image-size="80" />
        <iframe v-else :src="pdfUrl" class="pdf-frame" :style="{ transform: `scale(${scale})` }"></iframe>
      </el-tab-pane>
    </el-tabs>
    </div>

    <el-drawer v-model="detailVisible" title="数据明细" size="62%" class="drilldown-drawer">
      <div class="drilldown-meta">
        <span>来源表：{{ drilldown.table || '—' }}</span>
        <span>筛选：{{ drilldown.filters.map((item) => `${item.field} = ${item.value}`).join(' 且 ') }}</span>
        <span>本次返回 {{ drilldown.rows.length }} 行</span>
        <span>{{ evidenceScopeText(drilldown) }}</span>
        <span v-if="drilldown.nextLevel">语义层级：下一层 {{ drilldown.nextLevel }}</span>
      </div>
      <el-table scrollbar-always-on class="app-resizable-table" v-loading="drilldown.loading" :data="drilldownRowObjects" height="calc(100vh - 190px)" border size="small">
        <el-table-column v-for="column in drilldown.columns" :key="column" :prop="column" :label="column" min-width="120" show-overflow-tooltip />
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { statisticsNotice } from '../statisticalValidation.js'
import { evidenceScopeText } from '../evidenceScope.js'
import { ref, computed, watch, nextTick, defineAsyncComponent } from 'vue'
import { parseResultMarkdown } from '../resultMarkdown'
import DOMPurify from 'dompurify'
import { listFiles, listAttachments, downloadFile, downloadChartReport, runDrilldown } from '../api'
import { CHART_PALETTES, setChartPalette, store, toast } from '../store'
const ChartView = defineAsyncComponent(() => import('./ChartView.vue'))
import { loadPanel } from '../loadPanel'
const ExecutionHistory = loadPanel(() => import('./ExecutionHistory.vue'), '执行记录')
import TaskUnderstanding from './TaskUnderstanding.vue'
import ClarificationPanel from './ClarificationPanel.vue'
import { selectCurrentPdf,executionResultContent } from '../previewState'
import { sessionOutputFiles } from '../sessionFiles'
import { qualitySummaryText } from '../reflection'
import { resultAssessmentItems, productBoundaryView } from '../resultAssessment'

const props = defineProps({ session: Object, executionExpanded: Boolean })
const boundary = computed(() => productBoundaryView(props.session))
const assessmentItems = computed(() => resultAssessmentItems(props.session))
const emit = defineEmits(['rerun', 'refresh', 'show-evidence', 'clarify', 'show-files', 'toggle-execution'])
const activeTab = ref('answer')
watch(() => props.session?.thread_id, () => { activeTab.value = 'answer' })
const pdfUrl = ref('')
const pdfRequired = computed(() => props.session?.plan?.require_pdf ?? props.session?.require_pdf ?? false)
const scale = ref(1)
const exporting = ref(false)
const chartPalette = computed({
  get: () => store.chartPalette,
  set: (value) => setChartPalette(value),
})
const detailVisible = ref(false)
const drilldown = ref({ visible: false, loading: false, table: '', filters: [], columns: [], rows: [], nextLevel: '' })
const drilldownRowObjects = computed(() =>
  (drilldown.value.rows || []).map((row) =>
    Object.fromEntries((drilldown.value.columns || []).map((column, index) => [column, row[index]]))
  )
)

const evidence = computed(() => props.session?.evidence || [])
const charts = computed(() => {
  if(props.session?.status==='awaiting_input') return []
  if (Array.isArray(props.session?.charts)) return props.session.charts
  // 兼容旧接口；新版页面和导出均使用后端生成的完整图表清单。
  const seen = new Set()
  return evidence.value.filter(item => {
    if (!item.chart || item.chart.rowCount < 2) return false
    const key = JSON.stringify({fields: item.chart.fields, xAxis: item.chart.xAxis, series: item.chart.series, heatmap: item.chart.heatmap})
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})
const hasSessionFiles=ref(false)
watch(() => [props.session?.thread_id,props.session?.session_dir,props.session?.status,props.session?.result], async ([id,directory],previous,onCleanup) => {
  let stale=false
  onCleanup(()=>{stale=true})
  hasSessionFiles.value=false
  if(!id)return
  const [uploads,outputs]=await Promise.allSettled([listAttachments(id),directory?listFiles(directory,id,true):Promise.resolve({files:[]})])
  if(stale)return
  const attachments=uploads.status==='fulfilled'?uploads.value.files || []:[]
  const reports=outputs.status==='fulfilled'?sessionOutputFiles(outputs.value.files || [],attachments,directory).filter(file=>/\.(pdf|md|markdown|docx|html)$/i.test(file.name)):[]
  hasSessionFiles.value=attachments.length>0 || reports.length>0
},{immediate:true})
const selectedChart = ref('')
const chartScroll = ref(null)
watch(charts, (items) => {
  if (!items.some(item => item.id === selectedChart.value)) selectedChart.value = items[0]?.id || ''
}, {immediate: true})
function jumpToChart(id) {
  const target = document.getElementById(`chart-${id}`)
  if (target && chartScroll.value) {
    const container = chartScroll.value
    container.scrollTo({top: container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top, behavior: 'smooth'})
  }
}

const answerContent=computed(()=>executionResultContent(props.session))
const validationNotice=computed(()=>statisticsNotice(props.session?.query_result))
function renderContent(md){
  // Convert citations before sanitizing, never inject links into already-sanitized HTML attributes.
  return DOMPurify.sanitize(parseResultMarkdown(md.replace(/\[(S\d+)\]/g,'<a href="#evidence-$1" class="citation-ref" data-evidence="$1">[$1]</a>')),{ADD_ATTR:['data-evidence']})
}
function copyAnswer(){navigator.clipboard.writeText((boundary.value && boundary.value.status !== 'VERIFIED' ? boundary.value.candidateLabel + '\n\n' : '') + answerContent.value);toast('success','结果已复制到剪贴板')}

watch(
  () => [props.session?.thread_id, props.session?.session_dir, props.session?.status, props.session?.started_at, pdfRequired.value],
  async ([threadId, dir, status, startedAt, requirePdf], _previous, onCleanup) => {
    let cancelled = false
    let objectUrl = ''
    pdfUrl.value = ''
    onCleanup(() => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    })
    if (!dir || !requirePdf || status !== 'completed') return
    try {
      const response = await listFiles(dir, threadId, true)
      if (cancelled) return
      const pdf = selectCurrentPdf(response.files || [], startedAt)
      if (!pdf) return
      const result = await downloadFile(pdf.path, threadId)
      const blob = await result.blob()
      if (cancelled) return
      objectUrl = URL.createObjectURL(blob)
      pdfUrl.value = objectUrl + '#view=FitH'
    } catch { /* No report is preferable to displaying a prior execution's PDF. */ }
  },
  {immediate: true},
)

watch(pdfRequired, required => {
  if (!required && activeTab.value === 'pdf') activeTab.value = 'answer'
})

watch(charts, items => {
  if (!items.length && activeTab.value === 'charts') activeTab.value = 'answer'
})

async function openDrilldown(payload) {
  const session = props.session
  if (!session?.thread_id || !payload?.evidenceId) return
  detailVisible.value = true
  drilldown.value = {
    visible: true,
    loading: true,
    table: '',
    filters: payload.filters || [],
    columns: [],
    rows: [],
    nextLevel: '',
  }
  try {
    const result = await runDrilldown(session.thread_id, payload.evidenceId, payload.filters || [])
    drilldown.value = {
      visible: true,
      loading: false,
      table: result.table || '',
      filters: payload.filters || [],
      columns: result.columns || [],
      rows: result.rows || [],
      completeness: result.completeness,
      nextLevel: result.nextLevel || '',
    }
  } catch (error) {
    drilldown.value.loading = false
    toast('error', `明细加载失败：${error.message}`)
  }
}

async function exportChartReport() {
  if (!props.session?.thread_id || exporting.value) return
  exporting.value = true
  try {
    const blob = await downloadChartReport(props.session.thread_id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = '图表报告.pdf'
    a.click()
    URL.revokeObjectURL(url)
    toast('success', '图表报告已生成')
  } catch (error) {
    toast('error', `图表报告导出失败：${error.message}`)
  } finally {
    exporting.value = false
  }
}

function onMarkdownClick(event) {
  const link = event.target.closest('[data-evidence]')
  if (!link) return
  event.preventDefault()
  const evidenceId = link.dataset.evidence
  emit('show-evidence', evidenceId)
}

function zoom(v) {
  scale.value = v
}
</script>

<style scoped>
.result-preview {
  flex: 1;
  width: 100%;
  min-height: 0;
  padding: 12px 14px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.execution-details-toggle.attention {background:color-mix(in srgb,var(--danger-text) 10%,var(--card-bg));border-color:var(--danger-text);color:var(--danger-text);}
.execution-running-dot {width:6px;height:6px;border-radius:50%;background:currentColor;}
.preview-head-actions {position:absolute;right:0;top:0;z-index:2;display:flex;align-items:center;gap:8px;}
.session-files-button {display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:36px;padding:7px 12px;border:1px solid color-mix(in srgb,var(--primary) 24%,var(--border));border-radius:6px;background:color-mix(in srgb,var(--primary) 8%,var(--card-bg));color:var(--primary);font:inherit;font-size:13px;font-weight:600;line-height:1.4;cursor:pointer;white-space:nowrap;}
.session-files-button:hover {background:color-mix(in srgb,var(--primary) 14%,var(--card-bg));}
.session-files-button:focus-visible {outline:2px solid var(--primary);outline-offset:2px;}
.tabs-shell {position:relative;flex:1;min-height:0;display:flex;flex-direction:column;container-type:inline-size;}
.tabs :deep(.el-tabs__header) {padding-right:150px;border-bottom:1px solid var(--border);}
.has-session-files .tabs :deep(.el-tabs__header) {padding-right:296px;}
.has-session-files.execution-expanded .tabs :deep(.el-tabs__header) {padding-right:268px;}
.tabs :deep(.el-tabs__item) {padding-bottom:4px;}
.tabs :deep(.el-tabs__nav-wrap)::after {display:none;}
@container (max-width:500px) {
  .preview-head-actions {position:static;align-self:flex-end;margin-bottom:8px;flex-wrap:wrap;}
  .tabs :deep(.el-tabs__header),.has-session-files .tabs :deep(.el-tabs__header),.has-session-files.execution-expanded .tabs :deep(.el-tabs__header) {padding-right:0;}
}
.degraded-alert {
  margin-bottom: 6px;
}
.tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
/* 仅隐藏结果预览的滚动条，保留滚轮、触控板与键盘滚动。 */
.tabs :deep(*) { scrollbar-width: none; }
.tabs :deep(*::-webkit-scrollbar) { display: none; width: 0; height: 0; }
.tabs :deep(.el-scrollbar__bar) { display: none; }
.answer-content:focus-visible { outline:2px solid var(--primary); outline-offset:-2px; }
.tabs :deep(.el-tabs__header) { flex: 0 0 auto; }
.tabs :deep(.el-tabs__content) { flex: 1; min-height: 0; overflow: hidden; }
.tabs :deep(.el-tab-pane) { height: 100%; min-height: 0; overflow: hidden; }
.charts-pane { display: flex; flex-direction: column; gap: 8px; container-type:inline-size;container-name:chart-pane; }
.chart-picker { width:100%;min-width:0; }
.text-body { height: 100%;
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.8;
  max-height: 100%;
  overflow: auto;
  background: var(--bg);
  border-radius: 8px;
  padding: 12px;
}
.md-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  font-size: 13px;
  line-height: 1.7;
  padding: 8px 0;
}
.md-body :deep(h1),.md-body :deep(h2){font-size:18px;line-height:1.45;margin:22px 0 12px;font-weight:600;text-wrap:balance}
.md-body :deep(h3){font-size:16px;line-height:1.5;margin:18px 0 10px;font-weight:600}
.md-body :deep(> :first-child){margin-top:0}
.chart-toolbar{flex:0 0 auto;display:flex;flex-direction:column;align-items:stretch;gap:8px;padding:2px 0 8px;font-size:13px;color:var(--text-secondary)}
.chart-overview{display:flex;align-items:center;gap:12px;flex-wrap:wrap;line-height:1.6}.chart-overview strong{font-weight:500;color:var(--text)}.chart-overview>span{font-size:12px}
.chart-tools{display:grid;grid-template-columns:minmax(0,1fr) 154px max-content;gap:8px;align-items:center;width:100%;min-width:400px}
.chart-tools>.el-button{margin:0;white-space:nowrap}.palette-select{width:100%;min-width:0}
@container chart-pane (max-width:400px){.chart-toolbar{overflow-x:auto}.chart-overview{gap:4px 10px}}
.palette-label,.palette-option{display:flex;align-items:center;justify-content:flex-start;gap:8px;width:100%}
.palette-option{min-width:184px;font-size:14px;color:var(--text)}
.palette-swatches{display:inline-flex;align-items:center;gap:3px;flex-shrink:0}
.palette-swatches i{display:block;width:12px;height:12px;border-radius:3px;box-shadow:inset 0 0 0 1px #0000000a}
.palette-option .palette-swatches i{width:16px;height:16px}

.drilldown-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  padding: 0 0 10px;
  font-size: 12px;
  color: var(--text-secondary);
}
.drilldown-drawer :deep(.el-drawer__body) {
  padding-top: 8px;
}
.chart-grid {
  display: grid;
  flex: 1;
  min-height: 0;
  grid-template-columns: minmax(0, 1fr);
  grid-auto-rows: max-content;
  align-content: start;
  gap: 12px;
  overflow: auto;
  overscroll-behavior: contain;
  padding-bottom: 12px;
}
.citation-ref {
  color: var(--primary);
  font-size: 13px;
  text-decoration: none;
  padding: 0 1px;
}
.citation-ref:hover {
  text-decoration: underline;
}
.pdf-toolbar {
  display: flex;
  gap: 4px;
  padding-bottom: 4px;
}
.pdf-frame {
  width: 100%;
  height: calc(100% - 30px);
  border: none;
  border-radius: 8px;
  background: #fff;
  transform-origin: top left;
}
</style>

<style scoped>
.answer-wrap{height:100%;min-height:0;display:flex;flex-direction:column}.answer-body{font-size:15px;line-height:1.85}.answer-empty{padding:20px 0;color:var(--text-secondary);font-size:14px;line-height:1.7}
.answer-content{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain}
.answer-content .answer-body{overflow:visible}
.answer-actions{display:flex;align-items:center;justify-content:flex-start;flex-wrap:wrap;gap:12px;min-height:30px;padding:0 0 10px;flex-shrink:0;}
.answer-action-divider{height:13px;width:1px;background:var(--border);flex-shrink:0}
.all-evidence{display:inline-flex;align-items:center;gap:4px;border:0;background:transparent;padding:4px 0;font:inherit;font-size:13px;font-weight:400;line-height:1.5;color:var(--primary);cursor:pointer;white-space:nowrap}
.copy-answer :deep(.el-icon){margin-right:4px}
.all-evidence:hover{text-decoration:underline;text-underline-offset:3px}
.all-evidence:focus-visible{outline:2px solid var(--primary);outline-offset:3px;border-radius:2px}
</style>

<style scoped>
.boundary-table-scroll { overflow-x: auto; }
.boundary-table { border-collapse: collapse; width: 100%; margin: 12px 0; }
.boundary-table td, .boundary-table th { border: 1px solid var(--border); padding: 8px; text-align: left; vertical-align: top; }
</style>
