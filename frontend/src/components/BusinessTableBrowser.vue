<template>
  <div class="table-browser" aria-label="业务数据浏览">
    <div class="browse-toolbar"><label v-if="!fixedTable">业务表<el-select v-model="table" :disabled="catalogLoading" aria-label="选择业务表" @change="changeTable"><el-option v-for="t in tables" :key="t.name" :value="t.name" :label="showTechnical ? `${t.label} · ${t.name}` : t.label"/></el-select></label><strong v-else>{{ recordLabel(table,currentTable?.label) }}</strong><el-checkbox v-if="table.startsWith('cw_')" v-model="showTechnical">显示技术详情</el-checkbox><span class="browse-access">{{ table.startsWith('cw_') ? '共享盘同步' : capabilities.update ? '可维护' : '只读' }}</span><div class="browse-shortcuts"><el-popover v-if="restoredNotice" trigger="click" placement="bottom" :width="300"><p class="restore-detail">{{ restoredNotice }}</p><template #reference><el-button text class="restore-summary">{{ restoredNotice.includes('不存在') || restoredNotice.includes('失效') ? '浏览状态已调整' : '已恢复上次浏览' }}</el-button></template></el-popover><el-button v-if="capabilities.create" type="primary" :disabled="!table || loading" @click="openCreate">新增记录</el-button><el-button v-if="!table.startsWith('cw_')" :disabled="!table" @click="emit('import',table)">批量导入</el-button><el-button :disabled="!table || catalogLoading" @click="emit('structure',table)">表结构</el-button><el-button :loading="catalogLoading" @click="loadCatalog">刷新</el-button></div></div>
    <form class="browse-filters" @submit.prevent="search">
      <el-select v-model="field" clearable placeholder="选择筛选字段" aria-label="筛选字段"><el-option v-for="c in filterColumns" :key="c.name" :value="c.name" :label="showTechnical || !table.startsWith('cw_') ? `${columnLabel(c.name)} · ${c.name}` : columnLabel(c.name)"/></el-select>
      <el-select v-model="op" aria-label="筛选方式"><el-option v-for="(label,key) in operators" :key="key" :value="key" :label="label"/></el-select>
      <el-input v-model="value" clearable aria-label="筛选值" placeholder="输入筛选值"/>
      <el-button native-type="submit" type="primary" :disabled="!table" :loading="loading">查询</el-button><el-button :disabled="!table" @click="resetFilter">重置</el-button>
    </form>
    <div v-if="capabilities.update && selected.length" class="batch-actions" role="status"><span>已选 {{ selected.length }} 条（当前页）</span><el-button :disabled="!selected.length || loading" @click="openAdvanced('bulk_update')">批量编辑</el-button><el-button type="danger" plain :disabled="!selected.length || loading" @click="openAdvanced('bulk_delete')">批量删除</el-button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <el-empty v-if="!tables.length && !catalogLoading && !error" description="当前连接下没有可浏览的业务表" :image-size="70"/>
    <el-table ref="tableView" v-else v-loading="loading || catalogLoading" :data="rowObjects" @selection-change="selected=$event" border scrollbar-always-on class="app-resizable-table business-table" height="100%" >
      <template #empty><div v-if="!loading && !catalogLoading" class="table-empty" role="status"><strong>{{ error ? '暂时无法加载记录' : result.page > 1 ? '当前页暂无记录' : appliedFilters.length ? '没有符合筛选条件的记录' : '暂无记录' }}</strong><p v-if="!error && result.page <= 1 && !appliedFilters.length">{{ capabilities.create ? '可新增记录或批量导入数据' : '当前表尚无数据' }}</p><el-button v-if="!error && result.page > 1" link type="primary" @click="loadRows(1)">返回第一页</el-button><el-button v-else-if="!error && appliedFilters.length" link type="primary" @click="resetFilter">清空筛选</el-button><el-button v-else-if="error" link type="primary" @click="loadCatalog">重新加载</el-button></div></template>
      <el-table-column v-if="capabilities.update" type="selection" width="45" :resizable="false" label-class-name="no-column-resize" :selectable="()=>!!currentTable?.primary_key?.length"/>
      <el-table-column v-for="column in displayedColumns" :key="column" :prop="column" :label="columnLabel(column)" :width="(table==='cw_tags' && !showTechnical) || column===stretchColumn ? undefined : columnWidths[column]" :min-width="columnWidths[column]" :formatter="(row)=>recordValue(table,column,row[column],showTechnical)" show-overflow-tooltip/>
      <el-table-column v-if="capabilities.update || capabilities.delete" column-key="row-actions" :width="actionsCollapsed ? 88 : 200" fixed="right" class-name="row-actions-column">
        <template #header><button type="button" class="action-column-toggle" :aria-label="actionsCollapsed ? '展开操作列' : '向右收起操作列'" :aria-expanded="!actionsCollapsed" :title="actionsCollapsed ? '展开编辑、改主键和删除按钮' : '向右收起，为业务字段留出空间'" @click="actionsCollapsed=!actionsCollapsed"><span>操作</span><el-icon><ArrowLeft v-if="actionsCollapsed"/><ArrowRight v-else/></el-icon></button></template>
        <template #default="scope">
          <el-dropdown v-if="actionsCollapsed" trigger="click" placement="bottom-end" @command="rowAction($event,scope.$index)">
            <el-button link type="primary" :disabled="!currentTable?.primary_key?.length" :aria-label="`第 ${scope.$index+1} 行更多操作`">更多<el-icon class="more-arrow"><ArrowDown/></el-icon></el-button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item v-if="capabilities.update" command="edit">编辑记录</el-dropdown-item><el-dropdown-item v-if="capabilities.update" command="rename_key">修改主键</el-dropdown-item><el-dropdown-item v-if="capabilities.delete" command="delete" :divided="!!capabilities.update" ><span class="delete-row-menu">删除记录</span></el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
          <div v-else class="row-action-buttons"><el-button v-if="capabilities.update" link type="primary" :disabled="!currentTable?.primary_key?.length" @click="openEditor(scope.$index,'edit')">编辑</el-button><el-button v-if="capabilities.update" link type="primary" :disabled="!currentTable?.primary_key?.length" @click="openAdvanced('rename_key',scope.$index)">改主键</el-button><el-button v-if="capabilities.delete" link type="danger" :disabled="!currentTable?.primary_key?.length" @click="openEditor(scope.$index,'delete')">删除</el-button></div>
        </template>
      </el-table-column>
    </el-table>
    <div class="browse-pagination"><span>第 {{ result.page || 1 }} 页 · 本页 {{ result.rows?.length || 0 }} 条</span><el-select v-model="pageSize" aria-label="每页记录数" @change="search"><el-option v-for="size in [20,50,100]" :key="size" :label="`每页 ${size} 条`" :value="size"/></el-select><el-button :disabled="loading || !table || (result.page || 1)<=1" @click="loadRows((result.page || 1)-1)">上一页</el-button><el-button :disabled="loading || !result.has_more" @click="loadRows((result.page || 1)+1)">下一页</el-button></div>
    <details class="scope-help"><summary>浏览与维护说明</summary><p class="scope-note">{{ result.has_primary_key === false ? '该表没有主键，分页顺序可能随数据变化。' : '按主键顺序浏览；数据库内容变化时，前后两页可能出现变化。' }}{{ table.startsWith('cw_') ? '这些记录来自共享盘；原件或案件工作台核对后同步更新。' : capabilities.update ? '本地维护已启用，修改和删除均记录审计。' : '维护连接未配置，当前仅可浏览。' }}</p></details>
    <BusinessAdvancedEditor v-model:visible="advancedVisible" :table="editorTable" :keys="advancedKeys" :kind="advancedKind" @saved="advancedSaved"/>
    <BusinessRowEditor v-model:visible="editorVisible" :table="editorTable" :row-key="editorKey" :mode="editorMode" @saved="rowSaved"/>
  </div>
</template>
<script setup>
import {computed,nextTick,onMounted,ref,watch} from 'vue'
import {ArrowLeft,ArrowRight,ArrowDown} from '@element-plus/icons-vue'
import {recordColumns,recordLabel,defaultRecordTable,recordValue} from '../businessRecords'
import {readWorkspace,saveWorkspace,restoreBrowse} from '../businessWorkspace'
import {businessColumnWidths} from '../businessTableLayout'
import BusinessAdvancedEditor from './BusinessAdvancedEditor.vue'
import BusinessRowEditor from './BusinessRowEditor.vue'
import {toast} from '../store'
import {businessRowsWithKeys,selectedBusinessKeys} from '../businessRows'
import {getBusinessDatabase,browseBusinessRows} from '../api'
const emit=defineEmits(['changed','structure','import'])
const props=defineProps({refreshKey:Number,tableRequest:Object,fixedTable:String,excludeTables:{type:Array,default:()=>[]}})
const showTechnical=ref(false)
const displayedColumns=computed(()=>recordColumns(table.value,result.value.columns,showTechnical.value))
const tableView=ref(null),actionsCollapsed=ref(true),tableFont=ref('14px sans-serif')
const measureContext=document.createElement('canvas').getContext('2d')
const columnWidths=computed(()=>businessColumnWidths(result.value.columns,result.value.rows.map(row=>row.map((value,index)=>recordValue(table.value,result.value.columns[index],value,showTechnical.value))),columnLabel,text=>{
  if(!measureContext)return text.length*14
  measureContext.font=tableFont.value
  return measureContext.measureText(text).width
}))
const stretchColumn=computed(()=>(table.value==='cw_tags' && displayedColumns.value.includes('tag') ? 'tag' : '') || displayedColumns.value.find(column=>/内容|说明|名称|描述|路径/.test(columnLabel(column))) || displayedColumns.value.reduce((widest,column)=>!widest || columnWidths.value[column]>columnWidths.value[widest] ? column : widest,''))
watch(actionsCollapsed,async()=>{await nextTick();tableView.value?.doLayout()})
function rowAction(action,index){if(action==='rename_key')openAdvanced(action,index);else if(['edit','delete'].includes(action))openEditor(index,action)}
const selected=ref([]),advancedVisible=ref(false),advancedKeys=ref([]),advancedKind=ref('bulk_update')
function openAdvanced(kind,index){editorTable.value=table.value;advancedKind.value=kind;advancedKeys.value=kind==='rename_key'?[result.value.row_keys[index]]:selectedBusinessKeys(selected.value);advancedVisible.value=true}
async function advancedSaved(){toast('success','维护已完成并记录修改记录');await loadRows(1);emit('changed')}
const connectionCapabilities=ref({})
const capabilities=computed(()=>table.value.startsWith('cw_')?{...connectionCapabilities.value,create:false,update:false,delete:false}:connectionCapabilities.value)
const editorVisible=ref(false),editorKey=ref({}),editorTable=ref(''),editorMode=ref('edit')
const tables=ref([]),table=ref(''),field=ref(''),op=ref('eq'),value=ref(''),pageSize=ref(50),result=ref({columns:[],rows:[],page:1,has_more:false})
const loading=ref(false),catalogLoading=ref(false),error=ref('')
const operators={eq:'等于',contains:'包含',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于'}
const currentTable=computed(()=>tables.value.find(t=>t.name===table.value))
const filterColumns=computed(()=>(currentTable.value?.columns || []).filter(c=>recordColumns(table.value,[c.name],showTechnical.value).length))
const rowObjects=computed(()=>businessRowsWithKeys(result.value))
function columnLabel(name){return ({case_id:'所号',case_key:'关联所号',existing_art:'现有技术',closest_art:'最接近的现有技术',distinguishing_features:'区别特征',comparison:'对比分析',primary_points:'主要保护点',secondary_points:'次要保护点',client_case_id:'客户案号',owner_name:'代理师',technical_domain:'技术领域',stage:'案件阶段',received_date:'实际收案日期',draft_due_date:'初稿期限',client_review_due_date:'客户送审期限',filing_due_date:'向局提交期限',actual_filed_date:'实际交局日期',filing_verified:'交局已核实',relative_path:'文件路径',kind:'文件类型',state:'解析状态',revision:'内容版本',meeting_date:'会议日期',iprs:'参会IPR',agents:'参会代理师',inventors:'参会发明人',content:'会议内容',pending:'待补材料',changes:'修改意见',tag:'技术标签',method:'标签来源',conflict_fields:'冲突字段'}[name]) || currentTable.value?.columns.find(c=>c.name===name)?.label || name}
let requestVersion=0,initialized=false
const appliedFilters=ref([]),restoredNotice=ref('')
function persistBrowse(){if(initialized)saveWorkspace('browse',{table:table.value,field:field.value,op:op.value,value:value.value,pageSize:pageSize.value,page:result.value.page || 1,filters:appliedFilters.value})}
watch([table,field,op,value,pageSize,appliedFilters],persistBrowse,{deep:true})
function openTable(name){if(!tables.value.some(t=>t.name===name)){error.value='当前业务数据库不存在该表：'+name;return}if(table.value===name)return;table.value=name;return changeTable()}
watch(()=>props.tableRequest,request=>{if(request && initialized)openTable(request.table)})
async function loadCatalog(){catalogLoading.value=true;error.value='';++requestVersion;try{
  const catalog=await getBusinessDatabase();tables.value=catalog.tables.filter(t=>!props.excludeTables.includes(t.name));connectionCapabilities.value=catalog.capabilities
  if(!initialized){
    const previous=readWorkspace('browse'),saved=restoreBrowse(previous,tables.value)
    if(props.fixedTable){table.value=tables.value.some(t=>t.name===props.fixedTable)?props.fixedTable:'';if(!table.value)error.value='当前连接尚未接入此类记录。'}
    else if(saved){table.value=saved.table;field.value=saved.field;op.value=saved.op;value.value=saved.value;pageSize.value=saved.pageSize;appliedFilters.value=saved.filters;result.value.page=saved.page;restoredNotice.value=`已恢复 ${saved.table} · 第 ${saved.page} 页 · 每页 ${saved.pageSize} 条 · `+(saved.filters.length?saved.filters.map(f=>`${columnLabel(f.field)} ${operators[f.op]} ${f.value}`).join('；'):'未应用筛选')+(saved.removed?'（失效筛选已移除）':'')}
    else {table.value=defaultRecordTable(tables.value);if(previous && !props.excludeTables.includes(previous.table))restoredNotice.value='上次查看的业务表已不存在，已切换到当前可用表。'}
    initialized=true
    if(props.tableRequest && props.tableRequest.table!==table.value)await openTable(props.tableRequest.table)
    else await loadRows(result.value.page || 1)
  }else{
    if(!tables.value.some(t=>t.name===table.value))table.value=props.fixedTable?'':defaultRecordTable(tables.value)
    await changeTable()
  }
}catch(e){error.value=/^(Failed to fetch|Load failed|NetworkError.*)$/i.test(e.message) ? '连接服务失败，请点击“重新加载”重试。' : e.message}finally{catalogLoading.value=false}}
function changeTable(){field.value='';op.value='eq';value.value='';appliedFilters.value=[];restoredNotice.value='';result.value={columns:[],rows:[],page:1,has_more:false};return loadRows(1)}
function search(){appliedFilters.value=field.value?[{field:field.value,op:op.value,value:value.value}]:[];return loadRows(1)}
function resetFilter(){field.value='';value.value='';op.value='eq';return search()}
async function loadRows(page){selected.value=[];const version=++requestVersion;if(!table.value){loading.value=false;return}loading.value=true;error.value='';try{const rows=await browseBusinessRows({table:table.value,page,page_size:pageSize.value,filters:appliedFilters.value});if(version===requestVersion){result.value=rows;persistBrowse()}}catch(e){if(version===requestVersion){error.value=/^(Failed to fetch|Load failed|NetworkError.*)$/i.test(e.message) ? '连接服务失败，请点击“重新加载”重试。' : e.message;result.value={columns:[],rows:[],page:1,has_more:false}}}finally{if(version===requestVersion)loading.value=false}}
function openCreate(){editorTable.value=table.value;editorKey.value={};editorMode.value='create';editorVisible.value=true}
watch(()=>props.refreshKey,()=>{if(table.value)loadRows(1)})
function openEditor(index,mode){editorKey.value=result.value.row_keys?.[index] || {};editorTable.value=table.value;editorMode.value=mode;editorVisible.value=true}
async function rowSaved(result){toast('success',result.operation==='insert'?'新记录已添加并保存修改记录':result.operation==='delete'?'记录已删除并保存修改记录':'记录已更新并保存修改记录');await loadRows(['insert','delete'].includes(result.operation)?1:resultPage());emit('changed')}
function resultPage(){return result.value.page || 1}
onMounted(async()=>{await loadCatalog();await nextTick();const cell=tableView.value?.$el?.querySelector('td .cell');if(cell)tableFont.value=getComputedStyle(cell).font || '14px sans-serif'})
</script>
<style scoped>
.restore-detail{margin:0;font-size:13px;line-height:1.7;overflow-wrap:anywhere}.restore-summary{color:var(--text-secondary);font-size:12px}.table-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;min-height:180px;padding:24px;line-height:1.6}.table-empty strong{font-size:15px;font-weight:500;color:var(--text)}.table-empty p{font-size:13px;margin:0;color:var(--text-secondary)}
.batch-actions{display:flex;gap:10px;align-items:center;font-size:14px;flex-wrap:wrap}.table-browser{height:100%;min-height:0;display:flex;flex-direction:column;gap:12px;padding-top:4px;overflow:auto}.browse-toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;flex-shrink:0}.browse-shortcuts{display:flex;align-items:center;gap:8px;margin-left:auto;flex-wrap:wrap}.browse-shortcuts .el-button+.el-button{margin-left:0}.browse-access{font-size:13px;color:var(--text-secondary);padding:3px 8px;background:var(--bg);border:1px solid var(--border);border-radius:5px}.scope-help{flex-shrink:0;color:var(--text-secondary);font-size:13px}.scope-help summary{cursor:pointer;width:fit-content}.scope-help p{padding-top:8px}.browse-toolbar label{display:flex;flex-direction:row;align-items:center;gap:7px;font-size:14px}.browse-toolbar .el-select{width:300px;max-width:100%}.scope-note{font-size:13px;line-height:1.6;color:var(--text-secondary);margin:0}.browse-filters>.el-button{margin-left:0}.browse-filters{flex-shrink:0;display:grid;grid-template-columns:minmax(140px,1fr) 125px minmax(150px,1.2fr) auto auto;gap:8px}.business-table{flex:1 0 200px;min-height:200px;font-size:14px}.business-table :deep(.el-table__cell){padding:12px 0}.business-table :deep(.cell){padding-left:12px;padding-right:12px}.business-table :deep(.el-table__fixed-right-patch){background:var(--card-bg)}.action-column-toggle{display:flex;align-items:center;justify-content:space-between;gap:6px;width:100%;padding:0;border:0;background:transparent;color:var(--text-secondary);font:inherit;font-weight:600;cursor:pointer;min-height:24px}.action-column-toggle .el-icon{color:var(--primary);font-size:14px}.action-column-toggle:hover{color:var(--primary)}.action-column-toggle:focus-visible{outline:2px solid var(--primary);outline-offset:2px;border-radius:3px}.row-action-buttons{display:flex;align-items:center;gap:12px}.row-action-buttons .el-button+.el-button{margin-left:0}.more-arrow{margin-left:4px;font-size:12px}.delete-row-menu{color:var(--danger-text)}.browse-pagination{flex-shrink:0;display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:14px}.browse-pagination .el-select{width:135px;margin-left:auto}.error{color:var(--el-color-danger);margin:0;font-size:14px}@media(max-width:700px){.browse-filters{grid-template-columns:1fr 1fr}.browse-filters>.el-select:first-child{grid-column:1/-1}.browse-toolbar .el-select{width:250px}.browse-pagination .el-select{margin-left:0}}

.business-table :deep(th.el-table__cell){background:var(--bg);color:var(--text-secondary);font-weight:600;}
.business-table :deep(.cell){line-height:1.65;word-break:normal;}
.browse-filters{padding:12px;background:var(--bg);border:1px solid var(--border);border-radius:7px;gap:10px;}
</style>
