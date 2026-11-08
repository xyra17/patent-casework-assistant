<template>
  <div class="app" :style="paneStyle" :class="{ 'has-log': hasLog && !logCollapsed, 'is-resizing': !!resizing, 'knowledge-mode': ['casework', 'semantic', 'knowledge', 'rules'].includes(sidebarTab), 'business-mode': ['semantic','casework'].includes(sidebarTab), 'database-mode': sidebarTab === 'semantic', 'file-library-mode': sidebarTab === 'knowledge', 'module-navigation-mode': ['casework', 'knowledge', 'rules'].includes(sidebarTab) }">
    <!-- 左侧：会话工作区与共享数据管理 -->
    <aside class="sidebar glass-card">
      <div class="brand">
        <div class="brand-heading">
          <div class="brand-mark">
            <el-icon :size="20"><MagicStick /></el-icon>
          </div>
          <span class="logo-text">Patent Casework Assistant</span>
        </div>
        <div class="brand-controls">
          <el-popover placement="bottom-start" :width="230" trigger="click">
            <template #reference>
              <button class="session-summary" type="button">{{ totalSessions }} 个会话 · 查看状态<el-icon aria-hidden="true"><ArrowDown /></el-icon></button>
            </template>
            <div class="status-details" aria-label="会话状态统计">
              <strong>会话状态</strong>
              <span>运行中 <b>{{ stats.running }}</b></span>
              <span>已完成 <b>{{ stats.completed }}</b></span>
              <span v-if="stats.awaiting_input">待你确认 <b>{{ stats.awaiting_input }}</b></span>
              <span>失败 <b>{{ stats.failed }}</b></span>
              <span title="执行已结束，但仍有未通过的校验项">未达标 <b>{{ stats.degraded }}</b></span>
              <span v-if="stats.preparing">准备中 <b>{{ stats.preparing }}</b></span>
              <span v-if="stats.cancelled">已取消 <b>{{ stats.cancelled }}</b></span>
            </div>
          </el-popover>
          <el-tooltip :content="store.theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'" placement="bottom">
            <button class="theme-btn" type="button" @click="toggleTheme">
              <el-icon><Sunny v-if="store.theme === 'dark'" /><Moon v-else /></el-icon>
              <span>{{ store.theme === 'dark' ? '浅色' : '深色' }}</span>
            </button>
          </el-tooltip>
        </div>
      </div>

      <nav class="workspace-nav" aria-label="工作区导航">
        <button type="button" class="nav-item" :class="{active: sidebarTab === 'sessions'}"
          :aria-pressed="sidebarTab === 'sessions'" @click="sidebarTab = 'sessions'">会话</button>
        <button type="button" class="nav-item" :class="{active: trashVisible}" aria-haspopup="dialog"
          :aria-expanded="trashVisible" @click="trashVisible = true">回收站</button>
        <button type="button" class="nav-item" :class="{active: sidebarTab === 'casework'}" :aria-pressed="sidebarTab==='casework'" @click="sidebarTab='casework'">案件</button>
        <el-dropdown trigger="click" @command="sidebarTab = $event">
          <button type="button" class="nav-item data-menu" :class="{active: DATA_NAV.some(item => item.id === sidebarTab)}"
            aria-label="数据管理菜单">数据管理<el-icon aria-hidden="true"><ArrowDown /></el-icon></button>
          <template #dropdown>
            <el-dropdown-menu aria-label="数据管理">
              <el-dropdown-item v-for="item in DATA_NAV" :key="item.id" :command="item.id"
                :class="{selected: sidebarTab === item.id}">{{ item.label }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </nav>

      <div class="sidebar-content">
        <section v-show="sidebarTab === 'sessions'" class="sidebar-pane" aria-label="会话列表">
          <div class="sessions-pane">
            <el-scrollbar class="session-scroll">
              <p v-if="historyError" class="history-error" role="alert">{{ historyError }} <el-button link type="primary" :loading="historyLoading" @click="loadHistory(false)">重试</el-button></p>
              <div v-if="!store.sessions.length && !historyError" class="session-empty">
                <div class="empty-icon"><el-icon :size="22"><ChatDotRound /></el-icon></div>
                <p>暂无会话</p>
                <span>提交任务后会出现在这里</span>
              </div>
              <transition-group v-else name="session" tag="div" class="session-list" appear>
                <div
                  v-for="s in store.sessions"
                  :key="s.thread_id"
                  :data-thread-id="s.thread_id"
                  class="session-item"
                  :class="{ active: s.thread_id === store.currentThreadId }"
                  @click="onSwitch(s.thread_id)"
                >
                  <div class="session-head">
                    <span class="status-dot" :class="s.status"></span>
                    <el-tag :type="statusTagType(s.status, s)" size="small" effect="plain">{{ statusText(s.status, s) }}</el-tag>
                    <span class="session-time">{{ s.created_at.slice(5, 16) }}</span>
                  </div>
                  <div class="session-query">{{ s.query }}</div>
                  <div class="session-actions">
                    <el-button link size="small" @click.stop="rerun(s.thread_id)">
                      <el-icon><RefreshRight /></el-icon>重跑
                    </el-button>
                    <el-button link size="small" type="danger" @click.stop="onDelete(s.thread_id)">
                      <el-icon><Delete /></el-icon>删除
                    </el-button>
                  </div>
                </div>
              </transition-group>
              <el-button v-if="historyNext !== null" class="history-more" text :loading="historyLoading" @click="loadHistory(true)">加载更早会话</el-button>
            </el-scrollbar>
          </div>
        </section>

        <section v-show="sidebarTab === 'casework'" id="casework-navigation" class="sidebar-pane" aria-label="案件工作台导航"></section>
        <section v-show="sidebarTab === 'knowledge'" id="knowledge-navigation" class="sidebar-pane" aria-label="知识库导航"></section>

        <section v-show="sidebarTab === 'rules'" id="rules-navigation" class="sidebar-pane" aria-label="长期规则导航"></section>

        <section v-show="sidebarTab === 'semantic'" class="sidebar-pane" aria-label="业务数据库导航">
          <BusinessDatabaseNav v-model:record-table="businessRecord" v-model:active-tab="businessTab" v-model:settings-visible="businessSettingsVisible" />
        </section>
      </div>
    </aside>

    <!-- 中央主区域：结果预览铺满 -->
    <main class="main">
      <CaseworkPanel v-if="sidebarTab === 'casework'" sidebar-target="#casework-navigation" @ask="openCaseTask" />
      <BusinessDatabasePanel v-else-if="sidebarTab === 'semantic'" v-model:record-table="businessRecord" v-model:active-tab="businessTab" v-model:settings-visible="businessSettingsVisible" />
      <KnowledgePanel v-else-if="sidebarTab === 'knowledge'" sidebar-target="#knowledge-navigation" />
      <LongTermRulesPanel v-else-if="sidebarTab === 'rules'" sidebar-target="#rules-navigation" />
      <WelcomePanel
        v-else-if="!session"
        @pick="fillTemplate"
      />
      <ResultPreview v-else :session="session" :execution-expanded="!logCollapsed" @toggle-execution="toggleExecutionDetails" @rerun="rerun" @refresh="loadSessionDetail" @show-evidence="openEvidence" @clarify="answerClarification" @show-files="filesVisible=true" />
    </main>

    <div class="pane-resizer left-resizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="调整左侧会话栏宽度"
      :aria-valuemin="leftBounds.min" :aria-valuemax="leftBounds.max" :aria-valuenow="layout.left"
      title="左右拖动调整宽度，双击恢复默认；方向键微调"
      @pointerdown="startResize($event, 'left')" @pointermove="moveResize" @pointerup="finishResize" @pointercancel="finishResize" @lostpointercapture="finishResize"
      @dblclick="resetPane('left')" @keydown="resizeWithKey($event, 'left')"></div>
    <div v-if="hasLog && !logCollapsed" class="pane-resizer right-resizer" role="separator" tabindex="0" aria-orientation="vertical" aria-label="调整右侧执行与来源栏宽度"
      :aria-valuemin="rightBounds.min" :aria-valuemax="rightBounds.max" :aria-valuenow="layout.right"
      title="左右拖动调整宽度，双击恢复默认；方向键微调"
      @pointerdown="startResize($event, 'right')" @pointermove="moveResize" @pointerup="finishResize" @pointercancel="finishResize" @lostpointercapture="finishResize"
      @dblclick="resetPane('right')" @keydown="resizeWithKey($event, 'right')"></div>

    <!-- 任务结束后让出结果宽度，从结果区标题栏按需打开详情。 -->
    <aside v-if="hasLog" v-show="!logCollapsed" id="execution-details" class="log-side" aria-label="执行详情" tabindex="-1">
      <EventTree
        v-if="!logCollapsed"
        ref="eventPanel"
        :session="session"
        @rerun="rerun"
        @terminate="terminate"
      />
    </aside>

    <!-- 底部任务输入通栏 -->
    <footer v-show="!['casework', 'semantic', 'knowledge', 'rules'].includes(sidebarTab)" class="taskbar">
      <TaskInput :submit-task="startTask" />
    </footer>

    <RecycleBin v-if="trashVisible" v-model="trashVisible" @restored="onRestored" />
    <el-drawer v-model="filesVisible" title="会话附件与报告" size="min(600px, 100vw)">
      <FilePanel :session="session" embedded @rerun="rerun" @compare="compareSessionAttachments" />
    </el-drawer>

    <!-- Toast -->
    <div class="toasts">
      <transition-group name="toast">
        <div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.type">
          <el-icon><component :is="toastIcon(t.type)" /></el-icon>
          <span>{{ t.message }}</span>
        </div>
      </transition-group>
    </div>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import {readWorkspace,saveWorkspace} from './businessWorkspace'
import { BUSINESS_RECORDS } from './businessRecords'
import BusinessDatabaseNav from './components/BusinessDatabaseNav.vue'
import { usePaneResize } from './usePaneResize'
import { useExecutionPanel } from './useExecutionPanel'
import TaskInput from './components/TaskInput.vue'
import { loadPanel } from './loadPanel'
const EventTree = loadPanel(() => import('./components/EventTree.vue'), '执行详情')
import FilePanel from './components/FilePanel.vue'
const CaseworkPanel = defineAsyncComponent(() => import('./components/CaseworkPanel.vue'))
const BusinessDatabasePanel = defineAsyncComponent(() => import('./components/BusinessDatabasePanel.vue'))
const KnowledgePanel = defineAsyncComponent(() => import('./components/KnowledgePanel.vue'))
const LongTermRulesPanel = defineAsyncComponent(() => import('./components/LongTermRulesPanel.vue'))
const RecycleBin = defineAsyncComponent(() => import('./components/RecycleBin.vue'))
const trashVisible = ref(false)
const filesVisible = ref(false)
const ResultPreview = defineAsyncComponent(() => import('./components/ResultPreview.vue'))
import { createSessionConnections } from './sessionConnection'
import WelcomePanel from './components/WelcomePanel.vue'
import { submitTask, connectWS, waitForWS, cancelTask, listRuns, getRun, deleteRun, uploadFiles } from './api'
import { prepareTaskAttachments } from './sessionFiles'
import {
  store,
  addSession,
  getSession,
  switchSession,
  deleteSession,
  toggleTheme,
  sessionStats,
  sessionTotal,
  setHistorySummary,
  currentSession,
  toast,
} from './store'

const stats = computed(() => sessionStats())
const totalSessions = computed(() => sessionTotal())
const historyNext = ref(null)
const historyLoading = ref(false)
const historyError = ref('')
let summaryTimer
function refreshSummary() {
  clearTimeout(summaryTimer)
  summaryTimer = setTimeout(async () => {
    try { setHistorySummary(await listRuns(50, 0, store.sessions.map(s => s.thread_id))) } catch { /* Keep the last known total until retry. */ }
  }, 150)
}
const session = computed(() => currentSession())
const sidebarTab = ref('sessions')
const storedBusinessRecord = readWorkspace('recordTable')
const businessRecord = ref([...BUSINESS_RECORDS.map(item => item.table), 'other'].includes(storedBusinessRecord) ? storedBusinessRecord : 'cw_cases')
const storedBusinessTab = readWorkspace('tab')
const businessTab = ref(['browse', 'import', 'audit'].includes(storedBusinessTab) ? storedBusinessTab : 'browse')
const businessSettingsVisible = ref(false)
watch(businessRecord, value => saveWorkspace('recordTable', value))
watch(businessTab, value => saveWorkspace('tab', value))
watch(sidebarTab, () => { businessSettingsVisible.value = false })
watch(sidebarTab,value=>saveWorkspace('businessOpen',value==='semantic'))
const DATA_NAV = [
  {id: 'semantic', label: '业务数据库'},
  {id: 'knowledge', label: '知识库'},
  {id: 'rules', label: '长期规则'},
]
const { collapsed: logCollapsed } = useExecutionPanel(session)
watch(logCollapsed, collapsed => {
  if (collapsed && document.getElementById('execution-details')?.contains(document.activeElement)) {
    nextTick(() => document.getElementById('execution-details-toggle')?.focus())
  }
})
function toggleExecutionDetails() {
  logCollapsed.value = !logCollapsed.value
  nextTick(() => document.getElementById(logCollapsed.value ? 'execution-details-toggle' : 'execution-details')?.focus())
}
const hasLog = computed(() => !!session.value && !['casework', 'semantic', 'knowledge', 'rules'].includes(sidebarTab.value))
const { layout, paneStyle, resizing, leftBounds, rightBounds, startResize, moveResize, finishResize, resetPane, resizeWithKey } = usePaneResize(hasLog, logCollapsed)

const detailRequests = new WeakMap()
const connections = createSessionConnections({
  connectWS, getRun, onEvent,
  applyRun: (target, run) => Object.assign(target, runToSession(run, true)),
  isPresent: target => getSession(target.thread_id) === target,
})

function onEvent(session, ev) {
  session.events.push(ev)
  if (ev.event === 'session_created') {
    session.preparing = false
    session.ruleReviewPending = false
    session.status = 'running'
    session.session_dir = ev.data?.path || ''
  } else if (ev.event === 'rules_context') {
    session.rule_context = ev.data || {}
  } else if (['plan_created', 'plan_updated'].includes(ev.event)) {
    session.plan = ev.data || null
    if (ev.data?.max_rounds) session.max_rounds = ev.data.max_rounds
  } else if (ev.event === 'reflection') {
    session.hard_rounds.push(ev.data)
  } else if (ev.event === 'quality_summary') {
    session.quality_summary = ev.data || null
  } else if (ev.event === 'task_clarification') {
    session.status = 'awaiting_input'
    session.clarification = ev.data.clarification
    session.stop_reason = 'awaiting_input'
    session.result = ev.data.result || ''
    session.evidence = ev.data.evidence || []
    session.charts = []
    session.query_result = null
    session.loaded = true
    connections.close(session.thread_id)
    toast('warning','关键依据尚未确认，请在结果区选择下一步')
  } else if (ev.event === 'task_result') {
    session.status = 'completed'
    session.degraded = Boolean(ev.data?.degraded)
    session.result = ev.data?.result || ''
    session.result_assessment = ev.data?.result_assessment || null
    session.query_result = ev.data?.query_result || null
    session.evidence = ev.data?.evidence || session.evidence || []
    session.charts = ev.data?.charts || []
    session.loaded = true
    connections.close(session.thread_id)
    toast(session.degraded ? 'warning' : 'success', session.degraded ? '已应用兜底：返回最优结果与证据摘要' : '任务完成')
  } else if (ev.event === 'task_cancelled') {
    session.status = 'cancelled'
    session.stop_reason = 'user_cancel'
    connections.close(session.thread_id)
    toast('info', '任务已终止')
  } else if (ev.event === 'error') {
    session.status = 'failed'
    session.error = ev.message || '任务执行失败'
    session.stop_reason = ev.data?.stop_reason || 'execution_error'
    connections.close(session.thread_id)
    toast('error', ev.message || '任务执行失败')
  }
  if (['task_result','task_clarification','task_cancelled','error'].includes(ev.event)) refreshSummary()
}

async function executeSession(session, clarificationAnswer = null) {
  const threadId = session.thread_id
  const expectedRevisionId = session.intent_revisions?.[0]?.id ?? null
  detailRequests.delete(session)
  session.preparing = true
  session.ruleReviewPending = false
  try {
    const ws = connections.open(session)
    await waitForWS(ws)
    const skipped = []
    let started
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        started = await submitTask(session.query, threadId, session.task_query || session.query, session.require_pdf, session.max_rounds || 3, expectedRevisionId, skipped, session.round_mode || 'manual', session.allowed_sources || ['db_query','knowledge'], clarificationAnswer)
        break
      } catch (error) {
        if (error.detail?.code !== 'rule_conflict') throw error
        const conflicts = error.detail.rule_context.conflicts || []
        session.ruleReviewPending = true
        try {
          await ElMessageBox.confirm(conflicts.map(r => `• ${r.title}（v${r.revision}）：${r.reason}`).join('\n') + '\n\n当前任务尚未执行。本次跳过上述规则并继续，或取消后到长期规则中修改。',
            '核对长期规则', {confirmButtonText:'本次跳过冲突规则并继续',cancelButtonText:'取消执行',type:'warning',customClass:'rule-conflict-dialog'})
        } catch { const cancelled = new Error('已取消执行，长期规则未修改'); cancelled.cancelledBeforeRun = true; throw cancelled }
        finally { session.ruleReviewPending = false }
        for (const r of conflicts) if (!skipped.some(s => s.id === r.id && s.revision === r.revision)) skipped.push({id:r.id,revision:r.revision})
      }
    }
    if (!started) throw new Error('规则连续发生变化，请核对后重新执行')
    session.rule_context = started.rule_context || {}
    session.allowed_sources = started.allowed_sources || session.allowed_sources
    session.applied_intent_id = started.applied_intent_id
    session.started_at = started.started_at
    session.execution_id = started.execution_id
    session.execution_query = started.execution_query || session.task_query || session.query
    refreshSummary()
    toast('info', '会话已创建，开始执行')
    return true
  } catch (error) {
    session.status = error.cancelledBeforeRun ? 'cancelled' : 'failed'
    connections.close(threadId)
    toast(error.cancelledBeforeRun ? 'info' : 'error', error.message)
    // A lost POST response may still have started a task; reconcile before restoring old UI.
    try {
      const run = await getRun(threadId)
      Object.assign(session, runToSession(run, true))
      if (session.status === 'running') attachWS(session)
      return true
    } catch { return false }
  } finally {
    session.preparing = false
    session.ruleReviewPending = false
  }
}

function formatSessionTime(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return String(value).slice(5, 16).replace('T', ' ')
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const eventPanel=ref(null)
const pendingEvidence=ref(null)
watch([eventPanel,pendingEvidence],([panel,pending])=>{
  if (!panel || !pending) return
  pendingEvidence.value=null
  if (pending.threadId===session.value?.thread_id) panel.showEvidence(pending.id)
},{flush:'post'})
function openEvidence(id){
  pendingEvidence.value={threadId:session.value?.thread_id,id}
  logCollapsed.value=false
}

function runToSession(run, withEvents = false) {
  const events = withEvents ? (run.events || []) : []
  const planEvent = events.find((e) => e.event === 'plan_created')
  const reflectionEvents = events.filter((e) => e.event === 'reflection')
  const sessionEvent = events.find((e) => e.event === 'session_created')
  return {
    thread_id: run.thread_id,
    execution_id: run.execution_id,
    query: run.query,
    task_query: run.task_query || run.query,
    execution_query: run.execution_query || run.task_query || run.query,
    require_pdf: Boolean(run.require_pdf),
    max_rounds: run.max_rounds || 3,
    round_mode: run.round_mode || 'manual',
    allowed_sources: run.allowed_sources ?? null,
    clarification: run.clarification || null,
    clarification_response: run.clarification_response || null,
    intent_revisions: run.intent_revisions || [],
    applied_intent_id: run.applied_intent_id ?? null,
    rule_context: run.rule_context || {},
    preparing: false,
    ruleReviewPending: false,
    quality_summary: [...events].reverse().find(e => e.event === 'quality_summary')?.data || null,
    status: run.status || 'failed',
    degraded: Boolean(run.degraded),
    error: run.error || '',
    stop_reason: run.stop_reason || '',
    created_at: formatSessionTime(run.created_at),
    started_at: run.created_at,
    events,
    session_dir: run.session_dir ?? sessionEvent?.data?.path ?? '',
    result: run.result || '',
    result_assessment: run.result_assessment || null,
    query_result: run.query_result || null,
    plan: run.plan || planEvent?.data || null,
    hard_rounds: reflectionEvents.map((e) => e.data),
    evidence: run.evidence || [],
    charts: run.charts,
    loaded: withEvents,
  }
}

function attachWS(session) {
  if (session?.status === 'running' && !connections.has(session.thread_id)) connections.open(session, true)
}

async function loadSessionDetail(threadId) {
  const session = getSession(threadId)
  if (!session) return
  const request = {}
  detailRequests.set(session, request)
  connections.close(threadId)
  try {
    const run = await getRun(threadId)
    if (detailRequests.get(session) !== request || getSession(threadId) !== session) return
    Object.assign(session, runToSession(run, true))
  } catch (error) {
    if (detailRequests.get(session) === request) toast('error', `历史记录加载失败：${error.message}`)
  } finally {
    if (detailRequests.get(session) === request && getSession(threadId) === session && session.status === 'running') attachWS(session)
  }
}

async function loadHistory(more = false) {
  if (historyLoading.value) return
  historyLoading.value = true
  historyError.value = ''
  try {
    const data = await listRuns(50, 0, store.sessions.map(s => s.thread_id), more ? historyNext.value : null)
    const incoming = (data.runs || []).map(run => runToSession(run, false))
    // Never replace a locally running task or a loaded detail with a list summary.
    for (const item of incoming) if (!getSession(item.thread_id)) store.sessions.push(item)
    historyNext.value = data.next_cursor ?? null
    setHistorySummary(data)
    // Restore history without selecting a task; the welcome page is the entry point.
    // Running tasks still reconnect so their status stays current in the list.
    for (const target of store.sessions) attachWS(target)
  } catch (error) {
    historyError.value = `会话列表加载失败：${error.message}`
  } finally { historyLoading.value = false }
}

onMounted(() => loadHistory(false))

const MODE_HINTS = {
  quick: '\n【任务类型：快速问答】只做必要检索，直接回答问题；不要生成任何文件，不要展开为行业研究。',
  analysis: '\n【任务类型：数据分析】基于已获取的数据做分析并给出结论；不要生成文件。',
  report: '\n【任务类型：报告】请生成结构化的分析报告。',
}

async function startTask(query, requirePdf, mode = 'auto', maxRounds = 'auto', allowedSources = ['db_query','knowledge'], files = [], attachmentContext = '') {
  sidebarTab.value = 'sessions'
  let taskQuery = query + attachmentContext + (MODE_HINTS[mode] || '')
  if (requirePdf) {
    taskQuery += '（请生成 PDF 报告）'
  }
  const forcePdf = requirePdf || /(?:生成|导出|输出|保存为|提供|制作|转换为)[^。；\n]{0,20}pdf/i.test(query)
  const threadId = crypto.randomUUID().replace(/-/g, '')
  try {await prepareTaskAttachments(threadId, files, uploadFiles)}
  catch(error) {toast('error',`附件上传失败，任务未开始：${error.message}`);return false}
  const session = addSession(threadId, query, forcePdf)
  session.round_mode = maxRounds === 'auto' ? 'auto' : 'manual'
  session.max_rounds = maxRounds === 'auto' ? 3 : maxRounds
  session.task_query = taskQuery
  session.allowed_sources = [...allowedSources]
  return await executeSession(session)
}

async function rerun(threadId, allowedSources, clarificationAnswer = null, nextQuery = null) {
  const s = getSession(threadId)
  if (!s) return
  if (s.status === 'running') {
    toast('warning', '请先终止当前任务再重跑')
    return
  }
  if (!s.loaded) { await loadSessionDetail(threadId); if (!s.loaded) return }
  const previous = { ...s }
  if (nextQuery) {
    s.query=nextQuery
    s.task_query=nextQuery+MODE_HINTS.analysis
    s.require_pdf=false
  }
  if(Array.isArray(allowedSources))s.allowed_sources=[...allowedSources]
  connections.close(threadId)
  sidebarTab.value = 'sessions'
  s.events = []
  s.status = 'running'
  s.result = ''
  s.result_assessment = null
  s.query_result = null
  s.session_dir = ''
  s.degraded = false
  s.error = ''
  s.stop_reason = ''
  s.plan = null
  s.hard_rounds = []
  s.quality_summary = null
  s.evidence = []
  s.charts = []
  s.started_at = null
  s.rule_context = {}
  s.clarification = null
  s.clarification_response = null
  if (!await executeSession(s, clarificationAnswer)) Object.assign(s, previous)
}

async function compareSessionAttachments({threadId,query}) {
  await rerun(threadId,[],null,query)
}

async function answerClarification(answer) {
  const target=session.value
  if (!target || target.status!=='awaiting_input') return
  await rerun(target.thread_id,target.allowed_sources,answer)
}

async function terminate(threadId) {
  const s = getSession(threadId)
  if (!s) return
  try {
    await cancelTask(threadId)
    await loadSessionDetail(threadId)
    refreshSummary()
  } catch (error) {
    toast('error', error.message)
  }
}

async function onSwitch(threadId) {
  filesVisible.value = false
  switchSession(threadId)
  sidebarTab.value = 'sessions'
  const session = getSession(threadId)
  if (session && !session.loaded) await loadSessionDetail(threadId)
  if (session?.status === 'running') attachWS(session)
}

async function onDelete(threadId) {
  if (getSession(threadId)?.status === 'running') {
    toast('warning', '请先终止任务再删除会话')
    return
  }
  try {
    await ElMessageBox.confirm(
      '会话将移入回收站并保留30天，期间可恢复记录、附件和报告。到期后自动清理。业务数据库、知识库和已另存的长期规则不受影响。',
      '将会话移入回收站？',
      { type: 'warning', confirmButtonText: '移入回收站', cancelButtonText: '取消' }
    )
  } catch { return }
  try {
    await deleteRun(threadId)
    connections.close(threadId)
    deleteSession(threadId)
    refreshSummary()
    const next = getSession(store.currentThreadId)
    if (next && !next.loaded) await loadSessionDetail(next.thread_id)
    toast('success', '已移入回收站，30天内可以恢复')
  } catch (error) {
    toast('error', error.message)
  }
}

async function onRestored(threadId) {
  try {
    const run = await getRun(threadId)
    const existing = getSession(threadId)
    if (existing) Object.assign(existing, runToSession(run, true))
    else store.sessions.unshift(runToSession(run, true))
    trashVisible.value = false
    await onSwitch(threadId)
    refreshSummary()
  } catch (error) {toast('error', `会话已恢复，但加载失败：${error.message}`)}
}

function openCaseTask(value) {
  sidebarTab.value = 'sessions'
  fillTemplate(value)
}

function fillTemplate(value) {
  store.pendingTemplate = typeof value === 'object' ? value : null
  store.pendingSources = typeof value === 'object' ? value.allowed_sources ?? null : null
  store.pendingQuery = typeof value === 'object' ? value.value : value
  focusInput()
}

function focusInput() {
  setTimeout(() => {
    document.querySelector('.task-input textarea')?.focus()
  }, 60)
}

function statusText(status, session) {
  if (session?.preparing) return session.ruleReviewPending ? '待确认' : '核对规则'
  if (status === 'completed' && session?.degraded) return '未达标'
  return { running: '运行中', completed: '已完成', failed: '失败', cancelled:'已取消', awaiting_input:'待你确认' }[status] || status
}
function statusTagType(status, session) {
  if (status === 'completed' && session?.degraded) return 'warning'
  return { running: 'primary', completed: 'success', failed: 'danger',awaiting_input:'warning' }[status] || 'info'
}
function toastIcon(type) {
  return { success: 'CircleCheck', error: 'CircleClose', warning: 'Warning', info: 'InfoFilled' }[type] || 'InfoFilled'
}
</script>

<style scoped>
.app.knowledge-mode { grid-template-rows: minmax(0, 1fr); grid-template-areas: 'sidebar main'; }
.knowledge-nav-help { padding: 14px 2px; font-size: 12px; line-height: 1.8; }
.knowledge-nav-help p { color: var(--text-secondary); margin-top: 12px; }

.app {
  display: grid;
  grid-template-columns: var(--sidebar-size, 320px) minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) auto;
  grid-template-areas:
    'sidebar main'
    'taskbar taskbar';
  height: 100vh;
  gap: 14px;
  --layout-gap: 14px;
  padding: 14px;
  background:
    radial-gradient(circle at 0% 0%, rgba(22, 93, 255, 0.07), transparent 28%),
    radial-gradient(circle at 100% 100%, rgba(146, 84, 222, 0.07), transparent 30%),
    var(--bg);
}
.app.has-log {
  grid-template-columns: var(--sidebar-size, 320px) minmax(0, 1fr) var(--log-size, 380px);
  grid-template-areas:
    'sidebar main log'
    'taskbar taskbar taskbar';
}
.pane-resizer { width: var(--layout-gap); align-self: stretch; z-index: 10; cursor: col-resize; touch-action: none; position: relative; outline: none; }
.left-resizer { grid-area: sidebar; justify-self: end; transform: translateX(100%); }
.right-resizer { grid-area: log; justify-self: start; transform: translateX(-100%); }
.pane-resizer::after { content: ''; position: absolute; left: 50%; top: 50%; width: 2px; height: 48px; border-radius: 2px; background: var(--primary); opacity: 0; transform: translate(-50%, -50%); transition: opacity .15s, height .15s; }
.pane-resizer:hover::after, .pane-resizer:focus-visible::after, .is-resizing .pane-resizer::after { opacity: .65; }
.pane-resizer:focus-visible::after { width: 3px; height: 64px; }
.is-resizing, .is-resizing :deep(*) { cursor: col-resize !important; user-select: none !important; }
.sidebar {
  grid-area: sidebar;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
  min-height: 0;
}
.main {
  grid-area: main;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
  min-width: 0;
  min-height: 0;
}
.taskbar {
  grid-area: taskbar;
  position: relative;
  z-index: 20;
}

/* 高频入口一行常驻，低频管理入口按需展开。 */
.workspace-nav { display: grid; grid-template-columns: .8fr 1fr .8fr 1.2fr; gap: 3px; padding: 3px; border-radius: 9px; border: 1px solid var(--border); background: var(--bg); flex-shrink: 0; }
.workspace-nav > .el-dropdown { min-width: 0; display: flex; }
.nav-item { display: flex; align-items: center; justify-content: center; gap: 4px; width: 100%; min-width: 0; min-height: 34px; padding: 6px 3px; border: 0; border-radius: 6px; background: transparent; color: var(--text-secondary); font: inherit; font-size: 13px; cursor: pointer; white-space: nowrap; }
.nav-item .el-icon { font-size: 12px; flex-shrink: 0; }
.nav-item:hover { background: var(--primary-soft); color: var(--text); }
.nav-item.active { background: var(--primary-soft); color: var(--primary); font-weight: 600; }
.nav-item:focus-visible, .session-summary:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.session-summary { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: var(--text-secondary); font: inherit; font-size: 14px; line-height: 1.6; text-align: left; padding: 3px 0; cursor: pointer; white-space: nowrap; }
.session-summary:hover { color: var(--primary); }
.status-details { display: flex; flex-direction: column; gap: 12px; font-size: 13px; }
.status-details span { display: flex; justify-content: space-between; }
.status-details b { font-variant-numeric: tabular-nums; }
:deep(.el-dropdown-menu__item.selected) { color: var(--primary); font-weight: 600; }
.sidebar-content { flex: 1; min-height: 0; overflow: hidden; border-top: 1px solid var(--border); padding-top: 10px; }
.sidebar-pane { height: 100%; min-height: 0; overflow: auto; }
.sessions-pane {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}

/* 中央：执行过程可折叠，结果占主要空间 */
.log-side {
  grid-area: log;
  min-height: 0;
  min-width: 0;
  display: flex;
  overflow: hidden;
}
.log-side > .event-tree {
  flex: 1;
  min-width: 0;
  min-height: 0;
  width: 100%;
}

.brand {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 2px;
}
.brand-heading {
  display: flex;
  align-items: center;
  gap: 10px;
}
.brand-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.brand-mark {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  color: #fff;
  background: linear-gradient(135deg, #165dff, #6a5cff 55%, #9254de);
  box-shadow: 0 6px 16px rgba(22, 93, 255, 0.28);
  flex-shrink: 0;
}
.logo-text {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0;
  white-space: normal;
  overflow-wrap: break-word;
  color: var(--text);
  line-height: 1.2;
}
.logo-sub {
  font-size: 12px;
  color: var(--text-secondary);
  letter-spacing: 0.02em;
  white-space: nowrap;
}
.theme-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 9px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s, background 0.2s;
}
.theme-btn:hover {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-soft);
}

.section-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-secondary);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  flex-shrink: 0;
}
.session-scroll {
  flex: 1;
  min-height: 0;
  margin: 0 -4px;
  padding: 0 4px;
}
.session-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 24px 8px;
  color: var(--text-secondary);
  text-align: center;
}
.session-empty .empty-icon {
  width: 44px;
  height: 44px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  color: var(--primary);
  background: var(--primary-soft);
  margin-bottom: 6px;
}
.session-empty p {
  font-size: 12px;
  color: var(--text);
}
.session-empty span {
  font-size: 12px;
}
.session-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  position: relative;
}
.session-item {
  position: relative;
  border: 1px solid var(--border);
  border-left: 3px solid transparent;
  border-radius: 10px;
  padding: 9px 10px;
  cursor: pointer;
  background: var(--card-bg);
  transition: border-color 0.2s, background 0.2s, transform 0.2s;
}
.session-item:hover {
  border-color: var(--primary-soft);
  transform: translateY(-1px);
}
.session-item.active {
  border-left-color: var(--primary);
  border-color: var(--primary-soft);
  background: var(--primary-soft);
  box-shadow: 0 4px 14px var(--primary-soft);
}
.session-enter-active,
.session-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}
.session-enter-from {
  opacity: 0;
  transform: translateY(-6px);
}
.session-leave-to {
  opacity: 0;
  transform: translateX(-10px);
}
.session-move {
  transition: transform 0.22s ease;
}
.session-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 5px;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.status-dot.running { background: var(--primary); box-shadow: 0 0 0 3px rgba(22,93,255,.12); animation: pulse 1.6s infinite; }
.status-dot.completed { background: var(--success); }
.status-dot.awaiting_input { background: var(--el-color-warning); }
.status-dot.failed { background: var(--danger); }
.session-time {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}
.session-query {
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
.session-actions {
  display: flex;
  gap: 2px;
  margin-top: 3px;
  opacity: 0.8;
  transition: opacity 0.2s;
}
.session-item:hover .session-actions,
.session-item.active .session-actions {
  opacity: 1;
}

.toasts {
  position: fixed;
  top: 16px;
  right: 24px;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
  max-width: 360px;
}
.toast {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 16px;
  border-radius: 10px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.12);
  font-size: 13px;
  pointer-events: auto;
  backdrop-filter: blur(8px);
}
.toast.success { border-left: 3px solid var(--success); }
.toast.error { border-left: 3px solid var(--danger); }
.toast.warning { border-left: 3px solid var(--warning); }
.toast.info { border-left: 3px solid var(--primary); }
.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(20px);
}

@media (max-width: 1380px) {
  .app {
    grid-template-columns: var(--sidebar-size, 300px) minmax(0, 1fr);
    gap: 12px;
    --layout-gap: 12px;
    padding: 12px;
  }
  .app.has-log {
    grid-template-columns: var(--sidebar-size, 300px) minmax(0, 1fr) var(--log-size, 360px);
  }
}
@media (max-width: 1200px) {
  .pane-resizer { display: none; }
}
@media (max-width: 1100px) {
  .app {
    grid-template-columns: 280px minmax(0, 1fr);
  }
  .app.has-log {
    grid-template-columns: 280px minmax(0, 1fr) 340px;
  }
}
/* 有执行过程时，窄屏提前堆叠，避免 1200px 以下三栏把结果区挤得太窄 */
@media (min-width: 1101px) and (max-width: 1200px) {
  .app.has-log {
    display: flex;
    flex-direction: column;
    height: auto;
    min-height: 100vh;
    overflow: auto;
  }
  .app.has-log .sidebar {
    max-height: 560px;
    min-height: 480px;
  }
  .app.has-log .main {
    min-height: 640px;
  }
  .app.has-log .log-side {
    min-height: 360px;
  }
  .app.has-log .taskbar {
    position: sticky;
    bottom: 10px;
  }
}
@media (max-width: 1100px) {
  .app,
  .app.has-log {
    display: flex;
    flex-direction: column;
    height: auto;
    min-height: 100vh;
    overflow: auto;
  }
  .sidebar {
    max-height: 560px;
    min-height: 480px;
  }
  .app:not(.knowledge-mode) .sidebar {
    height: clamp(360px, 46dvh, 440px);
    min-height: 360px;
    max-height: 440px;
    flex-shrink: 0;
  }
  .main {
    min-height: 640px;
  }
  .log-side {
    min-height: 360px;
  }
  .taskbar {
    position: sticky;
    bottom: 10px;
  }
}
@media (max-width: 1100px) {
  .app.business-mode, .app.file-library-mode, .app.module-navigation-mode { display:grid; grid-template-columns:minmax(0,1fr); grid-template-rows:auto minmax(0,1fr); grid-template-areas:'sidebar' 'main'; height:100dvh; min-height:0; overflow:hidden; }
  .app.business-mode .sidebar, .app.file-library-mode .sidebar, .app.module-navigation-mode .sidebar { min-height:0; max-height:none; }
  .app.business-mode .sidebar-content, .app.file-library-mode .sidebar-content { display:none; }
  .app.database-mode .sidebar-content, .app.module-navigation-mode .sidebar-content { display:block; flex:none; }
  .app.database-mode .sidebar-pane, .app.module-navigation-mode .sidebar-pane { height:auto; }
  .app.business-mode .main, .app.file-library-mode .main, .app.module-navigation-mode .main { min-height:0; }
}
</style>

<style scoped>
.history-error{padding:8px 12px;color:var(--el-color-danger);font-size:13px;line-height:1.6}.history-more{width:100%;margin:8px 0}
</style>

<style scoped>
</style>
