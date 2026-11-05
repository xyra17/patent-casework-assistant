<template>
  <div class="welcome">
    <section class="hero">
      <div class="hero-glow glow-one"></div>
      <div class="hero-glow glow-two"></div>
      <div class="hero-grid"></div>

      <div class="hero-top">
        <div class="eyebrow"><span class="eyebrow-dot"></span> MULTI-AGENT DATA INTELLIGENCE</div>
        <div class="status-wrap">
          <div class="system-status">
            <span class="status-caption">系统状态</span>
            <span
              v-for="item in statusItems"
              :key="item.key"
              class="status-pill"
              :class="item.state"
              :title="item.title"
            >
              <i></i>
              <span>{{ item.label }}</span>
              <b>{{ item.value }}</b>
            </span>
          </div>
        </div>
      </div>

      <div class="hero-main">
        <div class="hero-copy">
          <h1><span class="grad">Patent Casework Assistant</span><br />{{ store.experience.tagline || '给你完整分析' }}</h1>
          <p class="lead">
            {{ store.experience.description || '主智能体拆解任务，网络、数据库、内部知识库按需检索；硬校验与语义反思辅助纠错，交付有来源的查询结果或报告。' }}
          </p>
        </div>

        <div class="hero-visual" aria-hidden="true">
          <div class="orbit orbit-outer"></div>
          <div class="orbit orbit-inner"></div>
          <div class="core">
            <div class="core-mark"><el-icon :size="24"><MagicStick /></el-icon></div>
            <span>主智能体</span>
            <small>DeepAgents</small>
          </div>
          <div class="agent-pill web"><i></i>网络检索</div>
          <div class="agent-pill db"><i></i>数据库</div>
          <div class="agent-pill kb"><i></i>知识库</div>
          <div class="visual-label">规划 · 检索 · 反思</div>
        </div>
      </div>

      <section class="scenario-section" aria-label="业务场景示例">
      <div class="feature-grid">
        <button
          v-for="item in features"
          :key="item.title"
          type="button"
          class="feature-card"
          :title="`点击填入示例：${item.prompt}`"
          @click="$emit('pick', item)"
        >
          <div class="feature-icon" :style="{ color: item.color }">
            <el-icon :size="19"><component :is="item.icon" /></el-icon>
          </div>
          <div class="feature-copy">
            <h3>{{ item.title }}</h3>
            <p>{{ item.desc }}</p>
            <small v-if="item.attachments">先选择 {{ item.attachments.min }} 份附件</small>
          </div>
          <el-icon class="feature-arrow"><Right /></el-icon>
        </button>
      </div>
      </section>

      <footer class="welcome-foot">
        <span>支持 PDF / Markdown 报告、上传文件分析、文件按会话独立存储</span>
        <span class="foot-hint">点击示例可修改任务；附件可在提交前选择</span>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { store } from '../store'
import { taskTemplates, homeTemplates } from '../taskTemplates'

defineEmits(['pick'])

const featureStyles = [
  { icon: 'Connection', color: '#7c8cff' },
  { icon: 'Search', color: '#c084fc' },
  { icon: 'CircleCheck', color: '#67e8b9' },
  { icon: 'Document', color: '#fbbf24' },
]

const templates = computed(() => taskTemplates(store.experience))
const features = computed(() => {
  return homeTemplates(templates.value).map((item, index) => ({
    ...item,
    ...featureStyles[index % featureStyles.length],
    title: item.label, desc: item.description, prompt: item.value, allowed_sources: item.allowed_sources,
  }))
})

const STATUS_ITEMS = [
  { key: 'web_search', label: '网络检索' },
  { key: 'database', label: '数据库' },
  { key: 'knowledge_base', label: '知识库' },
  { key: 'model', label: '模型接口' },
]

const status = ref(null)
const statusFailed = ref(false)
const statusItems = computed(() =>
  STATUS_ITEMS.map((item) => {
    const entry = status.value?.[item.key]
    if (!status.value) {
      return { ...item, state: 'checking', value: '检测中', title: '正在检测服务状态' }
    }
    if (statusFailed.value || !entry) {
      return { ...item, state: 'unknown', value: '未检测', title: '状态接口暂不可用' }
    }
    return {
      ...item,
      state: entry.state || (entry.ok ? 'ok' : 'error'),
      value: item.key === 'model' && entry.state === 'reachable' ? '已连接' : entry.label,
      title: `${item.label}：${entry.detail || entry.label}`,
    }
  })
)

onMounted(async () => {
  try {
    const response = await fetch('/api/status')
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    status.value = await response.json()
  } catch {
    statusFailed.value = true
    status.value = {}
  }
})
</script>

<style scoped>
.welcome {
  flex: 1;
  min-height: 0;
  display: flex;
}
.hero {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 38px 40px 18px;
  border-radius: 24px;
  color: var(--hero-text);
  background: var(--hero-bg);
  border: 1px solid var(--hero-border);
  box-shadow: 0 24px 70px rgba(8, 12, 26, 0.18);
}
.hero-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: var(--hero-grid-opacity, 0.32);
  background-image:
    linear-gradient(var(--hero-border) 1px, transparent 1px),
    linear-gradient(90deg, var(--hero-border) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: radial-gradient(circle at 50% 40%, #000 0%, transparent 72%);
}
.hero-glow {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
  filter: blur(6px);
}
.glow-one {
  width: 420px;
  height: 420px;
  right: -80px;
  top: -120px;
  background: radial-gradient(circle, var(--hero-glow), transparent 68%);
}
.glow-two {
  width: 360px;
  height: 360px;
  left: -90px;
  bottom: -120px;
  background: radial-gradient(circle, var(--hero-blue-glow), transparent 68%);
}
.hero-top {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-shrink: 0;
}
.hero-top .eyebrow {
  margin-bottom: 0;
}
.status-wrap {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-shrink: 0;
}
.hero-main {
  position: relative;
  z-index: 1;
  /* 允许在宽屏撑满，但不允许在矮屏被压缩到负高度；内容超出时由 .hero 滚动，避免标题被裁切、能力卡与按钮重叠。 */
  flex: 1 0 auto;
  min-height: 0;
  display: flex;
  align-items: center;
  gap: 36px;
}
.hero-copy {
  flex: 1;
  min-width: 0;
  max-width: 700px;
}
.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  letter-spacing: 0.16em;
  color: var(--hero-muted);
  font-weight: 600;
  margin-bottom: 18px;
}
.eyebrow-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #67e8b9;
  box-shadow: 0 0 0 3px rgba(103, 232, 185, 0.14);
}
h1 {
  font-size: clamp(36px, 4.2vw, 58px);
  line-height: 1.18;
  letter-spacing: -0.035em;
  margin-bottom: 16px;
  color: var(--hero-text);
}
.grad {
  background: linear-gradient(105deg, #7c5cff 0%, #b07cff 48%, #60a5fa 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.lead {
  max-width: 590px;
  color: var(--hero-muted);
  font-size: 15px;
  line-height: 1.9;
}
.system-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
  color: var(--hero-muted);
  font-size: 13px;
}
.status-caption {
  font-size: 12px;
  letter-spacing: 0.12em;
  opacity: 0.8;
  margin-right: 2px;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid var(--hero-border);
  background: var(--hero-panel);
  white-space: nowrap;
  font-size: 12px;
}
.status-pill > span {
  color: var(--hero-muted);
  font-weight: 500;
}
.status-pill i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--hero-muted);
  flex-shrink: 0;
}
.status-pill.ok,
.status-pill.reachable {
  background: color-mix(in srgb, var(--status-ok) 10%, var(--hero-panel));
  border-color: color-mix(in srgb, var(--status-ok) 28%, var(--hero-border));
}
.status-pill.ok i,
.status-pill.reachable i {
  background: var(--status-ok);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--status-ok) 15%, transparent);
}
.status-pill.ok b,
.status-pill.reachable b {
  color: var(--status-ok);
}
.status-pill.configured {
  background: color-mix(in srgb, var(--status-configured) 10%, var(--hero-panel));
  border-color: color-mix(in srgb, var(--status-configured) 28%, var(--hero-border));
}
.status-pill.configured i {
  background: var(--status-configured);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--status-configured) 15%, transparent);
}
.status-pill.configured b {
  color: var(--status-configured);
}
.status-pill.auth,
.status-pill.unconfigured,
.status-pill.error {
  background: color-mix(in srgb, var(--status-error) 9%, var(--hero-panel));
  border-color: color-mix(in srgb, var(--status-error) 26%, var(--hero-border));
}
.status-pill.auth i,
.status-pill.unconfigured i,
.status-pill.error i {
  background: var(--status-error);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--status-error) 14%, transparent);
}
.status-pill.auth b,
.status-pill.unconfigured b,
.status-pill.error b {
  color: var(--status-error);
}
.status-pill.empty {
  background: color-mix(in srgb, var(--status-warn) 10%, var(--hero-panel));
  border-color: color-mix(in srgb, var(--status-warn) 26%, var(--hero-border));
}
.status-pill.empty i {
  background: var(--status-warn);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--status-warn) 14%, transparent);
}
.status-pill.empty b {
  color: var(--status-warn);
}
.status-pill.checking i {
  background: var(--status-warn);
  animation: pulse 1.5s ease-in-out infinite;
}
.status-pill.checking b {
  color: var(--status-warn);
}
.status-pill.unknown i {
  background: var(--hero-muted);
}
.status-pill b {
  color: var(--hero-text);
  font-size: 13px;
  font-weight: 700;
}

.hero-visual {
  position: relative;
  width: 360px;
  height: 330px;
  flex-shrink: 0;
}
.orbit {
  position: absolute;
  left: 50%;
  top: 50%;
  border: 1px solid var(--hero-border);
  border-radius: 50%;
  transform: translate(-50%, -50%);
}
.orbit-outer {
  width: 330px;
  height: 330px;
  opacity: 0.45;
  animation: orbitPulse 9s ease-in-out infinite;
}
.orbit-inner {
  width: 240px;
  height: 240px;
  opacity: 0.8;
  animation: orbitPulse 7s ease-in-out infinite reverse;
}
@keyframes orbitPulse {
  0%, 100% { transform: translate(-50%, -50%) scale(1); opacity: 0.55; }
  50% { transform: translate(-50%, -50%) scale(1.035); opacity: 0.85; }
}
.core {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 132px;
  height: 132px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  color: #fff;
  background: radial-gradient(circle at 34% 28%, #a78bfa, #6366f1 46%, #1d4ed8 100%);
  box-shadow:
    0 0 80px var(--hero-glow),
    inset 0 0 0 1px rgba(255, 255, 255, 0.18);
}
.core-mark {
  display: grid;
  place-items: center;
  margin-bottom: 2px;
}
.core span {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.core small {
  font-size: 12px;
  opacity: 0.66;
  letter-spacing: 0.08em;
}
.agent-pill {
  position: absolute;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 999px;
  font-size: 12px;
  color: var(--hero-text);
  background: var(--hero-panel);
  border: 1px solid var(--hero-border);
  backdrop-filter: blur(14px);
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.14);
  white-space: nowrap;
}
.agent-pill i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.agent-pill.web { left: 50%; top: 6px; transform: translateX(-50%); }
.agent-pill.web i { background: #60a5fa; }
.agent-pill.db { right: -2px; bottom: 74px; }
.agent-pill.db i { background: #67e8b9; }
.agent-pill.kb { left: -2px; bottom: 48px; }
.agent-pill.kb i { background: #c084fc; }
.visual-label {
  position: absolute;
  left: 50%;
  bottom: 0;
  transform: translateX(-50%);
  font-size: 12px;
  letter-spacing: 0.2em;
  color: var(--hero-muted);
  white-space: nowrap;
}

.feature-grid {
  position: relative;
  z-index: 1;
  margin-top: -4px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  flex-shrink: 0;
}
.scenario-section {position:relative;z-index:1;flex-shrink:0;}
.feature-copy small {display:block;margin-top:7px;font-size:12px;color:var(--hero-text);}
.feature-card {
  position: relative;
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 14px 30px 14px 14px;
  border: 1px solid var(--hero-border);
  border-radius: 14px;
  background: var(--hero-panel);
  backdrop-filter: blur(16px);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: transform 0.2s, border-color 0.2s, background 0.2s;
}
.feature-card:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.feature-arrow {
  position: absolute;
  right: 10px;
  top: 12px;
  color: var(--hero-muted);
  opacity: 0.45;
  transition: transform 0.2s, opacity 0.2s, color 0.2s;
}
.feature-card:hover .feature-arrow {
  color: var(--hero-text);
  opacity: 0.95;
  transform: translateX(2px);
}
.feature-copy {
  min-width: 0;
}
.feature-card:hover {
  transform: translateY(-2px);
  border-color: rgba(124, 92, 255, 0.38);
  background: rgba(124, 92, 255, 0.09);
}
.feature-icon {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background: rgba(124, 92, 255, 0.12);
  border: 1px solid var(--hero-border);
}
.feature-card h3 {
  font-size: 13px;
  margin-bottom: 4px;
  color: var(--hero-text);
}
.feature-card p {
  font-size: 13px;
  line-height: 1.65;
  color: var(--hero-muted);
}
.welcome-foot {
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding-top: 12px;
  border-top: 1px solid var(--hero-border);
  font-size: 12px;
  color: var(--hero-muted);
  flex-shrink: 0;
}
.foot-hint {
  opacity: 0.75;
}
@media (max-width: 1360px) {
  .feature-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 1240px) {
  .hero {
    padding: 30px 28px 16px;
  }
  .hero-visual {
    width: 300px;
    height: 300px;
    transform: scale(0.9);
    transform-origin: center right;
  }
}
@media (max-width: 980px) {
  .hero-visual {
    display: none;
  }
  .feature-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (prefers-reduced-motion: reduce) {
  .orbit-outer,
  .orbit-inner {
    animation: none;
  }
}
@media (max-width: 640px) {
  .hero {
    padding: 24px 20px 14px;
  }
  .feature-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-height: 860px) {
  .hero {
    padding: 26px 30px 14px;
    gap: 16px;
  }
  .hero-main {
    gap: 24px;
  }
  .eyebrow {
    margin-bottom: 12px;
  }
  h1 {
    font-size: clamp(32px, 3.4vw, 46px);
    margin-bottom: 10px;
  }
  .lead {
    font-size: 13.5px;
    line-height: 1.75;
  }
  .hero-visual {
    width: 290px;
    height: 290px;
    transform: scale(0.88);
    transform-origin: center right;
  }
  .feature-card {
    padding: 10px 28px 10px 12px;
    gap: 9px;
  }
  .feature-icon {
    width: 32px;
    height: 32px;
  }
  .feature-card h3 {
    font-size: 12.5px;
  }
  .feature-card p {
    font-size: 12px;
    line-height: 1.55;
  }
  .welcome-foot {
    padding-top: 8px;
    font-size: 12px;
  }
}
@media (max-height: 760px) {
  .hero {
    padding: 20px 24px 10px;
    gap: 12px;
  }
  .hero-main {
    gap: 0;
  }
  .hero-visual {
    display: none;
  }
  h1 {
    font-size: clamp(30px, 3.2vw, 40px);
  }
  .lead {
    font-size: 12.5px;
    line-height: 1.7;
  }
  .feature-grid {
    gap: 8px;
  }
  .welcome-foot {
    display: none;
  }
}
@media (max-height: 660px) {
  .hero {
    padding: 16px 20px 8px;
    gap: 6px;
  }
  .status-caption {
    display: none;
  }
  h1 {
    font-size: 32px;
  }
  .lead {
    font-size: 12px;
  }
  .feature-card p {
    display: none;
  }
}
</style>
