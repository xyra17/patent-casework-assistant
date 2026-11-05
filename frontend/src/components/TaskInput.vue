<template>
  <div class="task-input glass-card">
    <div class="mode-row">
      <div class="mode-main">
        <div class="mode-left">
          <span class="mode-label">任务模式</span>
          <el-radio-group v-model="mode" size="small" class="mode-group" :disabled="submitting" aria-label="任务模式">
            <el-radio-button value="auto">
              <span class="mode-option"><el-icon><MagicStick /></el-icon>自动识别</span>
            </el-radio-button>
            <el-radio-button value="quick">
              <span class="mode-option"><el-icon><ChatLineRound /></el-icon>快速问答</span>
            </el-radio-button>
            <el-radio-button value="analysis">
              <span class="mode-option"><el-icon><DataAnalysis /></el-icon>数据分析</span>
            </el-radio-button>
            <el-radio-button value="report">
              <span class="mode-option"><el-icon><Document /></el-icon>报告</span>
            </el-radio-button>
          </el-radio-group>
        </div>
        <div class="task-options" role="group" aria-label="任务设置">
          <el-checkbox v-model="requirePdf" class="pdf-option" :disabled="submitting" @change="onPdfChange">生成 PDF</el-checkbox>
          <input ref="fileChooser" type="file" multiple :accept="ATTACHMENT_ACCEPT" :disabled="submitting" aria-label="选择本次任务附件" class="file-chooser" @change="chooseFiles" />
          <el-button class="attach-button" size="small" plain :disabled="submitting" @click="fileChooser?.click()"><el-icon class="el-icon--left"><Upload /></el-icon>添加附件</el-button>
        </div>
      </div>
      <div class="settings-anchor">
        <el-popover v-model:visible="settingsVisible" placement="top-end" :width="310" trigger="click">
          <template #reference>
            <el-button class="more-settings" size="small" text :disabled="submitting" :aria-expanded="settingsVisible" aria-label="更多设置"><el-icon class="el-icon--left"><Setting /></el-icon>更多设置<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
          </template>
          <section class="composer-settings" aria-label="更多任务设置">
            <div class="source-option"><SourceSelector v-model="allowedSources" :disabled="submitting" :teleported="false" remember-notice /></div>
            <div class="round-option">
              <label for="composer-rounds">处理轮次</label>
              <el-select id="composer-rounds" :teleported="false" v-model="maxRounds" size="small" aria-label="最多处理轮次" :disabled="submitting">
                <el-option value="auto" label="自动推荐" />
                <el-option v-for="n in [1,2,3,4,5,6]" :key="n" :value="n" :label="`${n} 轮`" />
              </el-select>
            </div>
          </section>
        </el-popover>
      </div>
    </div>

    <div class="input-row">
      <el-input
        ref="queryInput"
        v-model="query"
        aria-label="分析任务"
        type="textarea"
        :disabled="submitting"
        :autosize="{ minRows: 2, maxRows: 6 }"
        :placeholder="store.experience.placeholder || '例如：查缓存隔离方案的相似案件，或比较2026年9月和10月收案与交局数量'"
        @keydown.ctrl.enter="submit"
      />
      <div class="submit-col">
        <el-button type="primary" class="submit-btn" :loading="submitting" :disabled="!query.trim() || Boolean(attachmentHint)" @click="submit">
          <el-icon v-if="!submitting" class="el-icon--left"><Position /></el-icon>
          {{ submitting && attachments.length ? '上传并提交' : '提交任务' }}
        </el-button>
        <span class="kbd-hint">Ctrl + Enter</span>
      </div>
    </div>

    <div v-if="attachments.length || attachmentHint || attachmentError" class="task-attachments">
      <span v-if="attachments.length" class="foot-hint">{{ attachments.length }} 份附件 · 按选择顺序</span>
      <div v-for="(file,index) in attachments" :key="file.name" class="attachment-chip">
        <span :title="file.name">{{ selectedTemplate?.attachments?.min === 2 ? (index === 0 ? '基准 · ' : '对比 · ') : '' }}{{ file.name }}</span>
        <button type="button" :disabled="submitting" :aria-label="`移除待上传附件 ${file.name}`" @click="attachments.splice(index,1)"><el-icon><Close /></el-icon></button>
      </div>
      <p v-if="attachmentHint" class="attachment-instruction" role="status">{{ attachmentHint }}</p>
      <p v-if="attachmentError" class="attachment-error" role="alert">{{ attachmentError }}</p>
    </div>

    <div class="input-foot">
      <div class="quick-templates" role="group" aria-label="快捷模板">
        <span class="template-label">快捷模板</span>
        <el-button v-for="t in featuredQuickTemplates" :key="t.label" size="small" plain :disabled="submitting"
          :title="t.value" @click="fillFromTemplate(t)">{{ t.label }}</el-button>
        <el-dropdown trigger="click" placement="top-end" popper-class="scenario-dropdown" :disabled="submitting" @command="fillFromTemplate">
          <el-button class="scenario-trigger" size="small" plain :disabled="submitting">业务场景（{{ quickTemplates.length }}）<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu class="scenario-menu" aria-label="业务场景">
              <li v-for="group in scenarioGroups" :key="group.id" class="scenario-group" :data-category="group.id" role="presentation">
                <span class="scenario-group-label" aria-hidden="true">{{ group.label }}</span>
                <ul class="scenario-group-items" role="group" :aria-label="group.label">
                  <el-dropdown-item v-for="item in group.items" :key="item.id || item.label" :command="item" class="scenario-item">{{ item.label }}</el-dropdown-item>
                </ul>
              </li>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
      <span class="foot-hint">支持表格 / Word / PDF / 图片上传 · 文件按会话独立存储</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { Setting } from '@element-plus/icons-vue'
import { store, toast } from '../store'
import { TASK_GROUPS, taskTemplates, featuredTemplates, groupedTemplates, attachmentRequirement, attachmentTaskContext } from '../taskTemplates'
import { ATTACHMENT_ACCEPT, stageAttachments } from '../sessionFiles'
import SourceSelector from './SourceSelector.vue'
import {readSourcePreference, saveSourcePreference} from '../sourceSelection'

const quickTemplates = computed(() => taskTemplates(store.experience))
const scenarioGroups = computed(() => TASK_GROUPS.map(group => ({
  ...group, items: groupedTemplates(quickTemplates.value, group.id),
})).filter(group => group.items.length))
const featuredQuickTemplates = computed(() => featuredTemplates(quickTemplates.value, store.experience))
onMounted(async () => {
  try {
    const response = await fetch('/api/semantic')
    if (response.ok) store.experience = (await response.json()).experience || {}
  } catch { /* 通用模板仍可使用。 */ }
})

const props = defineProps({submitTask: {type:Function, required:true}})
const query = ref('')
const requirePdf = ref(false)
const mode = ref('auto')
const maxRounds = ref('auto')
const submitting = ref(false)
const settingsVisible = ref(false)
watch(submitting,value=>{if(value)settingsVisible.value=false})
const queryInput = ref(null)
const fileChooser = ref(null)
const attachments = ref([])
const selectedTemplate = ref(null)
const attachmentError = ref('')
const attachmentHint = computed(()=>attachmentRequirement(selectedTemplate.value,attachments.value))
const allowedSources = ref(readSourcePreference(localStorage))
watch(allowedSources, values=>saveSourcePreference(localStorage,values), {deep:true})

watch(mode, (value) => {
  if (value === 'quick' || value === 'analysis') requirePdf.value = false
})
watch(query,value=>{
  if (selectedTemplate.value && value!==selectedTemplate.value.value) selectedTemplate.value=null
})

// 欢迎页示例可填入输入框。
watch(
  () => store.pendingQuery,
  (v) => {
    if (v) {
      query.value = v
      if(store.pendingTemplate) {
        mode.value=store.pendingTemplate.mode || 'auto'
        requirePdf.value=Boolean(store.pendingTemplate.requirePdf)
        selectedTemplate.value=store.pendingTemplate
      } else selectedTemplate.value=null
      if(Array.isArray(store.pendingSources))allowedSources.value=[...store.pendingSources]
      store.pendingSources=null
      store.pendingTemplate=null
      store.pendingQuery = ''
    }
  }
)

function onPdfChange(checked) {
  if (checked) mode.value = 'report'
}

function fillFromTemplate(template) {
  if (submitting.value) return
  mode.value = template.mode || 'auto'
  requirePdf.value = Boolean(template.requirePdf)
  query.value = template.value
  selectedTemplate.value = template
  if(Array.isArray(template.allowed_sources))allowedSources.value=[...template.allowed_sources]
  queryInput.value?.focus()
}

function chooseFiles(event) {
  attachmentError.value=''
  try {attachments.value=stageAttachments(attachments.value,Array.from(event.target.files || []))}
  catch(error) {attachmentError.value=error.message}
  event.target.value=''
}

async function submit() {
  if (submitting.value || !query.value.trim() || attachmentHint.value) return
  submitting.value = true
  try {
    const accepted = await props.submitTask(query.value.trim(), requirePdf.value, mode.value, maxRounds.value,
      [...allowedSources.value], [...attachments.value], attachmentTaskContext(selectedTemplate.value,attachments.value))
    if (accepted) {
      allowedSources.value=allowedSources.value.filter(id=>id!=='web_search')
      query.value=''
      attachments.value=[]
      selectedTemplate.value=null
      attachmentError.value=''
    }
  } catch(error) {toast('error',error.message || '任务提交失败，附件与问题已保留')}
  finally {submitting.value=false}
}
</script>

<style scoped>
.task-input {
  padding: 11px 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 9px;
  border-radius: 16px;
  background:
    linear-gradient(120deg, rgba(22, 93, 255, 0.035), transparent 42%),
    var(--card-bg);
}
.task-attachments {display:flex;align-items:center;gap:7px;flex-wrap:wrap;min-width:0;}
.file-chooser {display:none;}
.attachment-chip {display:flex;align-items:center;gap:4px;max-width:270px;padding:4px 7px;border:1px solid var(--border);border-radius:6px;background:var(--bg);font-size:12px;}
.attachment-chip span {overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.attachment-chip button {display:grid;place-items:center;border:0;background:transparent;color:var(--text-secondary);padding:4px;cursor:pointer;}
.attachment-chip button:focus-visible {outline:2px solid var(--primary);}
.attachment-instruction,.attachment-error {flex-basis:100%;font-size:12px;margin:0;line-height:1.5;}
.attachment-instruction {color:var(--text-secondary);}
.attachment-error {color:var(--status-error);}
.mode-row {
  display: grid;
  grid-template-columns:minmax(0,1fr) 118px;
  align-items:center;
  gap:10px;
}
.mode-main {display:flex;align-items:center;gap:8px 20px;flex-wrap:wrap;min-width:0;}
.settings-anchor {display:flex;justify-content:center;min-width:0;}
.more-settings.el-button {height:30px;padding:0 6px;color:var(--text-secondary);font-size:13px;}
.attach-button.el-button {height:32px;margin-left:0;background:var(--bg);color:var(--text-secondary);border-color:var(--border);border-radius:7px;}
.composer-settings {display:flex;flex-direction:column;gap:12px;}
.composer-settings .source-option {display:flex;}
.composer-settings :deep(.source-trigger) {width:100%;justify-content:space-between;}
.composer-settings .round-option {justify-content:space-between;}
.mode-left {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  min-width: 0;
  flex: 0 1 auto;
}
.mode-label {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--text-secondary);
  white-space: nowrap;
}
.mode-group :deep(.el-radio-button__inner) {
  padding: 6px 10px;
  font-size: 12px;
  border-color: var(--border);
}
.mode-option {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.mode-group :deep(.el-radio-button:first-child .el-radio-button__inner) {
  border-radius: 7px 0 0 7px;
}
.mode-group :deep(.el-radio-button:last-child .el-radio-button__inner) {
  border-radius: 0 7px 7px 0;
}
.mode-group :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: linear-gradient(135deg, var(--primary), #8b5cf6) !important;
  border-color: transparent !important;
  color: #fff !important;
  box-shadow: 0 4px 14px rgba(109, 92, 255, 0.26);
}
.round-option { display: flex; align-items: center; gap: 4px; height: 32px; padding: 0 4px 0 10px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg); color: var(--text-secondary); font-size: 13px; white-space: nowrap; box-sizing: border-box; }
.task-options { display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0;flex:0 0 auto; }
.round-option .el-select { width: 106px; }
.round-option :deep(.el-select__wrapper) { min-height: 28px; background: transparent; box-shadow: none; padding: 0 6px; }
.round-option:focus-within { outline: 2px solid var(--primary); outline-offset: 2px; }
.pdf-option {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--bg);
  margin-right: 0;
}
.pdf-option.is-checked { border-color: var(--primary); background: var(--primary-soft); }
.pdf-option :deep(.el-checkbox__label) {
  font-size: 13px;
  color: var(--text-secondary);
}
.quick-templates {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.template-label {
  font-size: 13px;
  color: var(--text-secondary);
  margin-right: 2px;
}
.quick-templates :deep(.el-button + .el-button) { margin-left: 0; }
.quick-templates :deep(.el-button) {
  border-color: var(--border);
  color: var(--text-secondary);
  background: transparent;
}
.quick-templates :deep(.el-button:hover) {
  color: var(--primary);
  border-color: var(--primary);
}
.quick-templates :deep(.scenario-trigger.el-button) {
  color: var(--primary);
  background: color-mix(in srgb, var(--primary) 6%, var(--card-bg));
  border-color: color-mix(in srgb, var(--primary) 24%, var(--border));
}
.quick-templates :deep(.scenario-trigger.el-button:hover) { background: var(--primary-soft); }
:global(.scenario-dropdown.el-popper) {
  border-radius: 12px;
  border-color: var(--border);
  box-shadow: 0 14px 40px rgba(15, 23, 42, 0.14);
}
.scenario-menu {
  width: 366px;
  max-width: calc(100vw - 28px);
  max-height: min(620px, calc(100dvh - 100px));
  overflow-y: auto;
  overscroll-behavior: contain;
  box-sizing: border-box;
  padding: 5px 12px;
  border-radius: 11px;
  background: var(--card-bg);
}
.scenario-group {
  --category-color: var(--primary);
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 12px;
  padding: 7px 0;
  list-style: none;
}
.scenario-group[data-category='daily'] { --category-color: #245dcc; }
.scenario-group[data-category='complex'] { --category-color: #7941b5; }
.scenario-group[data-category='analytics'] { --category-color: #207351; }
.scenario-group[data-category='research'] { --category-color: #147580; }
.scenario-group[data-category='attachments'] { --category-color: #966318; }
:global([data-theme='dark'] .scenario-group[data-category='daily']) { --category-color: #8bb5ff; }
:global([data-theme='dark'] .scenario-group[data-category='complex']) { --category-color: #c8a3ff; }
:global([data-theme='dark'] .scenario-group[data-category='analytics']) { --category-color: #80d8ae; }
:global([data-theme='dark'] .scenario-group[data-category='research']) { --category-color: #79d0dc; }
:global([data-theme='dark'] .scenario-group[data-category='attachments']) { --category-color: #e7c17c; }
.scenario-group + .scenario-group { border-top: 1px solid var(--border); }
.scenario-group-label {
  align-self: start;
  justify-self: start;
  margin-top: 1px;
  padding: 5px 8px;
  border-radius: 6px;
  border: 1px solid color-mix(in srgb, var(--category-color) 15%, transparent);
  color: var(--category-color);
  background: color-mix(in srgb, var(--category-color) 8%, var(--card-bg));
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  white-space: nowrap;
}
.scenario-group-items { min-width: 0; padding: 0; margin: 0; list-style: none; }
.scenario-menu :deep(.scenario-item) {
  min-height: 30px;
  padding: 5px 10px;
  border-radius: 5px;
  color: var(--text);
  font-size: 13px;
  line-height: 20px;
  white-space: normal;
  overflow-wrap: anywhere;
}
.scenario-menu :deep(.scenario-item:not(.is-disabled):hover),
.scenario-menu :deep(.scenario-item:not(.is-disabled):focus) {
  color: var(--category-color);
  background: color-mix(in srgb, var(--category-color) 9%, var(--card-bg));
}
.scenario-menu :deep(.scenario-item:focus-visible) {
  outline: 2px solid var(--category-color);
  outline-offset: -2px;
}
.input-row {
  display: flex;
  gap: 10px;
  align-items: stretch;
}
.input-row :deep(.el-textarea) {
  flex: 1;
  min-width: 0;
}
.input-row :deep(.el-textarea__inner) {
  min-height: 58px !important;
  line-height: 1.6;
}
.submit-col {
  width: 118px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
}
.submit-btn {
  width: 100%;
  height: 42px;
  border: 0 !important;
  color: #fff !important;
  background: linear-gradient(135deg, var(--primary), #8b5cf6) !important;
  box-shadow: 0 8px 22px rgba(109, 92, 255, 0.28);
}
.submit-btn:hover {
  filter: brightness(1.05);
}
.kbd-hint {
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
  white-space: nowrap;
}
.input-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 7px;
  border-top: 1px dashed var(--border);
}
.foot-hint {
  font-size: 12px;
  color: var(--text-secondary);
}
@media (max-height: 760px) {
  .task-input {
    padding: 8px 12px 7px;
    gap: 7px;
  }
  .input-row :deep(.el-textarea__inner) {
    min-height: 48px !important;
  }
  .submit-btn {
    height: 38px;
  }
  .submit-col {
    width: 108px;
  }
  .mode-row {grid-template-columns:minmax(0,1fr) 108px;}
  .input-foot {
    padding-top: 5px;
  }
}
@media (max-width: 720px) {
  .mode-row {grid-template-columns:minmax(0,1fr);gap:4px;}
  .settings-anchor {justify-content:flex-end;}
  .mode-left { gap:6px 8px; }
  .mode-label { letter-spacing:0; }
  .task-options { flex-basis:100%; }
  .mode-main {gap:8px;}
  .mode-group :deep(.el-radio-button__inner) {padding:6px 8px;}
  .submit-btn {width:118px;}
  .input-row {
    flex-direction: column;
  }
  .submit-col {
    width: 100%;
    flex-direction: row;
    align-items: center;
    justify-content: flex-end;
  }
  .kbd-hint {
    margin-right: auto;
  }
}
</style>
