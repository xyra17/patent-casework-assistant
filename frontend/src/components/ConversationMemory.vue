<template>
  <div class="memory-view">
    <p class="memory-intro">正在沿用的任务与条件，以及这次追问参考的历史记录。</p>
    <div class="memory-tabs" role="tablist" aria-label="记忆查看方式">
      <button v-for="tab in tabs" :key="tab.id" :id="tabId(tab.id)" type="button" role="tab"
        :aria-selected="activeTab === tab.id" :aria-controls="panelId(tab.id)" :tabindex="activeTab === tab.id ? 0 : -1"
        @click="activeTab = tab.id" @keydown.right.prevent="moveTab(tab.id)" @keydown.left.prevent="moveTab(tab.id)"
        @keydown.home.prevent="selectTab('current')" @keydown.end.prevent="selectTab('loading')">
        <el-icon aria-hidden="true"><Collection v-if="tab.id === 'current'" /><DataLine v-else /></el-icon>{{ tab.label }}
      </button>
    </div>

    <div v-show="activeTab === 'current'" :id="panelId('current')" role="tabpanel" :aria-labelledby="tabId('current')" tabindex="0" class="memory-tab-content">
      <section class="current-task">
        <header class="memory-section-heading"><h3>当前任务</h3><span class="task-state" :class="{attention: memory.working?.degraded || memory.working?.status === 'failed'}">{{ workingStatus }}</span></header>
        <p class="current-goal" :class="{'goal-collapsed': goalCollapsible && !goalExpanded}">{{ memory.conversation?.goal || '开始对话后，这里会保留当前任务。' }}</p>
        <button v-if="goalCollapsible" type="button" class="memory-text-button" :aria-expanded="goalExpanded" @click="goalExpanded = !goalExpanded">{{ goalExpanded ? '收起任务' : '展开完整任务' }}<el-icon aria-hidden="true"><ArrowUp v-if="goalExpanded" /><ArrowDown v-else /></el-icon></button>
        <dl v-if="conditions.length" class="memory-conditions"><div v-for="item in conditions" :key="item.key"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl>
        <div class="scope-summary">
          <span>案件范围</span>
          <p v-if="caseIds == null">按当前任务查询</p>
          <p v-else-if="!caseIds.length" class="scope-empty">本轮限定范围为空</p>
          <template v-else>
            <div class="memory-case-list"><span v-for="id in shownCases" :key="id">{{ id }}</span></div>
            <button v-if="caseIds.length > 5" type="button" class="memory-text-button" :aria-expanded="casesExpanded" @click="casesExpanded = !casesExpanded">{{ casesExpanded ? '收起案号' : `查看全部 ${caseIds.length} 个案号` }}<el-icon aria-hidden="true"><ArrowUp v-if="casesExpanded" /><ArrowDown v-else /></el-icon></button>
          </template>
        </div>
        <p v-for="item in memory.conversation?.unresolved || []" :key="item" class="memory-caution"><el-icon aria-hidden="true"><Warning /></el-icon>{{ item }}</p>
      </section>

      <section v-if="rules.length" class="memory-block">
        <header class="memory-section-heading"><h3>本轮采用的项目约定</h3><span>{{ rules.length }} 条</span></header>
        <p class="memory-caption">经你确认后，用于后续相关任务。</p>
        <article v-for="rule in rules" :key="rule.id" class="agreed-rule"><header><strong>{{ rule.title }}</strong><span>v{{ rule.revision }}</span></header><p>{{ rule.content }}</p></article>
      </section>
      <details v-else class="no-rules"><summary><el-icon aria-hidden="true"><Collection /></el-icon><span>项目约定</span><span class="memory-caption">暂未采用</span><el-icon class="disclosure-arrow" aria-hidden="true"><ArrowDown /></el-icon></summary><p>可以在回答的更多操作中拟稿，确认后保存为项目长期规则。</p></details>

      <section class="memory-block history-block">
        <header class="memory-section-heading"><h3>{{ isSnapshot ? '本轮回看的记录' : '本轮参考的历史' }}</h3><span v-if="entries.length">{{ entries.length }} 条</span></header>
        <p class="memory-caption">打开当时的回答，查看原始内容与来源。</p>
        <div v-if="entries.length" class="memory-records">
          <button v-for="item in shownEntries" :key="item.turn_id" type="button" class="memory-record" :aria-label="`查看第 ${item.ordinal} 轮原始回答`" @click="emit('open-turn', item)">
            <span class="record-number">{{ String(item.ordinal).padStart(2, '0') }}</span>
            <span class="record-copy"><strong :title="item.question">{{ item.question }}</strong><span class="record-meta">第 {{ item.ordinal }} 轮<span class="record-origin" :class="`origin-${item.layer}`">{{ originLabel(item.layer) }}</span></span></span>
            <el-icon class="record-chevron" aria-hidden="true"><ArrowRight /></el-icon>
          </button>
          <button v-if="entries.length > 3" type="button" class="show-more-records" :aria-expanded="historyExpanded" @click="historyExpanded = !historyExpanded">{{ historyExpanded ? '收起历史记录' : `展开其余 ${entries.length - 3} 条记录` }}<el-icon aria-hidden="true"><ArrowUp v-if="historyExpanded" /><ArrowDown v-else /></el-icon></button>
        </div>
        <p v-else class="memory-empty-copy">{{ load ? '这次追问没有带入历史片段。' : '开始追问后，相关的历史记录会出现在这里。' }}</p>
      </section>
    </div>

    <div v-show="activeTab === 'loading'" :id="panelId('loading')" role="tabpanel" :aria-labelledby="tabId('loading')" tabindex="0" class="memory-tab-content">
      <template v-if="load && !isSnapshot">
        <section class="load-overview">
          <p>本轮加载的上下文</p>
          <div class="total-tokens"><strong>{{ number(load.total_tokens) }}</strong><span>/ {{ number(load.budget.total) }} Token</span></div>
          <progress :value="load.total_tokens" :max="load.budget.total" aria-label="本轮上下文总加载量"></progress>
          <p class="memory-caption">仅带入当前问题需要的内容，完整历史仍保留。</p>
        </section>
        <div class="tier-budgets">
          <section v-for="layer in layers" :key="layer.id" class="tier-budget" :class="`tier-${layer.id}`">
            <header><span class="tier-name"><i aria-hidden="true"></i>{{ layer.label }}</span><span class="tier-token-value">{{ number(load.tokens[layer.id]) }} <span>/ {{ number(load.budget[layer.id]) }}</span></span></header>
            <p>{{ layer.description }}</p>
            <progress :value="load.tokens[layer.id]" :max="load.budget[layer.id]" :aria-label="`${layer.label}本轮加载量`"></progress>
            <span class="tier-record-count">{{ layer.id === 'hot' ? `目标与条件已携带${loadedCount(layer.id) ? ` · 含 ${loadedCount(layer.id)} 条历史` : ''}` : `调入 ${loadedCount(layer.id)} 条记录` }}</span>
          </section>
        </div>
        <p class="token-estimate">Token 按 cl100k_base 估算，合计包含封装开销。</p>
      </template>
      <div v-else class="memory-inline-note"><el-icon aria-hidden="true"><Clock /></el-icon><div><strong>{{ isSnapshot ? '这轮直接回看了历史快照' : '还没有追问加载记录' }}</strong><p>{{ isSnapshot ? '保留原回答，没有为回看调用模型。' : '继续追问后，可在这里查看本轮实际加载量。' }}</p></div></div>

      <section v-if="cache" class="memory-block stored-history">
        <header class="memory-section-heading"><h3>保存的历史记录</h3><span>共 {{ storedTotal }} 条</span></header>
        <dl><div v-for="layer in layers" :key="layer.id"><dt>{{ layer.label }}</dt><dd>{{ cache.stored?.[layer.id] || 0 }}<span> 条</span></dd></div></dl>
      </section>
      <details class="memory-runtime"><summary><span>调度与执行记录</span><span v-if="load?.transitions?.length" class="memory-caption">{{ load.transitions.length }} 次变化</span><el-icon class="disclosure-arrow" aria-hidden="true"><ArrowDown /></el-icon></summary>
        <ul v-if="load?.transitions?.length"><li v-for="item in load.transitions" :key="item.turn_id"><span>第 {{ item.ordinal }} 轮</span><span>{{ tierLabel(item.from) }}<span class="transition-arrow">→</span>{{ tierLabel(item.to) }}</span></li></ul>
        <p v-else>本轮没有历史记录的层级变化。</p>
        <p>工具查询、来源与校验反馈随本次执行保存。当前执行：{{ workingStatus }}。</p>
      </details>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { ArrowDown, ArrowUp, ArrowRight, Clock, Collection, DataLine, Warning } from '@element-plus/icons-vue'

const props = defineProps({ memory: { type: Object, required: true }, threadId: { type: String, required: true }, turns: { type: Array, default: () => [] } })
const emit = defineEmits(['open-turn'])
const activeTab = ref('current')
const goalExpanded = ref(false)
const casesExpanded = ref(false)
const historyExpanded = ref(false)
const tabs = [{ id: 'current', label: '当前记忆' }, { id: 'loading', label: '加载详情' }]
const layers = [
  { id: 'hot', label: '热层', description: '当前目标、筛选条件与常用记录' },
  { id: 'warm', label: '近期层', description: '最近几轮对话的摘要' },
  { id: 'cold', label: '冷层', description: '按当前问题找回的较早记录' },
]
const labels = { owner: '承办人', client: '客户', stage: '案件状态', domain: '技术领域', due_field: '期限节点', due_status: '期限条件', missing_date_field: '日期缺口', start_month: '开始月份', end_month: '结束月份', as_of: '基准日', output_format: '输出格式', fields: '展示字段', metric: '统计指标', time_range: '时间范围' }
const dates = { draft_due_date: '初稿期限（所限）', client_review_due_date: '客户送审期限', filing_due_date: '向局提交期限', received_date: '实际收案日期', actual_filed_date: '实际交局日期' }
function conditionValue(key, value) {
  if (key === 'due_field' || key === 'missing_date_field') return dates[value] || value
  if (key === 'due_status') return { overdue: '已过期限', next14: '未来 14 天', unknown: '日期未记录' }[value] || value
  return Array.isArray(value) ? value.join('、') : value
}
const goalCollapsible = computed(() => (props.memory.conversation?.goal || '').length > 60)
const conditions = computed(() => Object.entries(props.memory.conversation?.conditions || {}).map(([key, value]) => ({ key, label: labels[key] || key, value: conditionValue(key, value) })))
const caseIds = computed(() => props.memory.conversation?.case_ids)
const shownCases = computed(() => casesExpanded.value ? caseIds.value : caseIds.value?.slice(0, 5))
const rules = computed(() => props.memory.long_term?.applied || [])
const cache = computed(() => props.memory.cache)
const load = computed(() => cache.value?.last_load)
const isSnapshot = computed(() => load.value?.retrieval?.method === 'explicit_snapshot_no_model')
const storedTotal = computed(() => layers.reduce((sum, layer) => sum + Number(cache.value?.stored?.[layer.id] || 0), 0))
const entries = computed(() => {
  const records = load.value?.entries || Object.entries(load.value?.loaded || {}).flatMap(([layer, ids]) => ids.map(id => {
    const turn = props.turns.find(item => item.turn_id === id)
    return turn ? { turn_id: id, ordinal: turn.ordinal, question: turn.user_text, layer } : null
  }).filter(Boolean))
  const order = { cold: 0, hot: 1, warm: 2 }
  return [...records].sort((a, b) => order[a.layer] - order[b.layer])
})
const shownEntries = computed(() => historyExpanded.value ? entries.value : entries.value.slice(0, 3))
const workingStatus = computed(() => props.memory.working?.degraded ? '结果待核对' : ({ completed: '执行结束', running: '执行中', failed: '执行失败', cancelled: '已终止', awaiting_input: '待补充' }[props.memory.working?.status] || '已保存'))
const number = value => Number(value || 0).toLocaleString('zh-CN')
const loadedCount = layer => load.value?.loaded?.[layer]?.length || 0
const tierLabel = id => layers.find(layer => layer.id === id)?.label || id
const originLabel = id => ({ hot: '经常引用', warm: '近期对话', cold: isSnapshot.value ? '历史快照' : '按需找回' }[id] || '历史记录')
const tabId = id => `memory-tab-${props.threadId}-${id}`
const panelId = id => `memory-panel-${props.threadId}-${id}`
async function selectTab(id) {
  activeTab.value = id
  await nextTick()
  document.getElementById(tabId(id))?.focus()
}
function moveTab(id) { selectTab(id === 'current' ? 'loading' : 'current') }
</script>

<style scoped>
.memory-view { --memory-muted: color-mix(in srgb, var(--text) 65%, var(--card-bg)); --memory-subtle: color-mix(in srgb, var(--bg) 70%, var(--card-bg)); color: var(--text); font-size: 14px; line-height: 1.65; }
.memory-view button { font: inherit; cursor: pointer; }
.memory-view :is(button, summary, [tabindex]):focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
.memory-intro { margin: 0 0 19px; color: var(--memory-muted); font-size: 13px; line-height: 1.75; }
.memory-tabs { display: flex; gap: 26px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
.memory-tabs button { display: flex; align-items: center; gap: 7px; min-height: 44px; padding: 0 0 12px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--memory-muted); font-size: 14px; }
.memory-tabs button[aria-selected='true'] { color: var(--primary); border-bottom-color: var(--primary); font-weight: 600; }
.memory-tabs button:hover { color: var(--text); }
.memory-tab-content { outline: none; }
.current-task { padding: 19px 20px 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--memory-subtle); }
.memory-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.memory-section-heading h3 { font-size: 13px; line-height: 1.6; font-weight: 600; margin: 0; }
.memory-section-heading > span { font-size: 12px; color: var(--memory-muted); flex-shrink: 0; }
.memory-section-heading .task-state { border: 1px solid var(--border); border-radius: 5px; background: var(--card-bg); padding: 2px 7px; font-size: 11px; }
.task-state.attention, .memory-caution { color: var(--status-warn)!important; }
.current-goal { font-size: 16px; font-weight: 550; line-height: 1.75; margin: 11px 0 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.goal-collapsed { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.memory-text-button { display: inline-flex; gap: 5px; align-items: center; padding: 6px 0; min-height: 32px; margin-top: 3px; border: 0; background: none; color: var(--primary); font-size: 12px!important; }
.memory-text-button:hover { text-decoration: underline; }
.memory-conditions { margin: 17px 0 0; padding-top: 15px; border-top: 1px solid var(--border); }
.memory-conditions > div { display: grid; grid-template-columns: 74px minmax(0, 1fr); gap: 14px; margin: 8px 0; font-size: 13px; }
.memory-conditions dt, .scope-summary > span { color: var(--memory-muted); }
.memory-conditions dd { margin: 0; overflow-wrap: anywhere; }
.scope-summary { margin-top: 15px; font-size: 12px; }
.scope-summary > p { margin: 4px 0 0; color: var(--text); }
.scope-summary .scope-empty { color: var(--status-warn); }
.memory-case-list { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.memory-case-list > span { border: 1px solid var(--border); background: var(--card-bg); border-radius: 5px; padding: 3px 7px; font-size: 11px; font-variant-numeric: tabular-nums; }
.memory-caution { display: flex; align-items: flex-start; gap: 6px; font-size: 12px; margin: 13px 0 0; }
.memory-caution .el-icon { flex-shrink: 0; margin-top: 3px; }
.memory-block { padding: 23px 0 0; }
.memory-caption { font-size: 12px; color: var(--memory-muted); line-height: 1.7; }
.memory-block > .memory-caption { margin: 5px 0 12px; }
.no-rules { margin-top: 16px; padding: 0 0 16px; border-bottom: 1px solid var(--border); }
.no-rules summary, .memory-runtime summary { display: flex; align-items: center; gap: 8px; min-height: 40px; cursor: pointer; list-style: none; font-size: 13px; }
.no-rules summary::-webkit-details-marker, .memory-runtime summary::-webkit-details-marker { display: none; }
.no-rules summary > .el-icon:first-child { color: var(--memory-muted); font-size: 16px; }
.no-rules summary > .memory-caption { margin-left: auto; }
.disclosure-arrow { color: var(--memory-muted); font-size: 12px; }
details[open] > summary .disclosure-arrow { transform: rotate(180deg); }
.no-rules > p { margin: 6px 0 2px; color: var(--memory-muted); font-size: 12px; line-height: 1.8; }
.agreed-rule { margin-top: 12px; padding: 12px 14px; border: 1px solid var(--border); border-radius: 9px; }
.agreed-rule header { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.agreed-rule strong { font-weight: 550; font-size: 13px; }
.agreed-rule header span { color: var(--memory-muted); font-size: 11px; }
.agreed-rule p { font-size: 13px; line-height: 1.8; margin: 5px 0 0; overflow-wrap: anywhere; }
.memory-records { margin: 0 -10px; }
.memory-record { display: flex; width: 100%; align-items: center; gap: 12px; text-align: left; border: 1px solid transparent; border-radius: 9px; background: transparent; padding: 12px 10px; color: var(--text); min-height: 74px; }
.memory-record:hover { background: var(--memory-subtle); border-color: var(--border); }
.record-number { align-self: flex-start; color: var(--memory-muted); font-size: 12px; font-variant-numeric: tabular-nums; padding-top: 3px; min-width: 23px; }
.record-copy { min-width: 0; flex: 1; }
.record-copy strong { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; font-size: 13px; font-weight: 500; line-height: 1.7; }
.record-meta { display: flex; gap: 9px; align-items: center; font-size: 11px; color: var(--memory-muted); margin-top: 5px; }
.record-origin { font-size: 10px; padding: 1px 6px; background: var(--memory-subtle); border-radius: 4px; }
.origin-cold { color: var(--primary); background: var(--primary-soft); }
.record-chevron { color: var(--memory-muted); font-size: 13px; flex-shrink: 0; }
.show-more-records { display: flex; width: calc(100% - 20px); margin: 7px 10px 0; justify-content: center; align-items: center; gap: 6px; min-height: 38px; border: 1px solid var(--border); border-radius: 8px; background: var(--card-bg); color: var(--memory-muted); font-size: 12px!important; }
.show-more-records:hover { color: var(--primary); border-color: var(--primary); }
.memory-empty-copy { font-size: 13px; color: var(--memory-muted); margin: 14px 0 0; }
.load-overview > p:first-child { font-size: 13px; margin: 0 0 5px; color: var(--memory-muted); }
.total-tokens { display: flex; align-items: baseline; gap: 9px; font-variant-numeric: tabular-nums; margin-bottom: 14px; }
.total-tokens strong { font-size: 30px; letter-spacing: -.025em; font-weight: 600; line-height: 1.4; }
.total-tokens > span { font-size: 13px; color: var(--memory-muted); }
.memory-view progress { display: block; appearance: none; width: 100%; height: 5px; border: 0; border-radius: 8px; overflow: hidden; background: var(--memory-subtle); accent-color: var(--meter-color, var(--primary)); }
.memory-view progress::-webkit-progress-bar { background: var(--memory-subtle); border-radius: 8px; }
.memory-view progress::-webkit-progress-value { background: var(--meter-color, var(--primary)); border-radius: 8px; }
.memory-view progress::-moz-progress-bar { background: var(--meter-color, var(--primary)); border-radius: 8px; }
.load-overview > .memory-caption { margin: 10px 0 4px; }
.tier-budgets { margin-top: 23px; border-top: 1px solid var(--border); }
.tier-budget { padding: 17px 0; border-bottom: 1px solid var(--border); }
.tier-hot { --meter-color: color-mix(in srgb, #bd722a 75%, var(--text)); }
.tier-warm { --meter-color: var(--primary); }
.tier-cold { --meter-color: var(--text-secondary); }
.tier-budget header { display: flex; align-items: baseline; gap: 12px; justify-content: space-between; font-size: 13px; }
.tier-name { display: inline-flex; gap: 7px; align-items: center; font-weight: 550; }
.tier-name i { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--meter-color); }
.tier-token-value { font-variant-numeric: tabular-nums; font-size: 12px; }
.tier-token-value > span { color: var(--memory-muted); }
.tier-budget > p { margin: 5px 0 11px 13px; font-size: 12px; color: var(--memory-muted); }
.tier-record-count { display: block; font-size: 11px; color: var(--memory-muted); margin-top: 8px; }
.token-estimate { font-size: 11px; color: var(--memory-muted); margin: 11px 0 0; }
.stored-history > dl { display: grid; grid-template-columns: repeat(3, 1fr); margin: 13px 0 0; gap: 16px; }
.stored-history dt { font-size: 12px; color: var(--memory-muted); }
.stored-history dd { margin: 4px 0 0; font-size: 20px; font-weight: 550; font-variant-numeric: tabular-nums; }
.stored-history dd > span { font-size: 11px; font-weight: 400; color: var(--memory-muted); }
.memory-runtime { border-top: 1px solid var(--border); margin-top: 22px; padding-top: 10px; }
.memory-runtime summary > .memory-caption { margin-left: auto; }
.memory-runtime summary > .disclosure-arrow { margin-left: auto; }
.memory-runtime > p { font-size: 12px; color: var(--memory-muted); line-height: 1.8; margin: 10px 0; }
.memory-runtime ul { list-style: none; padding: 0; margin: 7px 0 12px; }
.memory-runtime li { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding: 6px 0; font-size: 12px; }
.memory-runtime li > span:first-child { color: var(--memory-muted); }
.transition-arrow { display: inline-block; padding: 0 8px; color: var(--memory-muted); }
.memory-inline-note { display: flex; gap: 12px; padding: 16px; background: var(--memory-subtle); border: 1px solid var(--border); border-radius: 10px; }
.memory-inline-note > .el-icon { color: var(--primary); font-size: 19px; margin-top: 2px; }
.memory-inline-note strong { font-size: 13px; font-weight: 550; }
.memory-inline-note p { font-size: 12px; color: var(--memory-muted); line-height: 1.7; margin: 5px 0 0; }
:global(.memory-inspector.el-drawer .el-drawer__header) { margin-bottom: 0; padding: 24px 28px 15px; color: var(--text); }
:global(.memory-inspector.el-drawer .el-drawer__title) { font-size: 19px; font-weight: 600; line-height: 1.6; }
:global(.memory-inspector.el-drawer .el-drawer__body) { padding: 0 28px 28px; }
:global(.memory-inspector.el-drawer .el-drawer__close-btn) { width: 36px; height: 36px; border-radius: 8px; color: var(--text-secondary); }
:global(.memory-inspector.el-drawer .el-drawer__close-btn:hover) { color: var(--text); background: var(--bg); }
@media (max-width: 520px) {
  :global(.memory-inspector.el-drawer .el-drawer__header) { padding: 16px 20px 10px; }
  :global(.memory-inspector.el-drawer .el-drawer__body) { padding: 0 20px 24px; }
  :global(.memory-inspector.el-drawer .el-drawer__close-btn) { width: 44px; height: 44px; }
  .current-task { padding: 16px; }
  .memory-tabs { gap: 25px; margin-bottom: 20px; }
  .memory-tabs button { padding-bottom: 10px; }
  .memory-conditions > div { grid-template-columns: 68px minmax(0, 1fr); gap: 10px; }
  .memory-text-button, .show-more-records { min-height: 44px; }
  .total-tokens strong { font-size: 28px; }
}
</style>
