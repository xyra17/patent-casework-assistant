<template>
  <div class="semantic-panel" :class="{expanded}">
    <div v-if="tableRequest" class="browse-context" aria-label="数据浏览上下文"><span>来自数据浏览 · {{ tableRequest.table }}</span><el-button @click="emit('return-browse')">返回数据浏览</el-button></div>
    <el-empty v-if="error" :description="error" :image-size="70" />
    <el-skeleton v-else-if="!catalog" :rows="5" animated />
    <template v-else>
      <p v-if="physicalError" class="query-error" role="status">{{ physicalError }}</p>
      <div class="semantic-view-switch" role="group" aria-label="指标与结构视图">
        <button type="button" :aria-pressed="activeView==='query'" @click="activeView='query'">指标查询</button>
        <button type="button" :aria-pressed="activeView==='structure'" @click="activeView='structure'">表结构</button>
        <button type="button" :aria-pressed="activeView==='relations'" @click="activeView='relations'">关联关系<span>{{ catalog.relationships?.length || 0 }}</span></button>
      </div>
      <div class="semantic-workspace">
      <p v-if="contextError" class="query-error" role="status">{{ contextError }}</p>
<section v-show="activeView==='query'" class="semantic-query" aria-label="指标查询">
      <header class="section-heading"><h2>指标查询</h2></header>
      <div class="query-form">
        <label class="query-field"><span v-if="expanded">统计指标</span><el-select v-model="queryMetrics" :size="expanded?'default':'small'" aria-label="统计指标" multiple collapse-tags placeholder="选择指标（可多选）">
          <el-option v-for="metric in catalog.metrics" :key="metric.key" :label="metric.label" :value="metric.key" />
        </el-select></label>
        <label class="query-field"><span v-if="expanded">分组维度（可选）</span><el-select v-model="queryDims" :size="expanded?'default':'small'" aria-label="分组维度" multiple collapse-tags placeholder="分组维度">
          <el-option v-for="dim in dimensionOptions" :key="dim.value" :label="dim.label" :value="dim.value" />
        </el-select></label>
        <el-button :size="expanded?'default':'small'" type="primary" :loading="queryLoading" :disabled="optionsLoading || !!optionsError || !queryMetrics.length" @click="runQuery">查询指标</el-button>
      </div>
      <p class="query-hint" role="status">{{ optionsLoading?'正在核对指标与维度…':optionsError || `指标来源表：${metricTables.join('、') || '未选择'}；分组关联表：${dimensionTables.join('、') || '无'}` }}</p>
      <p v-if="dimensionNotice" class="query-hint" role="status">{{ dimensionNotice }}</p>
      <details v-if="incompatibleDimensions.length" class="semantic-metrics"><summary>不可用维度及原因 · {{ incompatibleDimensions.length }}</summary><p v-for="dim in incompatibleDimensions" :key="dim.value">{{ dim.label }}：{{ dim.reason }}</p></details>
      <p v-if="queryError" class="query-error">{{ queryError }}</p>
      <div v-if="queryResult" class="query-result">
        <span class="query-meta">{{ queryResult.rowCount }} 行 · {{ (queryResult.metrics || [queryResult.metric]).map((m) => m?.label).join(' / ') }}</span>
        <el-table scrollbar-always-on class="app-resizable-table" :data="queryRows" size="small" border :max-height="expanded?360:150">
          <el-table-column v-for="column in queryResult.columns" :key="column" :prop="column" :label="queryColumnLabel(column)" min-width="90" show-overflow-tooltip />
        </el-table>
      </div>

      <p v-if="expanded && !queryResult" class="query-hint">不选分组维度时，查看整体汇总。</p>
      <details class="semantic-metrics" :open="!expanded"><summary>查看指标口径 · {{ catalog.metrics.length }} 个指标</summary>
        <div v-if="expanded" class="metric-definitions"><article v-for="metric in catalog.metrics" :key="metric.key"><strong>{{ metric.label }}</strong><code>{{ metric.expression }}</code></article></div>
        <div v-else class="metric-list">
          <el-tag v-for="metric in catalog.metrics" :key="metric.key" size="small" effect="plain" :title="metric.expression">
            {{ metric.label }}
          </el-tag>
        </div>
      </details>
      <details class="chart-capabilities"><summary>可用图表与数据要求</summary>
      <div class="capability-list">
        <span
          v-for="cap in catalog.capabilities"
          :key="cap.type"
          class="capability"
          :class="{ available: cap.available }"
          :title="cap.reason"
        >
          <strong>{{ cap.label }}</strong><span>{{ cap.available ? '可用' : '暂不可用' }}</span><small>{{ cap.reason }}</small>
        </span>
      </div>
      </details></section>
      <section v-show="activeView==='structure'" class="structure-workspace" aria-label="数据表结构">
        <nav class="table-picker" aria-label="选择结构数据表"><h2>数据表 <span>{{ Object.keys(catalog.tables || {}).length }}</span></h2><div class="table-picker-list"><button v-for="(table,name) in catalog.tables" :key="name" type="button" :aria-pressed="selectedTable===name" :title="`${table.label || name} · ${name}`" @click="selectedTable=name"><strong>{{ tablePresentation(name,table).label }}</strong><code>{{ name }}</code></button></div></nav>
        <div v-if="currentTable" class="table-detail">
          <header class="section-heading table-heading">
            <h2>{{ tablePresentation(selectedTable,currentTable).label }}</h2>
            <span v-if="tablePresentation(selectedTable,currentTable).note" class="access-note">{{ tablePresentation(selectedTable,currentTable).note }}</span>
            <span class="field-count">{{ Object.keys(currentTable.columns || {}).length }} 个字段</span>
            <el-button :disabled="currentTable.exists!==true" @click="emit('browse-table',selectedTable)">查看数据</el-button>
          </header>
          <p v-if="currentTable.exists===false" class="query-error" role="status">实际数据库未发现该表，语义目录仍保留其定义。</p>
          <div class="structure-options">
            <details class="table-notes"><summary>表说明</summary><code>{{ selectedTable }}</code><p v-if="currentTable.description">{{ currentTable.description }}</p><p>字段类型来自数据库，业务含义来自配置；未配置的含义需另行核对。</p></details>
            <el-checkbox v-model="showTechnical">显示技术详情</el-checkbox>
          </div>
          <div ref="fieldsScroll" class="fields-scroll">
            <table :key="showTechnical ? 'technical' : 'basic'" v-resizable-columns class="fields-table" :class="{'with-technical':showTechnical}">
              <thead><tr><th>字段</th><th>含义</th><th>类型</th><template v-if="showTechnical"><th>约束</th><th>默认值</th><th>配置</th></template></tr></thead>
              <tbody><tr v-for="(column,name) in currentTable.columns" :key="name" :data-field="name" tabindex="-1" :class="{'highlighted-field':highlightedField===name}">
                <td><code>{{ name }}</code></td>
                <td><template v-if="column.label && column.label!==name">{{ column.label }}<span v-if="column.unit">（{{ column.unit }}）</span></template><span v-else class="muted">未配置</span><small v-if="showTechnical">{{ roleLabel(column.role) }}{{ column.aggregation?' · '+column.aggregation:'' }}</small><small v-if="!showTechnical && column.exists===false" class="query-error">字段不存在</small></td>
                <td>{{ column.physical?.type || '未核实' }}</td>
                <template v-if="showTechnical"><td>{{ column.physical ? `${column.physical.key==='PRI'?'主键 · ':''}${column.physical.nullable?'可为空':'不可为空'}`:'未核实' }}</td><td><code>{{ physicalDefault(column) }}</code><small v-if="column.physical?.extra">{{ column.physical.extra }}</small></td><td><span :class="{'query-error':column.exists===false}">{{ column.exists===false?'字段不存在':column.configured?'已配置':column.exists===null?'未核实':'未配置含义' }}</span></td></template>
              </tr></tbody>
            </table>
          </div>
        </div><el-empty v-else description="暂无表结构" :image-size="60"/>
      </section>
      <section v-show="activeView==='relations'" class="relationships" aria-label="表间关联关系"><header class="section-heading"><h2>表间关联关系</h2><span>{{ catalog.relationships?.length || 0 }} 条</span></header><table v-resizable-columns v-if="catalog.relationships?.length" class="relations-table"><thead><tr><th>来源字段</th><th>关联字段</th></tr></thead><tbody><tr v-for="relation in catalog.relationships" :key="relation.from+relation.to"><td><button class="relation-link" @click="locateRelation(relation.from)">{{ relation.from }}</button></td><td><button class="relation-link" @click="locateRelation(relation.to)">{{ relation.to }}</button></td></tr></tbody></table><el-empty v-else description="暂无关联关系" :image-size="60"/></section>
      </div>
    </template>
  </div>
</template>
<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { getSemantic, semanticQuery, getBusinessDatabase, getMetricOptions } from '../api'

import {readWorkspace,saveWorkspace,mergeStructure} from '../businessWorkspace'
import { tablePresentation } from '../schemaPresentation'
const saved=readWorkspace('semantic')
const props=defineProps({expanded: Boolean,tableRequest:Object})
const emit=defineEmits(['return-browse','browse-table'])
function roleLabel(role){return {id:"标识",foreign_key:"关联字段",dimension:"分组维度",attribute:"属性",text:"文本",time:"时间",measure:"数值指标",metric:"数值指标",geo:"地理位置",hierarchy:"层级",stage:"阶段",source:"来源",target:"目标"}[role || "dimension"] || role}

const catalog = ref(null)
const error = ref('')
const activeView=ref(['structure','query','relations'].includes(saved?.view)?saved.view:'structure')
const selectedTable=ref(typeof saved?.table==='string'?saved.table:'')
const showTechnical=ref(false)
const physicalError=ref(''),fieldsScroll=ref(null),highlightedField=ref('')
function physicalDefault(column){const p=column.physical;if(!p)return '未核实';if(p.default!==null && p.default!==undefined)return p.default===''?"''（空字符串）":String(p.default);return p.extra?.includes('auto_increment')?'自动编号':p.nullable?'NULL':'无默认值'}
async function locateRelation(value){const [table,field]=value.split('.');activeView.value='structure';selectedTable.value=table;highlightedField.value=field;contextError.value='';await nextTick();const row=[...(fieldsScroll.value?.querySelectorAll('[data-field]') || [])].find(el=>el.dataset.field===field);if(row){row.scrollIntoView({block:'nearest',inline:'nearest'});row.focus({preventScroll:true})}else contextError.value=`未找到关联字段 ${value}，请核对语义配置。`}
const contextError=ref('')
function applyTableRequest(){
  if(!props.tableRequest)return
  activeView.value='structure';highlightedField.value=''
  if(!catalog.value)return
  const name=props.tableRequest.table
  selectedTable.value=Object.hasOwn(catalog.value.tables || {},name)?name:''
  contextError.value=selectedTable.value?'':`当前表 ${name} 尚未配置语义结构，可返回数据浏览查看原始记录。`
}
watch(()=>props.tableRequest,applyTableRequest)
const currentTable=computed(()=>catalog.value?.tables?.[selectedTable.value])
const queryMetrics = ref(Array.isArray(saved?.metrics)?saved.metrics.filter(v=>typeof v==='string').slice(0,20):[])
const queryDims = ref(Array.isArray(saved?.dims)?saved.dims.filter(v=>typeof v==='string').slice(0,20):[])
const queryLoading = ref(false)
const queryError = ref('')
const queryResult = ref(null)

const optionsLoading=ref(false),optionsError=ref(''),dimensionNotice=ref(''),metricTables=ref([]),allDimensions=ref([])
const dimensionOptions=computed(()=>allDimensions.value.filter(d=>d.compatible))
const incompatibleDimensions=computed(()=>allDimensions.value.filter(d=>!d.compatible))
const dimensionTables=computed(()=>[...new Set(queryDims.value.map(d=>d.split('.')[0]))])
let optionsVersion=0
async function refreshOptions(){
  if(!catalog.value)return
  const version=++optionsVersion;optionsLoading.value=true;optionsError.value='';allDimensions.value=[]
  try{const result=await getMetricOptions(queryMetrics.value);if(version!==optionsVersion)return;allDimensions.value=result.dimensions;metricTables.value=result.tables;optionsError.value=result.error;const allowed=new Set(result.dimensions.filter(d=>d.compatible).map(d=>d.value));const removed=queryDims.value.filter(d=>!allowed.has(d));queryDims.value=queryDims.value.filter(d=>allowed.has(d));dimensionNotice.value=removed.length?'已移除不兼容的分组维度：'+removed.join('、'):''}
  catch(e){if(version===optionsVersion){optionsError.value=e.message;metricTables.value=[]}}
  finally{if(version===optionsVersion)optionsLoading.value=false}
}
watch(queryMetrics,refreshOptions,{deep:true})
watch([activeView,selectedTable,queryMetrics,queryDims],()=>{if(catalog.value)saveWorkspace('semantic',{view:activeView.value,table:selectedTable.value,metrics:queryMetrics.value,dims:queryDims.value})},{deep:true})
watch([queryMetrics,queryDims],()=>{queryResult.value=null;queryError.value=''},{deep:true})
const queryRows = computed(() =>
  (queryResult.value?.rows || []).map((row) =>
    Object.fromEntries((queryResult.value.columns || []).map((column, index) => [column, row[index]]))
  )
)

function queryColumnLabel(column) {
  return queryResult.value?.semantic?.[column]?.label || column
}

async function runQuery() {
  if(optionsLoading.value || optionsError.value)return
  if (!queryMetrics.value.length) {
    queryError.value = '请先选择指标'
    return
  }
  queryLoading.value = true
  queryError.value = ''
  const queryKey=JSON.stringify([queryMetrics.value,queryDims.value])
  try {
    const result = await semanticQuery({
      metrics: queryMetrics.value,
      dimensions: queryDims.value,
      filters: {},
      limit: 100,
    })
    if(queryKey===JSON.stringify([queryMetrics.value,queryDims.value]))queryResult.value=result
  } catch (e) {
    if(queryKey===JSON.stringify([queryMetrics.value,queryDims.value])){queryError.value = e.message;queryResult.value = null}
  } finally {
    queryLoading.value = false
  }
}

async function load() {
  const [sem,physical]=await Promise.allSettled([getSemantic(),getBusinessDatabase()])
  if(sem.status==='rejected'){error.value=`语义层加载失败：${sem.reason.message}`;return}
  if(physical.status==='rejected')physicalError.value='实际数据库结构读取失败，以下仅展示语义配置，类型及默认值尚未核实。'
  catalog.value={...sem.value,tables:mergeStructure(sem.value.tables,physical.status==='fulfilled'?physical.value:null)}
  if(!Object.hasOwn(catalog.value.tables,selectedTable.value))selectedTable.value=Object.keys(catalog.value.tables)[0] || ''
  applyTableRequest()
  const validMetrics=new Set(catalog.value.metrics.map(m=>m.key))
  queryMetrics.value=queryMetrics.value.filter(m=>validMetrics.has(m))
  if(!Array.isArray(saved?.metrics) && !queryMetrics.value.length && catalog.value.metrics[0])queryMetrics.value=[catalog.value.metrics[0].key]
}

onMounted(load)
</script>

<style scoped>
.semantic-panel{height:100%;min-height:0;display:flex;flex-direction:column;overflow:hidden;padding:4px 4px 16px 0;font-size:14px;color:var(--text)}
.browse-context{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;flex-shrink:0;margin-bottom:14px;color:var(--text-secondary);font-size:14px}.browse-context span{overflow-wrap:anywhere}
.semantic-workspace{flex:1;min-height:0;overflow:auto}
.semantic-query{min-width:0;padding:8px 4px 12px 0}
.section-heading{display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap}
.section-heading h2{font-size:17px;font-weight:600;margin:0;line-height:1.5;color:var(--text)}
.section-heading>span{font-size:13px;color:var(--text-secondary)}
.section-heading .section-count{background:var(--bg);border:1px solid var(--border);padding:2px 8px;border-radius:5px}
.query-form{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:12px;align-items:end}
.query-field{display:flex;flex-direction:column;gap:8px;min-width:0}.query-field>span{color:var(--text);font-size:14px}.query-form :deep(.el-select){width:100%;min-width:0}
.query-form>.el-button{margin:0}.query-hint{font-size:13px;color:var(--text-secondary);line-height:1.6;margin:10px 0 0}
.query-error{margin:10px 0;color:var(--danger-text)}.query-result{margin-top:18px}.query-meta{display:block;font-size:13px;color:var(--text-secondary);margin-bottom:8px}.query-result :deep(.el-table){font-size:14px}
.semantic-metrics{margin-top:12px;font-size:13px;color:var(--text-secondary)}summary{cursor:pointer;width:fit-content;line-height:1.7}summary:hover{color:var(--primary)}summary:focus-visible{outline:2px solid var(--primary);outline-offset:3px}
.metric-definitions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 24px;margin-top:6px}.metric-definitions article{display:flex;flex-direction:column;gap:6px;padding:12px 0;border-bottom:1px solid var(--border)}.metric-definitions strong{color:var(--text);font-size:14px;font-weight:500}.metric-definitions code{font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.metric-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.fields-scroll{overflow:auto}
.fields-table{width:100%;min-width:440px;table-layout:fixed;border-collapse:collapse;text-align:left;font-size:13px}
.fields-table th,.fields-table td{padding:12px;border-bottom:1px solid var(--border);overflow-wrap:anywhere;vertical-align:top}
.fields-table th{font-weight:500;background:var(--bg);color:var(--text-secondary)}
.fields-table th:nth-child(1){width:38%}.fields-table th:nth-child(2){width:40%}.fields-table th:nth-child(3){width:22%}
.fields-table code{font-size:12px;color:var(--text);overflow-wrap:anywhere}.fields-table small{display:block;font-size:12px;color:var(--text-secondary);margin-top:5px}
.fields-table.with-technical{min-width:800px}.fields-table.with-technical th:nth-child(1){width:20%}.fields-table.with-technical th:nth-child(2){width:22%}.fields-table.with-technical th:nth-child(3){width:14%}.fields-table.with-technical th:nth-child(4){width:16%}.fields-table.with-technical th:nth-child(5){width:14%}.fields-table.with-technical th:nth-child(6){width:14%}
.fields-table .highlighted-field{background:var(--primary-soft);outline:2px solid var(--primary);outline-offset:-2px}
.fields-table .query-error{color:var(--danger-text)}
.relation-link{border:0;background:transparent;padding:3px;color:var(--primary);font:inherit;font-family:monospace;cursor:pointer;text-align:left;overflow-wrap:anywhere}.relation-link:hover{text-decoration:underline}.relation-link:focus-visible{outline:2px solid var(--primary);outline-offset:3px}
.structure-options{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin:12px 0}
.structure-options .el-checkbox{height:24px;flex-shrink:0}.structure-options :deep(.el-checkbox__label){font-size:12px;color:var(--text-secondary)}
.table-notes{min-width:0;color:var(--text-secondary);font-size:12px;line-height:1.7}.table-notes summary{line-height:24px}.table-notes p{margin:8px 0}.table-notes code{display:block;margin-top:8px;overflow-wrap:anywhere}
.relationships{padding:8px 4px 12px 0}.relationships h3{font-size:15px;font-weight:600;margin:0 0 10px}.relation-list{display:flex;flex-direction:column;gap:8px}.relation{font-family:ui-monospace,SFMono-Regular,monospace;font-size:13px;line-height:1.7;color:var(--text-secondary);overflow-wrap:anywhere}.muted{color:var(--text-secondary)}
.chart-capabilities{border-top:1px solid var(--border);margin-top:20px;padding-top:14px;color:var(--text-secondary);font-size:13px}.capability-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 24px;margin-top:12px}.capability{display:grid;grid-template-columns:1fr auto;gap:6px;padding:10px 0;border-bottom:1px solid var(--border);font-size:13px}.capability strong{font-weight:500;color:var(--text)}.capability small{grid-column:1/-1;line-height:1.6;font-size:13px}.capability.available>span{color:var(--success-text)}
.semantic-source{font-size:12px;color:var(--text-secondary);margin-top:12px}.semantic-panel:not(.expanded){font-size:13px}.semantic-panel:not(.expanded) .query-form{grid-template-columns:1fr}.semantic-panel:not(.expanded) .section-heading>span{display:none}
@media(max-width:650px){.section-heading{margin-bottom:12px}.query-form{grid-template-columns:1fr}.query-form>.el-button{justify-self:start}.metric-definitions,.capability-list{grid-template-columns:1fr}.table-collapse :deep(.el-collapse-item__header){gap:8px}.semantic-workspace{gap:20px}}

.semantic-view-switch{display:flex;gap:4px;width:fit-content;padding:4px;background:var(--bg);border:1px solid var(--border);border-radius:8px;flex-shrink:0;margin:0 0 20px}
.semantic-view-switch button{display:flex;align-items:center;gap:7px;border:0;border-radius:5px;background:transparent;color:var(--text-secondary);font:inherit;padding:8px 14px;cursor:pointer;white-space:nowrap}
.semantic-view-switch button[aria-pressed="true"]{color:var(--primary);background:var(--card-bg);box-shadow:0 1px 3px #0000000d;font-weight:600}
.semantic-view-switch button>span{font-size:12px;opacity:.8}.semantic-view-switch button:focus-visible,.table-picker button:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
.structure-workspace{display:grid;grid-template-columns:200px minmax(0,1fr);gap:20px;min-height:100%}
.table-picker{padding-right:20px;border-right:1px solid var(--border)}.table-picker h2{font-size:14px;font-weight:500;margin:4px 0 12px;color:var(--text-secondary)}
.table-picker button{display:flex;flex-direction:column;gap:6px;width:100%;text-align:left;border:1px solid transparent;border-radius:6px;padding:12px;background:transparent;color:var(--text);cursor:pointer;margin:0 0 6px;font:inherit}
.table-picker button[aria-pressed="true"]{background:var(--primary-soft);border-color:var(--primary-soft);color:var(--primary)}.table-picker button:hover{background:var(--bg)}.table-picker strong{font-weight:500;font-size:14px}.table-picker code{font-size:12px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}.table-picker h2>span{font-size:12px;margin-left:6px}
.table-detail{min-width:0;padding-top:4px}.table-detail .table-heading{gap:10px;margin-bottom:6px}.table-heading .field-count{margin-left:auto;white-space:nowrap}.table-heading .access-note{padding:1px 6px;border:1px solid var(--border);border-radius:4px;font-size:12px}
.relations-table{width:100%;border-collapse:collapse;font-size:14px}.relations-table th,.relations-table td{padding:14px 12px;text-align:left;border-bottom:1px solid var(--border);overflow-wrap:anywhere}.relations-table th{font-weight:500;color:var(--text-secondary);background:var(--bg)}.relations-table code{font-size:13px}
@media(max-width:850px){.structure-workspace{grid-template-columns:160px minmax(0,1fr);gap:16px}.table-picker{padding-right:12px}}
@media(max-width:650px){.semantic-view-switch{width:100%;gap:2px}.semantic-view-switch button{flex:1;justify-content:center;padding:8px 5px;font-size:13px}.structure-workspace{display:flex;flex-direction:column;gap:16px}.table-picker{padding:0 0 12px;border-right:0;border-bottom:1px solid var(--border)}.table-picker h2{display:none}.table-picker-list{display:flex;gap:8px;overflow:auto}.table-picker button{min-width:132px;width:auto;margin:0;padding:10px;flex-shrink:0}.relations-table{table-layout:fixed}.relations-table th,.relations-table td{padding:12px 8px}}
</style>
