<template>
  <section class="rules-panel glass-card" aria-label="长期规则管理">
    <ModuleNavigation title="长期规则" :target="sidebarTarget">
      <div class="nav-links">
        <button v-for="item in filters" :key="item.value" type="button" :class="{active:filter===item.value}" :aria-current="filter===item.value?'page':undefined" @click="filter=item.value"><el-icon aria-hidden="true"><component :is="item.icon" /></el-icon>{{ item.label }}<span class="nav-count">{{ item.count }}</span></button>
      </div>
      <div class="nav-links nav-secondary">
        <button type="button" aria-haspopup="dialog" @click="create"><el-icon aria-hidden="true"><Plus /></el-icon>新建规则</button>
        <el-dropdown trigger="click" @command="fromTemplate">
          <button type="button" aria-haspopup="menu" :disabled="!templates.length"><el-icon aria-hidden="true"><DocumentCopy /></el-icon>从示例创建<el-icon aria-hidden="true" class="template-arrow"><ArrowDown /></el-icon></button>
          <template #dropdown><el-dropdown-menu><el-dropdown-item v-for="item in templates" :key="item.key" :command="item.key">{{ item.title }}</el-dropdown-item></el-dropdown-menu></template>
        </el-dropdown>
      </div>
    </ModuleNavigation>
    <header class="rules-header"><div class="rules-heading"><h1>长期规则</h1><span>/ {{ filters.find(item=>item.value===filter)?.label }}</span><span role="status">{{ visibleRules.length }} 条规则</span></div><el-button :loading="loading" @click="load">刷新</el-button></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="rule-list" v-loading="loading">
      <el-empty v-if="!rules.length && !loading" description="暂无规则，可从示例创建。" :image-size="80" />
      <article v-for="rule in visibleRules" :key="rule.id" class="rule-card">
        <div class="rule-heading"><h2>{{ rule.title }}</h2><el-switch :model-value="rule.active" :disabled="acting" :aria-label="`启用规则：${rule.title}`" inline-prompt active-text="启用" inactive-text="停用" @change="toggle(rule)" /><span class="muted">{{ RULE_CATEGORIES[rule.category] }} · v{{ rule.revision }}</span></div>
        <p class="rule-content">{{ rule.content }}</p>

        <p class="rule-scope"><strong>适用范围</strong> {{ rule.scope_note }}<span class="muted"> · {{ rule.match_mode === 'all_tasks' ? '所有任务' : '匹配：'+rule.match_terms.join('、') }}</span></p>
        <p v-if="rule.binding" class="muted">执行定义：{{ rule.binding.metric_key }}{{ rule.binding.formula ? ` · ${rule.binding.formula_mode==='aggregate'?'先汇总再计算':'逐行计算后汇总'} · ${formulaLabel(rule.binding.formula)}` : rule.binding.aggregation ? ` · ${rule.binding.aggregation}(${rule.binding.field})` : ' · 沿用指标表达式' }} · {{ rule.binding.filters.length }} 项默认过滤</p>
        <details v-if="rule.source_snapshot"><summary>来源会话：{{ rule.source_query || rule.source_thread_id?.slice(0,8) }} · #{{ rule.source_revision_id }}</summary><p class="rule-content">{{ rule.source_snapshot }}</p><span class="muted">保留确认时的快照；删除来源会话不会自动删除此项目规则。</span></details>
        <div class="rule-actions"><el-button link type="primary" @click="edit(rule)">编辑</el-button><el-button link @click="showVersions(rule)">版本记录</el-button><el-button link type="danger" :disabled="acting" @click="remove(rule)">删除</el-button></div>
      </article>
      <el-empty v-if="rules.length && !visibleRules.length" description="此状态下暂无规则" :image-size="55" />
    </div>
    <RuleEditor v-model:visible="editorVisible" :draft="draft" :source-thread="draft?.source_thread_id" :source-revision="draft?.source_revision_id" :source-snapshot="draft?.source_snapshot" :source-query="draft?.source_query" @saved="saved" />
    <el-dialog v-model="versionsVisible" title="规则版本记录" width="min(650px,94vw)"><div class="version-list"><article v-for="item in versions" :key="item.revision"><strong>v{{ item.revision }} · {{ item.payload.active ? '启用' : '停用' }}</strong><p class="muted">{{ new Date(item.created_at).toLocaleString('zh-CN', {hour12:false}) }}</p><p class="rule-content">{{ item.payload.content }}</p><p class="muted">范围：{{ item.payload.scope_note }} · 适用：{{ item.payload.match_mode === 'all_tasks' ? '所有任务' : item.payload.match_terms.join('、') }}</p><p v-if="item.payload.binding" class="muted">绑定指标：{{ item.payload.binding.metric_key }} · {{ item.payload.binding.formula ? `${item.payload.binding.formula_mode==='aggregate'?'先汇总再计算':'逐行计算后汇总'} · ${formulaLabel(item.payload.binding.formula)}` : item.payload.binding.aggregation ? `${item.payload.binding.aggregation}(${item.payload.binding.field})` : '沿用指标表达式' }}<br>默认过滤：{{ item.payload.binding.filters.map(f => `${f.field} ${operators[f.op] || f.op} ${Array.isArray(f.value) ? f.value.join('、') : f.value}`).join('；') || '无' }}</p></article></div></el-dialog>
  </section>
</template>
<script setup>
import {computed,onMounted,ref} from 'vue'
import {ElMessageBox} from 'element-plus'
import {List, CircleCheck, CircleClose, Plus, DocumentCopy, ArrowDown} from '@element-plus/icons-vue'
import ModuleNavigation from './ModuleNavigation.vue'
import {getRuleTemplates,getLongTermRules,saveLongTermRule,deleteLongTermRule,getRuleVersions} from '../api'
import {RULE_CATEGORIES,rulePayload,blankRule,formulaLabel} from '../ruleForms'
import {toast} from '../store'
import RuleEditor from './RuleEditor.vue'
const operators={eq:'等于',ne:'不等于',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于',like:'匹配',in:'属于'}
defineProps({sidebarTarget:{type:String,default:''}})
const templates=ref([]),filter=ref('all')
const filters=computed(()=>[
  {value:'all',label:'全部规则',icon:List,count:rules.value.length},
  {value:'active',label:'已启用',icon:CircleCheck,count:rules.value.filter(rule=>rule.active).length},
  {value:'inactive',label:'未启用',icon:CircleClose,count:rules.value.filter(rule=>!rule.active).length},
])
const visibleRules=computed(()=>rules.value.filter(r=>filter.value==='all' || r.active===(filter.value==='active')))
const rules=ref([]),loading=ref(false),acting=ref(false),error=ref(''),editorVisible=ref(false),draft=ref(null),versionsVisible=ref(false),versions=ref([])
async function load(){loading.value=true;error.value='';try{rules.value=(await getLongTermRules()).rules}catch(e){error.value=e.message}finally{loading.value=false}}
function fromTemplate(key){const item=templates.value.find(t=>t.key===key);if(item){const {key:ignored,...value}=item;draft.value={...blankRule(),...value,active:false};editorVisible.value=true}}
function create(){draft.value=blankRule();editorVisible.value=true}
function edit(rule){draft.value=rule;editorVisible.value=true}
async function saved(rule){toast('success',rule?.active?'规则已保存并启用':'规则已保存，尚未启用');await load()}
async function toggle(rule){acting.value=true;try{await saveLongTermRule({...rulePayload(rule),active:!rule.active,expected_revision:rule.revision,confirmed:true},rule.id);await load()}catch(e){toast('error',e.message)}finally{acting.value=false}}
async function remove(rule){try{await ElMessageBox.confirm(`删除“${rule.title}”及其管理版本记录？已完成任务保留当时采用的规则快照。`,'删除长期规则',{type:'warning',confirmButtonText:'删除',cancelButtonText:'取消'})}catch{return}acting.value=true;try{await deleteLongTermRule(rule.id,rule.revision);await load();toast('success','规则已删除')}catch(e){toast('error',e.message)}finally{acting.value=false}}
async function showVersions(rule){try{versions.value=(await getRuleVersions(rule.id)).versions;versionsVisible.value=true}catch(e){toast('error',e.message)}}
onMounted(async()=>{await load();try{templates.value=(await getRuleTemplates()).templates}catch(e){toast('error',e.message)}})
</script>
<style scoped>
.rules-panel{height:100%;min-height:0;display:flex;flex-direction:column;padding:16px 20px;overflow:hidden}
.rules-header{display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:32px;margin-bottom:14px;flex-shrink:0}
.rules-heading{display:flex;align-items:baseline;gap:12px;min-width:0;flex-wrap:wrap}
h1{font-size:20px;font-weight:600;margin:0}
.rules-heading span{font-size:13px;color:var(--text-secondary)}
.template-arrow{margin-left:auto}
.rule-scope{font-size:13px;line-height:1.7;padding:8px 10px;background:var(--bg);border-radius:6px}.rule-scope strong{font-weight:500;color:var(--text-secondary);margin-right:6px}.rule-list{flex:1;min-height:0;overflow:auto;padding-right:6px}.rule-card{padding:18px 0;border-bottom:1px solid var(--border)}.rule-heading{display:flex;gap:10px;align-items:center;flex-wrap:wrap}h2{font-size:16px;margin:0}.rule-content{font-size:14px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere;margin:10px 0}.muted{font-size:13px;color:var(--text-secondary);line-height:1.6;margin:6px 0}.rule-actions{display:flex;gap:14px;margin-top:10px}.rule-actions :deep(.el-button+.el-button){margin-left:0}.error{color:var(--el-color-danger)}summary{font-size:13px;cursor:pointer;color:var(--text-secondary)}.version-list{max-height:60vh;overflow:auto}.version-list article{padding:10px 0;border-bottom:1px solid var(--border)}pre{white-space:pre-wrap;font-size:12px}@media(max-width:650px){.rules-panel{padding:12px}.rules-heading{gap:4px 8px}.rules-heading span:last-child{flex-basis:100%}}
</style>
