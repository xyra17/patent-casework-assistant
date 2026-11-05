<template>
  <div class="event-tree glass-card" :class="{ 'is-collapsed': collapsed }">
    <div class="panel-head">
      <div class="panel-views" role="tablist" aria-label="执行、校验与来源视图">
        <button id="execution-tab" type="button" role="tab" class="panel-view" :class="{active: activeView === 'execution'}"
          :aria-selected="activeView === 'execution'" aria-controls="execution-panel" @click="activeView = 'execution'">
          <el-icon><Monitor /></el-icon>执行过程
        </button>
        <span class="panel-view-divider" aria-hidden="true"></span>
        <button id="reflection-tab" type="button" role="tab" class="panel-view" :class="{active: activeView === 'reflection'}"
          :aria-selected="activeView === 'reflection'" aria-controls="reflection-panel" @click="activeView = 'reflection'">
          <el-icon><CircleCheck /></el-icon>反思校验
        </button>
        <span class="panel-view-divider" aria-hidden="true"></span>
        <button id="evidence-tab" type="button" role="tab" class="panel-view" :class="{active:activeView==='evidence'}" :aria-selected="activeView==='evidence'" aria-controls="evidence-panel" @click="activeView='evidence'">来源（{{ session?.evidence?.length || 0 }}）</button>
      </div>
    </div>

    <section v-show="activeView === 'execution'" id="execution-panel" class="execution-panel" role="tabpanel" aria-labelledby="execution-tab">
    <!-- 控制栏：筛选 + 进度 + 操作 -->
    <div class="control-bar">
      <span class="panel-meta">
        <span v-show="eventFilter === 'all'" class="task-status"><el-tag size="small" effect="plain" :type="statusType">{{ statusLabel }}</el-tag></span>
        <span class="event-count">{{ tree.recordCount }} 条记录</span>
      </span>

      <el-select v-model="eventFilter" size="small" class="filter-select" aria-label="筛选执行记录">
        <el-option v-for="opt in FILTER_OPTIONS" :key="opt.value" :label="opt.label" :value="opt.value" />
      </el-select>
      <div class="bar-actions">
        <el-tooltip :content="allExpanded ? '收起全部阶段' : '展开全部阶段'">
          <el-button link size="small" :aria-label="allExpanded ? '收起全部阶段' : '展开全部阶段'" @click="toggleAll">
            <el-icon><Fold v-if="allExpanded" /><Expand v-else /></el-icon>
          </el-button>
        </el-tooltip>
        <el-tooltip content="清空日志">
          <el-button link size="small" aria-label="清空日志" @click="clearLogs">
            <el-icon><Delete /></el-icon>
          </el-button>
        </el-tooltip>
        <el-tooltip content="终止任务">
          <el-button link size="small" type="danger" aria-label="终止任务" :disabled="!isRunning" @click="$emit('terminate', session.thread_id)">
            <el-icon><VideoPause /></el-icon>
          </el-button>
        </el-tooltip>
      </div>
    </div>
    <el-progress
      v-if="eventFilter === 'all'"
      class="progress-line"
      :percentage="progressPercent"
      :status="progressStatus"
      :stroke-width="3"
      :show-text="false"
    />

    <div v-if="!collapsed && visibleActors.length" class="agent-legend">
      <span v-for="actor in visibleActors" :key="actor.name"><i class="legend-dot" :style="{background:actor.color}"></i>{{ actor.name }}</span>
    </div>

    <!-- 执行树 -->
    <el-scrollbar v-if="!collapsed" ref="scrollbarRef" class="tree-scroll">
      <el-empty v-if="!session || !tree.children.length" :description="emptyDescription" :image-size="80" />
      <div v-else class="tree-body">
        <EventNode :key="`${session?.thread_id}:${eventFilter}`" :node="tree" :expanded-all="allExpanded" />
      </div>
    </el-scrollbar>

    </section>
    <section v-show="activeView === 'reflection'" id="reflection-panel" class="reflection-panel" role="tabpanel" aria-labelledby="reflection-tab">
      <div class="reflection-toolbar">
        <span v-if="session?.round_mode === 'auto' && !session?.plan?.round_budget">自动推荐 · 规划中</span>
        <span v-else>已校验 {{ reflectionHistory.length }} / {{ session?.round_mode === 'auto' ? '推荐上限' : '最多' }} {{ session?.max_rounds || 3 }} 轮</span>
        <el-select v-if="reflectionHistory.length" v-model="selectedRound" size="small" aria-label="选择校验轮次">
          <el-option v-for="(round, index) in reflectionHistory" :key="index" :value="index" :label="`第 ${round.round ?? index + 1} 轮${index === reflectionHistory.length - 1 ? '（最新）' : ''}`" />
        </el-select>
      </div>
      <p v-if="session?.quality_summary" class="quality-stop" role="status">{{ qualitySummaryText(session.quality_summary) }}</p>
      <p v-if="session?.plan?.round_budget" class="reflection-note">{{ session.plan.round_budget.mode === 'auto' ? '自动推荐' : '手动设置' }} {{ session.plan.round_budget.max_rounds }} 轮上限：{{ session.plan.round_budget.reason }}。通过即结束。</p>
      <el-scrollbar class="reflection-scroll">
        <el-empty v-if="!reflection" :description="isRunning ? '任务执行中，等待本轮校验结果' : '暂无校验记录'" :image-size="64" />
        <div v-else class="reflection">
          <p class="reflection-note">规则校验检查格式、数据源和关键统计；统计逐项核对数字、单位、指标与时间；内容分项核对任务、依据、口径、逻辑和不确定性。通过项可展开，待修正项默认展开。</p>
          <div class="reflection-summary">
            <span>规则校验：{{ CHECK_STATES[reflection.hardState] }}</span>
            <span>内容审查：{{ CHECK_STATES[reflection.softState] || '未记录' }}</span>
          </div>
          <section v-if="reflection.issueLifecycle.length" class="verification-section" aria-label="逐项处理记录">
            <h3>问题处理记录</h3>
            <p class="reflection-note">按问题编号逐轮复核；模型复核结论仍可能出错，不能覆盖程序硬错误。</p>
            <div class="verification-list">
              <details v-for="issue in reflection.issueLifecycle" :key="issue.issue_id" class="verification-check" :class="issue.resolved ? 'passed' : issue.kind === 'hard' ? 'failed' : 'unverified'" :open="!issue.resolved">
                <summary><span>{{ issue.issue_id }} · {{ issue.label }}</span><span class="check-state">{{ issue.outcomeText }}</span></summary>
                <div class="verification-body">
                  <p class="r-detail"><strong>发现问题：</strong>{{ issue.detail }}</p>
                  <p v-if="issue.task_quote" class="r-detail"><strong>任务要求：</strong>{{ issue.task_quote }}</p>
                  <p v-if="issue.suggestion && !issue.resolved" class="r-detail"><strong>待处理：</strong>{{ issue.suggestion }}</p>
                  <ol class="issue-history">
                    <li v-for="(step, index) in issue.history" :key="index">
                      <strong>第 {{ step.round }} 轮 · {{ step.outcomeText }}</strong>
                      <p class="r-detail">{{ step.detail }}</p>
                      <div v-if="step.evidenceIds.length" class="verification-sources">
                        <span>复核依据</span><button v-for="id in step.evidenceIds" :key="id" type="button" @click="showEvidence(id)">{{ id }}</button>
                      </div>
                    </li>
                  </ol>
                </div>
              </details>
            </div>
          </section>
          <section v-if="reflection.verification" class="verification-section" aria-label="分项内容审查">
            <h3>分项内容审查</h3>
            <p class="reflection-note">{{ reflection.verification.detail }}</p>
            <div class="verification-list">
              <details v-for="item in reflection.verificationChecks" :key="item.id" class="verification-check" :class="item.state" :open="item.pending">
                <summary><span>{{ item.label }}</span><span class="check-state">{{ item.statusText }}</span></summary>
                <div class="verification-body">
                  <p class="r-detail">{{ item.detail }}</p>
                  <p v-if="item.task_quote" class="r-detail"><strong>任务要求：</strong>{{ item.task_quote }}</p>
                  <blockquote v-if="item.answer_quote" class="answer-location">{{ item.answer_quote }}</blockquote>
                  <div v-if="item.evidenceIds.length" class="verification-sources" aria-label="审查关联来源">
                    <span>关联来源</span><button v-for="id in item.evidenceIds" :key="id" type="button" @click="showEvidence(id)">{{ id }}</button>
                  </div>
                  <p v-if="item.pending && item.suggestion" class="r-detail correction-advice"><strong>{{ item.action_label }}：</strong>{{ item.suggestion }}</p>
                </div>
              </details>
            </div>
          </section>
          <div class="reflection-items">
            <div v-for="item in reflection.items" :key="item.label" class="reflection-item" :class="item.state">
              <div class="check-heading"><span>{{ item.label }}</span><span class="check-state">{{ item.status }}</span></div>
              <p class="r-detail">{{ item.detail }}</p>
            </div>
          </div>
          <section v-if="reflection.gaps.length" class="reflection-issues reflection-gap-records">
            <h3>检索过程缺口</h3>
            <p class="reflection-note">这是检索时的观察记录；后续证据可能已补齐，以本轮内容审查为准。</p>
            <ul><li v-for="gap in reflection.gaps" :key="gap.id">{{ gap.id }} · {{ gap.missing }}</li></ul>
          </section>
          <section v-if="reflection.issues.length" class="reflection-issues">
            <h3>本轮待修正问题</h3>
            <ul><li v-for="(issue, index) in reflection.issues" :key="index">{{ issue }}</li></ul>
          </section>
        </div>
      </el-scrollbar>
    </section>
    <section v-show="activeView==='evidence'" id="evidence-panel" class="sources-panel" role="tabpanel" aria-labelledby="evidence-tab"><EvidencePanel :items="session?.evidence || []" :selected-id="selectedEvidence"/></section>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { buildExecutionTrace, EXECUTION_FILTER_OPTIONS } from '../executionTrace'
import EventNode from './EventNode.vue'
import EvidencePanel from './EvidencePanel.vue'
import { SUB_AGENT_META } from '../store'
import { CHECK_STATES, reflectionDetails, qualitySummaryText } from '../reflection'

const props = defineProps({ session: Object, collapsed: Boolean })
defineEmits(['rerun', 'terminate'])

const FILTER_OPTIONS = EXECUTION_FILTER_OPTIONS
const eventFilter = ref('all')
const activeView = ref('execution')
const selectedEvidence=ref('')
async function showEvidence(id=''){
  selectedEvidence.value=id;activeView.value='evidence'
  await nextTick()
  const card=id ? document.getElementById(`evidence-${id}`) : null
  const container=card?.closest('.evidence-panel')
  if(card && container)container.scrollTo({top:container.scrollTop+card.getBoundingClientRect().top-container.getBoundingClientRect().top,behavior:'smooth'})
}
defineExpose({showEvidence})
watch(()=>props.session?.thread_id,()=>{activeView.value='execution';selectedEvidence.value='';eventFilter.value='all'})
const selectedRound = ref(0)
const reflectionHistory = computed(() => props.session?.hard_rounds || [])
watch(() => [props.session?.thread_id, reflectionHistory.value.length], () => {
  selectedRound.value = Math.max(0, reflectionHistory.value.length - 1)
}, {immediate: true})

const allExpanded = ref(false)
watch(() => [props.session?.thread_id, eventFilter.value], () => { allExpanded.value = false })
function toggleAll() {
  allExpanded.value = !allExpanded.value
}

const scrollbarRef = ref(null)
watch(
  () => [props.session?.thread_id, props.session?.events?.length, eventFilter.value],
  async (now, before) => {
    if (props.collapsed || activeView.value !== 'execution') return
    const wrap = scrollbarRef.value?.wrapRef
    const changedView = !before || now[0] !== before[0] || now[2] !== before[2]
    const firstHistory = props.session?.status !== 'running' && !before?.[1]
    const nearBottom = !wrap || wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 60
    await nextTick()
    if (changedView || firstHistory) scrollbarRef.value?.setScrollTop(0)
    else if (nearBottom) scrollbarRef.value?.setScrollTop(scrollbarRef.value.wrapRef?.scrollHeight || 0)
  }
)

// 点击「展开」时从顶部开始看，避免一展开就停在最新事件、看不到计划/起始步骤。
watch(
  () => props.collapsed,
  async (collapsed) => {
    if (collapsed) return
    await nextTick()
    scrollbarRef.value?.setScrollTop(0)
  }
)

function clearLogs() {
  if (props.session) props.session.events = []
}

const tree = computed(() => buildExecutionTrace(props.session?.events || [], {
  filter: eventFilter.value, meta: SUB_AGENT_META,
}))

const visibleActors = computed(() => [...new Map(tree.value.children.map(stage => [stage.type === 'unknown' ? '归属待确认' : stage.actor, {
  name: stage.type === 'unknown' ? '归属待确认' : stage.actor,
  color: stage.type === 'main' ? 'var(--primary)' : SUB_AGENT_META[stage.actor]?.color || 'var(--text-secondary)',
}])).values()])
const emptyDescription = computed(() => !props.session ? '暂无执行记录，请提交任务'
  : props.session.preparing ? '核对规则后开始执行，当前尚未调用任务工具'
  : FILTER_OPTIONS.find(option => option.value === eventFilter.value)?.emptyLabel || '当前筛选下暂无记录')

const reflection = computed(() => reflectionDetails(reflectionHistory.value[selectedRound.value],
  selectedRound.value === reflectionHistory.value.length - 1 ? props.session?.quality_summary : null))

const isRunning = computed(() => props.session?.status === 'running' && !props.session?.preparing)
const statusLabel = computed(() => props.session?.preparing ? (props.session.ruleReviewPending ? '待确认规则' : '正在核对规则') : ({
  running: '运行中',
  completed: props.session?.degraded ? '未达标' : '已完成',
  failed: '失败',
  cancelled:'已取消',
  awaiting_input:'待你确认',
}[props.session?.status] || '待开始'))
const statusType = computed(() => ({
  running: 'primary',
  completed: props.session?.degraded ? 'warning' : 'success',
  failed: 'danger',
  cancelled:'info',
  awaiting_input:'warning',
}[props.session?.status] || 'info'))
const progressPercent = computed(() => {
  if (!props.session || props.session.preparing || props.session.status === 'cancelled') return 0
  if (props.session.status === 'completed') return 100
  if (props.session.status === 'failed') return 100
  return Math.min(90, 10 + (props.session.events || []).length * 5)
})
const progressStatus = computed(() => {
  if (props.session?.status === 'completed') return 'success'
  if (props.session?.status === 'failed') return 'exception'
  return ''
})
</script>

<style scoped>
.event-tree {
  display: flex;
  flex-direction: column;
  padding: 14px;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.event-tree.is-collapsed {
  flex: 0 0 auto;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  padding-bottom: 8px;
}
.panel-meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.panel-meta :deep(.el-tag) {
  height: 20px;
  padding: 0 6px;
  font-size: 12px;
}
.event-count {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}
.control-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
  padding-bottom: 6px;
}
.bar-actions {
  display: flex;
  align-items: center;
  gap: 1px;
  margin-left: auto;
  flex-shrink: 0;
}
.bar-actions :deep(.el-button) {
  padding: 0 4px;
  margin-left: 0;
}
.progress-line {
  margin: 0 0 8px;
}
.progress-line :deep(.el-progress-bar__outer) {
  background: var(--border);
}
.filter-select {
  width: 126px;
  flex-shrink: 0;
}
.progress {
  flex: 1;
  min-width: 80px;
}
.agent-legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 0;
  padding: 8px 1px 4px;
  font-size: 12px;
  color: var(--text-secondary);
}
.agent-legend span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.legend-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.legend-dot.web { background: var(--web); }
.legend-dot.db { background: var(--db); }
.legend-dot.kb { background: var(--kb); }
.legend-dot.main { background: var(--primary); }
.legend-hint {
  margin-left: auto;
  opacity: 0.72;
  white-space: nowrap;
}
@media (max-width: 1500px) {
  .legend-hint {
    display: none;
  }
}
.tree-scroll {
  flex: 1;
  min-height: 0;
  min-width: 0;
  width: 100%;
}
.tree-body {
  padding: 10px 0;
  min-width: 0;
}

.panel-views { display: flex; align-items: center; gap: 4px; min-width: 0; }
.panel-view { display: inline-flex; align-items: center; gap: 4px; padding: 8px 2px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--text-secondary); font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; white-space: nowrap; }
.panel-view-divider { width: 1px; height: 14px; margin: 0 2px; background: var(--border); flex-shrink: 0; }
.panel-view.active { border-bottom-color: var(--primary); color: var(--primary); }
.panel-view:hover { color: var(--text); }
.panel-view:focus-visible { outline: 2px solid var(--primary); outline-offset: 1px; }
.execution-panel, .reflection-panel { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }
.quality-stop { margin: 0 0 10px; padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px; color: var(--text-secondary); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.reflection-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 4px 0 12px; font-size: 13px; color: var(--text-secondary); }
.reflection-toolbar .el-select { width: 145px; }
.reflection-scroll { flex: 1; min-height: 0; }
.reflection { padding: 0 5px 16px 0; }
.reflection-note { margin: 0 0 12px; color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.reflection-summary { display: flex; flex-direction: column; gap: 6px; padding: 10px; margin-bottom: 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 13px; }
.reflection-items { display: flex; flex-direction: column; gap: 10px; }
.reflection-item { padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); }
.check-heading { display: flex; justify-content: space-between; align-items: center; gap: 10px; font-size: 13px; font-weight: 600; }
.check-state { font-size: 12px; font-weight: 400; color: var(--text-secondary); }
.passed .check-state { color: var(--success-text); }
.failed .check-state { color: var(--danger-text); }
.skipped .check-state, .unverified .check-state { color: var(--status-warn); }
.verification-section { margin-bottom:16px; }
.verification-section h3 { font-size:14px;margin:0 0 6px; }
.verification-list { display:flex;flex-direction:column;gap:6px; }
.verification-check { border:1px solid var(--border);border-radius:8px;background:var(--bg); }
.verification-check summary { display:flex;align-items:center;gap:8px;padding:10px;cursor:pointer;font-size:13px;font-weight:600; }
.verification-check summary::before { content:'▸';color:var(--text-secondary); }
.verification-check[open] summary::before { content:'▾'; }
.verification-check summary .check-state { margin-left:auto;white-space:nowrap; }
.verification-check summary > span:first-child { min-width:0;overflow-wrap:anywhere; }
.verification-check summary .check-state { white-space:normal;text-align:right;flex-shrink:0;max-width:55%; }
.issue-history { padding-left:20px;margin:8px 0 0;font-size:12px;color:var(--text-secondary); }
.issue-history li + li { margin-top:10px; }
.verification-check summary:focus-visible, .verification-sources button:focus-visible { outline:2px solid var(--primary);outline-offset:2px; }
.verification-body { padding:0 10px 10px; }
.answer-location { margin:8px 0;padding:6px 10px;border-left:2px solid var(--border);font-size:13px;color:var(--text-secondary);overflow-wrap:anywhere;white-space:pre-wrap; }
.verification-sources { display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-top:8px;font-size:12px;color:var(--text-secondary); }
.verification-sources button { padding:2px 6px;border:1px solid var(--border);border-radius:4px;background:var(--primary-soft);color:var(--primary);font:inherit;cursor:pointer; }
.correction-advice { padding-top:8px;border-top:1px solid var(--border); }
.r-detail { margin: 8px 0 0; font-size: 13px; color: var(--text-secondary); line-height: 1.7; overflow-wrap: anywhere; }
.reflection-issues { margin-top: 16px; font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.reflection-issues h3 { font-size: 14px; margin: 0 0 8px; }
.reflection-issues ul { padding-left: 20px; color: var(--danger-text); }
.reflection-gap-records ul { color: var(--text-secondary); }
</style>

<style scoped>
.sources-panel{flex:1;min-height:0;overflow:hidden}
</style>
