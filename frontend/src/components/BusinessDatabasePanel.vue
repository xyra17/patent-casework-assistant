<template>
  <section class="business-database glass-card" aria-label="业务数据库">
    <header class="database-header"><h1>业务数据库</h1><span aria-hidden="true">/</span><span class="current-view">{{ currentTitle }}</span></header>
    <div class="database-views">
      <div v-if="visited.browse" v-show="activeTab==='browse'" class="database-view" aria-label="业务记录"><BusinessTableBrowser :key="recordTable" :table-request="browseRequest" :fixed-table="recordTable==='other'?'':recordTable" :exclude-tables="recordTable==='other'?recordOptions.map(r=>r.table).concat('cw_sync_state'):[]" @changed="auditRefresh++" @structure="openStructure" @import="openImport" :refresh-key="browseRefresh"/></div>
      <div v-if="visited.import" v-show="activeTab==='import'" class="database-view" aria-label="数据导入"><DataImportPanel ref="importPanel" @committed="browseRefresh++"/></div>
      <div v-if="visited.audit" v-show="activeTab==='audit'" class="database-view" aria-label="修改记录"><BusinessAuditPanel :refresh-key="auditRefresh"/></div>
    </div>
    <el-drawer v-model="settingsVisible" title="数据库设置" size="min(1100px,96vw)" class="database-settings">
      <el-tabs v-model="settingsTab" class="database-tabs"><el-tab-pane label="指标与结构" name="semantic" lazy><SemanticPanel expanded :table-request="structureRequest" @return-browse="settingsVisible=false" @browse-table="openBrowse"/></el-tab-pane><el-tab-pane label="SQL 控制台" name="sql" lazy><BusinessSQLConsole/></el-tab-pane></el-tabs>
    </el-drawer>
  </section>
</template>
<script setup>
import {computed,nextTick,reactive,ref,watch} from 'vue'
import {BUSINESS_RECORDS} from '../businessRecords'
import BusinessSQLConsole from './BusinessSQLConsole.vue'
import BusinessAuditPanel from './BusinessAuditPanel.vue'
import BusinessTableBrowser from './BusinessTableBrowser.vue'
import SemanticPanel from './SemanticPanel.vue'
import DataImportPanel from './DataImportPanel.vue'
const recordOptions=BUSINESS_RECORDS
const recordTable=defineModel('recordTable',{type:String,required:true})
const activeTab=defineModel('activeTab',{type:String,required:true})
const settingsVisible=defineModel('settingsVisible',{type:Boolean,required:true})
const auditRefresh=ref(0),browseRefresh=ref(0),importPanel=ref(null),settingsTab=ref('semantic')
const visited=reactive({browse:false,import:false,audit:false})
watch(activeTab,value=>{visited[value]=true},{immediate:true})
const currentTitle=computed(()=>activeTab.value==='import'?'数据导入':activeTab.value==='audit'?'修改记录':BUSINESS_RECORDS.find(item=>item.table===recordTable.value)?.label || '其他数据')
const browseRequest=ref(null),structureRequest=ref(null)
watch(recordTable,()=>{browseRequest.value=null},{flush:'sync'})
function openBrowse(table){recordTable.value=BUSINESS_RECORDS.some(r=>r.table===table)?table:'other';browseRequest.value={table};activeTab.value='browse';settingsVisible.value=false}
function openStructure(table){structureRequest.value={table};settingsTab.value='semantic';settingsVisible.value=true}
async function openImport(table){activeTab.value='import';await nextTick();await importPanel.value?.openForTable(table)}
</script>
<style scoped>
.business-database { height:100%; min-height:0; display:flex; flex-direction:column; padding:16px 20px; overflow:hidden; }
.database-header { display:flex; align-items:center; gap:12px; min-height:32px; margin-bottom:14px; flex-shrink:0; color:var(--text-secondary); }
.database-header h1 { font-size:20px; font-weight:600; margin:0; color:var(--text); }
.current-view { font-size:14px; }
.database-views { flex:1; min-height:0; overflow:hidden; }
.database-view { height:100%; min-height:0; overflow:hidden; }
.database-view :deep(.import-panel) { padding:0; border:0; box-shadow:none; background:transparent; height:100%; overflow:hidden; }
.database-tabs { flex:1; min-height:0; display:flex; flex-direction:column; }
.database-tabs :deep(.el-tabs__content) { flex:1; min-height:0; overflow:hidden; }
.database-tabs :deep(.el-tab-pane) { height:100%; min-height:0; overflow:hidden; }
.database-tabs :deep(.el-tabs__header) { flex-shrink:0; margin-bottom:12px; }
.database-tabs :deep(.semantic-panel) { height:100%; }
@media(max-width:650px) {
  .business-database { padding:12px; }
  .database-header { gap:8px; margin-bottom:10px; }
}
</style>
<style>.database-settings .el-drawer__body{display:flex;flex-direction:column;overflow:hidden}.database-settings .semantic-panel{height:100%;overflow:auto}</style>
