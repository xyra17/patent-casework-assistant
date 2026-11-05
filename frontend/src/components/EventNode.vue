<template>
  <div class="event-node" :class="{'is-stage':node.stage, 'is-timeline':node.type === 'timeline'}">
    <div class="label" :role="canExpand ? 'button' : undefined" :tabindex="canExpand ? 0 : undefined"
      :aria-expanded="canExpand ? expanded : undefined" @click="toggle" @keydown.enter.prevent="toggle" @keydown.space.prevent="toggle">
      <el-icon v-if="canExpand" class="arrow" :class="{ expanded }">
        <CaretRight />
      </el-icon>
      <span v-else class="arrow leaf">·</span>

      <!-- 子智能体：专属配色 + 图标 -->
      <el-icon v-if="node.icon" class="node-icon" :size="14" :color="node.color">
        <component :is="node.icon" />
      </el-icon>

      <span
        class="badge"
        :class="[node.type, { pulse: node.type === 'running' }]"
        :style="node.color ? { color: node.color, borderColor: node.color } : {}"
      >
        {{ node.label }}
      </span>

      <span v-if="node.stage && node.round" class="round">第 {{ node.round }} 轮</span>
      <span v-if="node.type === 'tool'" class="tool-meta">{{ node.argsSummary }}</span>
      <span v-if="node.duration" class="duration">{{ node.duration }}</span>
      <span v-if="node.severity === 'warning' && node.status !== 'error'" class="status warning">需关注</span>
      <span v-if="node.status === 'running'" class="status running">执行中</span>
      <span v-if="node.status === 'interrupted'" class="status">已中断</span>
      <span v-if="node.status === 'unknown'" class="status">结束状态未记录</span>
      <span v-if="node.status === 'error'" class="status error">失败</span>
      <span v-if="node.summary && node.summary.includes('达到上限')" class="status limit">
        <el-icon><Warning /></el-icon>已达上限
      </span>
      <span v-if="node.timestamp" class="time">{{ node.timestamp }}</span>
    </div>

    <!-- 工具调用展开的完整参数 / 返回摘要 -->
    <div v-if="expanded && ['tool','dispatch'].includes(node.type) && (node.args || node.summary)" class="tool-args collapse-transition">
      <el-button v-if="node.args" link size="small" @click.stop="toggleArgs">查看参数</el-button>
      <el-button v-if="node.summary" link size="small" @click.stop="toggleOutput">查看返回</el-button>
      <pre v-if="showArgs && node.args">{{ JSON.stringify(node.args, null, 2) }}</pre>
      <pre v-if="showOutput && node.summary" class="tool-output">{{ node.summary }}</pre>
    </div>

    <!-- 子智能体最终返回摘要 -->
    <div v-if="expanded && ['assistant','return'].includes(node.type) && node.summary" class="tool-args">
      <el-button link size="small" @click.stop="toggleOutput">查看返回摘要</el-button>
      <pre v-if="showOutput" class="tool-output">{{ node.summary }}</pre>
    </div>

    <div v-if="expanded && node.children && node.children.length" class="children collapse-transition">
      <EventNode v-for="(child, i) in node.children" :key="child.key || i" :node="child" :expanded-all="expandedAll" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({ node: Object, expandedAll: { type: Boolean, default: false } })
// 保留时间线标题与阶段列表；阶段及工具详情默认折叠，手动展开不受新事件影响。
const expanded = ref(props.node.type === 'timeline' || props.expandedAll || Boolean(props.node.initialExpanded))
const canExpand = computed(() => Boolean(props.node.children?.length || (['tool','dispatch'].includes(props.node.type) && (props.node.args || props.node.summary)) || (['assistant','return'].includes(props.node.type) && props.node.summary)))
watch(() => props.expandedAll, (value) => { expanded.value = props.node.type === 'timeline' || value })
const showArgs = ref(false)
const showOutput = ref(false)
function toggle() {
  if (!canExpand.value) return
  expanded.value = !expanded.value
}
function toggleArgs() {
  showArgs.value = !showArgs.value
}
function toggleOutput() {
  showOutput.value = !showOutput.value
}
</script>

<style scoped>
.event-node {
  margin-left: 14px;
}
.event-node.is-timeline, .event-node.is-stage { margin-left: 0; }
.is-stage > .label { margin-top: 10px; padding: 6px 0; flex-wrap: wrap; }
.is-stage > .label .badge { font-weight: 600; overflow-wrap: anywhere; }
.round { font-size: 12px; color: var(--text-secondary); }
.badge.timeline { font-size: 13px; color: var(--text-secondary); padding-left: 0; }
.badge.activity, .badge.dispatch, .badge.return { color: var(--text-secondary); white-space: normal; overflow-wrap: anywhere; }
.label {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 0;
  cursor: pointer;
}
.label:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; border-radius: 4px; }
.arrow {
  width: 14px;
  color: var(--text-secondary);
  transition: transform 0.2s;
}
.arrow.expanded {
  transform: rotate(90deg);
}
.arrow.leaf {
  color: var(--border);
}
.node-icon {
  flex-shrink: 0;
}
.badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 13px;
  border: 1px solid transparent;
}
.badge.main {
  background: var(--primary-soft);
  color: var(--primary);
}
.badge.assistant {
  background: var(--card-bg);
  border-style: solid;
}
.badge.tool {
  background: var(--bg);
  color: var(--text-secondary);
}
.badge.session {
  background: rgba(146, 84, 222, 0.12);
  color: var(--kb-text);
}
.badge.plan {
  background: rgba(22, 93, 255, 0.09);
  color: var(--primary);
  font-weight: 600;
  white-space: normal;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.badge.step {
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  border: none;
  padding-left: 2px;
}
.badge.result {
  background: rgba(103, 194, 58, 0.12);
  color: var(--success-text);
}
.badge.error {
  background: rgba(245, 108, 108, 0.12);
  color: var(--danger-text);
}
.time {
  margin-left: auto;
  padding-left: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  flex-shrink: 0;
}
.duration {
  font-size: 13px;
  color: var(--text-secondary);
  background: var(--bg);
  border-radius: 3px;
  padding: 0 4px;
}
.status {
  white-space: nowrap;
  flex-shrink: 0;
  font-size: 12px;
  padding: 0 4px;
  border-radius: 3px;
}
.status.running {
  color: var(--primary);
  background: rgba(22, 93, 255, 0.1);
}
.status.warning { color: var(--warning); background: rgba(230, 162, 60, 0.14); }
.status.error {
  color: var(--danger);
  background: rgba(245, 108, 108, 0.12);
}
.status.limit {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: var(--warning);
  background: rgba(230, 162, 60, 0.14);
}
.tool-meta {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}
.tool-args {
  margin-left: 18px;
  padding: 4px 0;
}
.tool-args pre {
  font-size: 13px;
  background: var(--bg);
  border-radius: 4px;
  padding: 8px;
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.tool-args pre.tool-output {
  max-height: 260px;
  color: var(--text-secondary);
}
.children {
  border-left: 1px dashed var(--border);
}
</style>
