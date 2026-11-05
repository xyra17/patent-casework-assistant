<template>
  <div class="evidence-panel">
    <el-empty v-if="!items.length" description="本次任务暂无来源记录" :image-size="70" />
    <article v-for="item in items" :id="`evidence-${item.id}`" :key="item.id" class="evidence-card" :class="{selected:item.id===selectedId}">
      <header class="evidence-head">
        <span class="evidence-id">{{ item.id }}</span>
        <el-tag size="small" effect="plain" :type="tagType(item.type)">{{ typeLabel(item.type) }}</el-tag>
        <strong>{{ evidenceTitle(item) || '未命名来源' }}</strong>
        <span v-if="item.rowCount != null" class="evidence-count">{{ item.rowCount }} 行</span>
        <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer" class="evidence-link">打开来源</a>
      </header>
      <el-button v-if="item.casework && item.file_id" link type="primary" @click="caseSource.open(item)">查看案卷原文与版本</el-button>
      <p v-if="item.location" class="evidence-location">{{ item.location }}<span v-if="item.revision"> · 文档版本 {{ item.revision.slice(0, 8) }}</span></p>
      <details v-if="item.applicability" class="evidence-location">
        <summary>版本适用核对 · {{ item.applicability.verified ? '信息已确认' : '待核实' }} · {{ {current:'现行查询',history:'历史查询',compare:'版本对比'}[item.applicability.purpose] || '旧记录' }}</summary>
        <p>制度：{{ item.applicability.version?.document_id || '未登记' }} · 版本：{{ item.applicability.version?.version_label || '未命名' }}</p>
        <p>适用日期：{{ item.applicability.as_of || '未记录' }} · 生效：{{ item.applicability.version?.effective_from || '未知' }} · 失效：{{ item.applicability.version?.effective_until || '未登记' }}</p>
        <p>组织：{{ item.applicability.scope?.organization || '未指定' }} · 地区：{{ item.applicability.scope?.region || '未指定' }} · 业务：{{ item.applicability.scope?.business || '未指定' }}</p>
        <p v-for="warning in item.applicability.warnings || []" :key="warning">待核实：{{ warning }}</p>
        <p v-for="(rel,index) in item.applicability.relations || []" :key="index">关系：{{ rel.source }} → {{ rel.target }}（{{ {replaces:'替代',amends:'局部修订',supplements:'补充',references:'引用'}[rel.kind] }}）；依据：{{ rel.basis }}；条款：{{ rel.source_clause }} → {{ rel.target_clause }}</p>
      </details>
      <p v-if="evidenceScopeText(item)" class="evidence-location">{{ evidenceScopeText(item) }}</p>
      <p v-if="item.snippet" class="evidence-snippet">{{ item.snippet }}</p>
      <pre v-if="item.sql" class="evidence-sql">{{ item.sql }}</pre>
      <p v-if="item.params?.length" class="evidence-location">查询参数：{{ JSON.stringify(item.params) }}</p>
      <p v-if="item.rule_versions?.length" class="evidence-location">已执行长期规则：{{ item.rule_versions.map(r => `${r.id.slice(0,8)} v${r.revision}`).join('、') }}</p>
      <details v-if="item.effective_bindings?.length"><summary>实际执行口径</summary><p v-for="entry in item.effective_bindings" :key="entry.metric_key" class="evidence-location">{{ entry.metric_key }}：{{ formulaLabel(entry.binding.formula) }}；时间：{{ entry.binding.time_field || '沿用语义层' }}；过滤：{{ entry.binding.filters.map(f=>`${f.field} ${f.op} ${JSON.stringify(f.value)}`).join('；') || '无' }}</p></details>
      <div v-if="item.columns?.length && item.rows?.length" class="evidence-table-wrap">
        <table v-resizable-columns class="evidence-table">
          <thead><tr><th v-for="(col, colIndex) in item.columns" :key="colIndex">{{ caseFieldLabel(col) }}</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in (expanded[item.id] ? item.rows : item.rows.slice(0, 20))" :key="index">
              <td v-for="(col, colIndex) in item.columns" :key="colIndex">{{ row[colIndex] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <el-button v-if="item.rows?.length > 20" link type="primary" @click="expanded[item.id] = !expanded[item.id]">{{ expanded[item.id] ? '收起到前 20 行' : `展开已保存的 ${item.rows.length} 行` }}</el-button>
      <span v-if="item.chart" class="chart-note">该来源包含图表数据，可在「图表」页查看</span>
    </article>
    <CaseDocumentDrawer ref="caseSource"/>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { evidenceTitle, caseFieldLabel } from '../sourcePresentation.js'
import { evidenceScopeText } from '../evidenceScope.js'
import {formulaLabel} from '../ruleForms'
import CaseDocumentDrawer from './CaseDocumentDrawer.vue'
const caseSource=ref(null)
const expanded = reactive({})
defineProps({ items: { type: Array, default: () => [] }, selectedId: {type:String,default:''} })

function typeLabel(type) {
  return { database: '数据库', web: '网页', knowledge: '知识库', file: '文件' }[type] || '来源'
}
function tagType(type) {
  return { database: 'success', web: 'primary', knowledge: 'warning', file: 'info' }[type] || 'info'
}
</script>

<style scoped>
.evidence-location { font-size: 12px; color: var(--text-secondary); margin: 8px 0; line-height: 1.6; }
.evidence-panel {
  height: 100%;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 2px 10px;
}
.evidence-card {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg);
  padding: 10px 12px;
}
.evidence-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  font-size: 12px;
}
.evidence-id {
  font-size: 12px;
  font-weight: 700;
  color: var(--primary);
  background: var(--primary-soft);
  padding: 1px 5px;
  border-radius: 4px;
}
.evidence-count {
  font-size: 12px;
  color: var(--text-secondary);
}
.evidence-link {
  margin-left: auto;
  font-size: 13px;
  color: var(--primary);
}
.evidence-snippet {
  margin-top: 7px;
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.65;
  color: var(--text-secondary);
  max-height: 180px;
  overflow: auto;
}
.evidence-sql {
  margin-top: 7px;
  padding: 7px 9px;
  border-radius: 6px;
  background: var(--card-bg);
  font-size: 12px;
  line-height: 1.5;
  overflow: auto;
  color: var(--text-secondary);
}
.evidence-table-wrap {
  margin-top: 7px;
  overflow: auto;
  max-height: 220px;
  border-radius: 6px;
}
.evidence-table {
  border-collapse: collapse;
  font-size: 12px;
  white-space: nowrap;
}
.evidence-table th,
.evidence-table td {
  border: 1px solid var(--border);
  padding: 4px 7px;
  text-align: left;
}
.evidence-table th {
  background: var(--card-bg);
  color: var(--text);
  position: sticky;
  top: 0;
}
.evidence-table td {
  color: var(--text-secondary);
}
.chart-note {
  display: inline-block;
  margin-top: 6px;
  font-size: 12px;
  color: var(--primary);
}
</style>

<style scoped>.evidence-card{flex-shrink:0;min-width:0;overflow-wrap:anywhere}.evidence-card.selected{border-color:var(--primary);box-shadow:inset 3px 0 var(--primary)}.evidence-panel{min-height:0;overscroll-behavior:contain}.evidence-head{font-size:13px}.evidence-snippet{font-size:14px;max-height:320px}</style>
