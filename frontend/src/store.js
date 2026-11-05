// 全局状态：会话列表 + 当前会话 + 主题 + Toast（用 reactive 单例共享）
import { reactive } from 'vue'

export const store = reactive({
  experience: {},
  historyStats: null,
  historyTotal: 0,
  historyBaseline: {},
  sessions: [], // [{ thread_id, query, task_query, require_pdf, status, created_at, events, session_dir, result, evidence, loaded }]
  currentThreadId: '',
  theme: localStorage.getItem('tanyuan-theme') === 'dark' ? 'dark' : 'light',
  chartPalette: localStorage.getItem('tanyuan-chart-palette') || 'nature',
  toasts: [], // [{ id, type, message }]
  pendingQuery: '', // 快捷模板填充到输入框的指令
  pendingSources: null,
  pendingTemplate: null,
})

// 互斥统计：未达标属于执行结束，但不计入已完成。
export function sessionCategory(s) {
  return s.preparing ? 'preparing' : s.status === 'completed' && s.degraded ? 'degraded' : s.status
}

export function setHistorySummary(data) {
  store.historyStats = {...data.stats, preparing:0}
  store.historyTotal = data.total
  store.historyBaseline = data.tracked || Object.fromEntries(store.sessions.map(s => [s.thread_id, sessionCategory(s)]))
}

export function sessionStats() {
  const stats = { running:0, completed:0, failed:0, degraded:0, preparing:0, cancelled:0,awaiting_input:0, ...(store.historyStats || {}) }
  if (store.historyStats) for (const key of Object.values(store.historyBaseline)) {
    if (stats[key] !== undefined) stats[key]--
  }
  for (const s of store.sessions) {
    const key = sessionCategory(s)
    if (stats[key] !== undefined) stats[key]++
  }
  return stats
}

export function sessionTotal() {
  return store.historyStats ? store.historyTotal + store.sessions.length - Object.keys(store.historyBaseline).length : store.sessions.length
}

export function getSession(threadId) {
  return store.sessions.find((s) => s.thread_id === threadId)
}

export function currentSession() {
  return getSession(store.currentThreadId) || null
}

export function addSession(threadId, query, requirePdf) {
  const session = {
    thread_id: threadId,
    query,
    task_query: query, // 实际提交给后端的指令（可能含任务类型提示）
    require_pdf: requirePdf,
    max_rounds: 3,
    quality_summary: null,
    rule_context: {},
    preparing: false,
    ruleReviewPending: false,
    status: 'running', // running | completed | failed
    degraded: false, // completed 但反思达到上限、存在未达标项
    created_at: new Date().toLocaleString('zh-CN'),
    events: [],
    session_dir: '',
    result: '',
    query_result: null,
    plan: null, // 结构化任务计划（plan_created）
    hard_rounds: [], // 反思校验记录（供反思卡片）
    charts: [],
    evidence: [], // 证据链：SQL 行 / 网页 / 知识库 / 文件
    loaded: true, // 历史会话是否已从 /api/runs/{id} 拉取完整事件
  }
  store.sessions.unshift(session)
  store.currentThreadId = threadId
  // 必须返回 store 中的响应式代理；直接返回原始对象会让 onEvent 的
  // events/status/result 修改绕过 Proxy，界面永远停在“运行中/暂无日志”。
  return store.sessions[0]
}

export function switchSession(threadId) {
  store.currentThreadId = threadId
}

export function deleteSession(threadId) {
  const i = store.sessions.findIndex((s) => s.thread_id === threadId)
  if (i >= 0) store.sessions.splice(i, 1)
  if (store.currentThreadId === threadId) {
    store.currentThreadId = store.sessions[0]?.thread_id || ''
  }
}

function syncTheme() {
  const root = document.documentElement
  root.setAttribute('data-theme', store.theme)
  // Element Plus 的 dark css-vars 以 html.dark 为入口；两个标记都维护。
  root.classList.toggle('dark', store.theme === 'dark')
  root.style.colorScheme = store.theme === 'dark' ? 'dark' : 'light'
}

export function toggleTheme() {
  store.theme = store.theme === 'light' ? 'dark' : 'light'
  localStorage.setItem('tanyuan-theme', store.theme)
  syncTheme()
}

// 应用主题到 <html> 的 data-theme / class
export function applyTheme() {
  syncTheme()
}

let toastSeq = 0
export function toast(type, message) {
  const id = ++toastSeq
  store.toasts.push({ id, type, message })
  setTimeout(() => {
    const i = store.toasts.findIndex((t) => t.id === id)
    if (i >= 0) store.toasts.splice(i, 1)
  }, 3000)
}

export const CHART_PALETTES = {
  nature: {
    label: '科研柔和',
    colors: ['#4E79A7', '#F28E2B', '#59A14F', '#B07AA1', '#76B7B2', '#EDC948', '#E15759', '#9C755F'],
  },
  morandi: {
    label: '清新蓝绿',
    colors: ['#3989B9', '#35A697', '#7DBA89', '#72AFD4', '#D1B168', '#8C9FC5', '#5B9290', '#A3BEA7'],
  },
  ocean: {
    label: '蓝紫雅致',
    colors: ['#667BC2', '#A087C3', '#4C9DC1', '#C18FAE', '#86B5C3', '#8D9ACD', '#ADABD0', '#7C9EAD'],
  },
  sunset: {
    label: '经典商务',
    colors: ['#377DB2', '#329B91', '#D1A24D', '#887BAF', '#C47E78', '#6A9EB7', '#93A763', '#A58A74'],
  },
}

export function setChartPalette(name) {
  if (!CHART_PALETTES[name]) return
  store.chartPalette = name
  localStorage.setItem('tanyuan-chart-palette', name)
}

export { QUICK_TEMPLATES } from './taskTemplates.js'

// 子智能体专属配色 + 图标（供执行树渲染）
export const SUB_AGENT_META = {
  网络检索助手: { color: '#409EFF', icon: 'Search' },
  数据库查询助手: { color: '#67C23A', icon: 'DataLine' },
  内部知识库助手: { color: '#9254DE', icon: 'Collection' },
}
