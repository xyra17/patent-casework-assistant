<template>
  <section class="import-panel" aria-label="业务数据导入">
    <div class="import-toolbar">
      <div class="import-switch" role="tablist" aria-label="导入视图"><button type="button" role="tab" :aria-selected="view==='new'" :class="{active:view==='new'}" @click="view='new'">新建导入</button><button type="button" role="tab" :aria-selected="view==='history'" :class="{active:view==='history'}" @click="view='history'">导入记录<span v-if="catalog?.history.length">{{ catalog.history.length }}</span></button></div>
      <div class="import-toolbar-end"><span>校验预览后确认追加</span><el-button :loading="loading" :disabled="working" @click="refresh()">刷新</el-button></div>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <el-skeleton v-if="!catalog && loading" :rows="6" animated/>
    <template v-if="catalog">
      <div v-show="view==='new'" class="import-scroll">
        <div class="target-section">
          <label>目标连接<el-select v-model="selectedConnection" aria-label="导入目标连接" :disabled="working || loading || (!!batch && !canEdit)" @change="connectionChanged"><el-option v-for="c in catalog.connections" :key="c.id" :value="c.id" :label="`${c.label} · ${c.database}`"/></el-select><span>{{ selectedProfile?.host }}:{{ selectedProfile?.port }} · 已配置 {{ catalog.connections.length }} 个连接</span></label>
          <label>目标业务表<el-select v-model="tableName" filterable clearable aria-label="目标数据库表" placeholder="选择要追加数据的表" :disabled="working || loading || (!!batch && !canEdit)"><el-option v-for="t in catalog.tables" :key="t.name" :label="tableLabel(t)" :value="t.name" :disabled="t.engine!=='InnoDB'"/></el-select><span>向已有表追加记录，不会创建新表或覆盖已有记录</span></label>
        </div>
        <el-alert v-if="targetTable" class="dedupe-notice" :title="targetTable.dedupe_rule?.columns.length ? `业务查重依据：${targetTable.dedupe_rule.label}` : '尚无业务唯一标识，只检查数据库唯一约束'" :description="targetTable.dedupe_rule?.columns.length ? '需映射并填写上述字段。同一标识、映射字段内容不同属于冲突，必须修正；相同内容才允许跳过。' : targetTable.dedupe_rule?.notice" :type="targetTable.dedupe_rule?.columns.length?'info':'warning'" :closable="false" show-icon/>
        <el-alert v-if="!catalog.write_configured" title="此连接尚未配置导入账号，可先上传并校验" type="warning" :closable="false" show-icon/>
        <input ref="picker" class="file-picker" type="file" accept=".xlsx,.xls,.csv" aria-label="选择业务数据表格" @change="upload"/>
        <template v-if="!batch">
          <div class="upload-zone" :class="{dragging:dragActive,busy:uploading}" @dragover.prevent="dragActive=true" @dragleave.prevent="dragActive=false" @drop.prevent="dropFile">
            <div class="upload-icon"><el-icon :size="28"><Upload /></el-icon></div>
            <h2>{{ uploading?'正在读取表格…':'将表格拖到这里' }}</h2><p>导入 Excel 或 CSV，下一步检查字段映射与写入预览。</p>
            <el-button type="primary" :loading="uploading" :disabled="working" @click="picker.click()">选择文件</el-button>
            <div class="file-limits"><span>XLSX / XLS / CSV</span><span>单文件 ≤ 20 MB</span><span>最多 5000 行</span></div>
          </div>
          <details class="file-guide"><summary>表格需要怎么准备？</summary><p>首行为表头。公式请先粘贴为值；编号、长数字和前导零请在 Excel 中保存为文本。文件只会先暂存到服务器，确认写入前不会修改业务数据库。</p></details>
        </template>
        <div v-else class="batch-workspace">
          <div class="batch-content">
            <div class="batch-strip"><div><strong>{{ batch.filename }}</strong><span>{{ statusText(batch.status) }} · 批次 {{ batch.id.slice(0,8) }}</span></div><div class="batch-actions"><el-button v-if="canEdit" size="small" :disabled="working" @click="picker.click()">更换文件</el-button><el-button v-if="!canEdit" size="small" :disabled="working" @click="newBatch">新建导入</el-button><el-button v-if="canDiscard" link type="danger" :disabled="working" @click="discard">放弃批次</el-button></div></div>
            <el-alert v-if="batch.status==='completed'" :title="`已向 ${batch.table} 追加 ${batch.inserted_rows} 条记录，跳过 ${batch.skipped_rows || 0} 条相同重复`" type="success" :closable="false" show-icon/>
            <el-alert v-else-if="['uncertain','committing'].includes(batch.status)" :title="batch.message || '写入结果待核实，请勿重复提交'" type="warning" :closable="false" show-icon/>
            <el-alert v-else-if="batch.status==='failed'" :title="batch.message" type="error" :closable="false" show-icon/>
            <section v-if="canEdit" class="mapping-section"><label class="sheet-picker">工作表<el-select v-model="sheetName" :disabled="working" aria-label="工作表"><el-option v-for="s in batch.sheets" :key="s.name" :value="s.name" :label="`${s.name} · ${s.row_count} 行`"/></el-select></label>
          <details v-if="sourceSheet" class="source-preview"><summary>查看原始表格 · 前 5 行</summary>

            <el-table scrollbar-always-on class="app-resizable-table" :data="sourceSheet.preview" max-height="200" size="small" border>
              <el-table-column label="原表行号" width="90" prop="row" />
              <el-table-column v-for="(h, index) in sourceSheet.headers" :key="h" :label="h" min-width="125">
                <template #default="{row}">{{ row.cells[index] }}</template>
              </el-table-column>
            </el-table>
          </details>

          <template v-if="targetTable && sourceSheet">
            <h3>将表格列对应到业务字段</h3><p>已按字段名或字段说明匹配；请检查后再校验。未映射的可选字段使用数据库默认值，自增编号自动生成。</p>
            <el-table scrollbar-always-on border :data="targetTable.columns.filter(c => !c.generated)" max-height="320" size="small" class="app-resizable-table quiet-table-borders mapping-table">
              <el-table-column label="数据库字段" min-width="200"><template #default="{row}">
                <strong>{{ row.name }}</strong><span v-if="row.required" class="required"> 必填</span><small>{{ row.comment || '—' }}</small>
              </template></el-table-column>
              <el-table-column prop="type" label="字段类型" min-width="140" />
              <el-table-column label="来自表格列" min-width="220"><template #default="{row}">
                <el-select v-model="mapping[row.name]" clearable :disabled="working" :aria-label="`映射 ${row.name}`" placeholder="不导入，使用默认值">
                  <el-option v-for="h in sourceSheet.headers" :key="h" :label="h" :value="h" />
                </el-select>
              </template></el-table-column>
            </el-table>
            <div class="duplicate-policy"><strong>遇到重复记录</strong><el-radio-group v-model="duplicatePolicy" :disabled="working" aria-label="重复记录处理方式"><el-radio value="block">阻止整批导入</el-radio><el-radio value="skip">跳过内容相同的重复</el-radio></el-radio-group><p>内容冲突始终阻止写入。未映射字段使用数据库默认值，查重仅比较本次映射内容。</p></div>
            <div class="validation-actions"><el-button type="primary" :loading="validating" :disabled="working || loading || !!error || !Object.keys(cleanMapping).length" @click="validate">校验并预览</el-button>
              <span>此步骤只读取数据，不向业务表试写。</span></div>
          </template>

            </section>
        <section v-if="validation && canEdit" ref="validationSection" class="validation-section">
          <h3>校验与写入预览</h3>
          <div v-if="validation.dedupe" class="duplicate-report"><h3>查重预览</h3><div class="duplicate-counts"><span>待新增 <strong>{{ validation.dedupe.new }}</strong></span><span>相同重复 <strong>{{ validation.dedupe.duplicate }}</strong></span><span :class="{'error':validation.dedupe.conflict}">内容冲突 <strong>{{ validation.dedupe.conflict }}</strong></span></div><p>{{ duplicatePolicy==='skip'?'确认后仅追加待新增记录，相同重复会跳过。':'发现重复则整批阻止，可检查后选择跳过相同重复并重新校验。' }}<template v-if="!validation.insert_count"> 当前没有待新增记录，不能提交。</template></p><el-table scrollbar-always-on border class="app-resizable-table quiet-table-borders" :data="validation.dedupe.records" max-height="200" size="small"><el-table-column prop="row" label="原表行号" width="90"/><el-table-column label="检查结果" width="110"><template #default="{row}"><el-tag :type="row.status==='new'?'success':row.status==='conflict'?'danger':'warning'" effect="plain">{{ {new:'待新增',duplicate:'相同重复',conflict:'内容冲突'}[row.status] }}</el-tag></template></el-table-column><el-table-column label="命中依据与位置" min-width="260"><template #default="{row}">{{ row.matches.map(m=>`${m.source} · ${m.rule}${m.differences?.length?' · 差异字段：'+m.differences.join('、'):''}`).join('；') || '未命中查重标识' }}</template></el-table-column></el-table><p v-if="validation.dedupe.detail_truncated">仅展示前100行明细；上方统计覆盖全部记录。</p></div>
          <el-alert :type="validation.valid ? 'success' : 'error'" :closable="false" show-icon
            :title="validationTitle"
            :description="validation.valid ? '确认写入前会再次查重；结果变化需重新校验。写入时继续检查唯一键、外键等约束，任一行失败会回滚本批次。' : '请核对下方字段错误和上方查重结果；修正文件、映射或重复处理方式后重新校验。'" />
          <el-table scrollbar-always-on border class="app-resizable-table quiet-table-borders" v-if="validation.error_count" :data="validation.errors" max-height="260" size="small">
            <el-table-column prop="row" label="原表行号" width="90" /><el-table-column prop="column" label="数据库字段" min-width="140" />
            <el-table-column prop="value" label="原始值" min-width="130" /><el-table-column prop="message" label="问题" min-width="220" />
          </el-table>
          <template v-if="validation.valid">
            <p v-if="validation.unmapped_headers.length" class="notice">以下源列不会写入：{{ validation.unmapped_headers.join('、') }}</p>
            <h3>实际写入预览 · 前 10 行</h3>
            <el-table scrollbar-always-on class="app-resizable-table" :data="validation.preview" size="small" max-height="240" border>
              <el-table-column v-for="(name, index) in validation.columns" :key="name" :label="name" min-width="140">
                <template #default="{row}"><span :class="{'muted': row[index] == null}">{{ row[index] == null ? 'NULL' : row[index] }}</span></template>
              </el-table-column>
            </el-table>
            <div class="commit-actions"><p>点击后需再次确认，将以一个事务追加{{ validation.insert_count }} 条记录，跳过 {{ duplicatePolicy==='skip' ? validation.dedupe?.duplicate || 0 : 0 }} 条相同重复。</p>
              <el-button type="primary" :loading="committing" :disabled="working || loading || !!error || !catalog.write_configured" @click="commit">确认写入数据库</el-button></div>
          </template>
        </section>

          </div>
          <aside class="batch-progress" aria-label="本批次导入进度"><h3>本次进度</h3><ol><li v-for="(title,index) in steps" :key="title" :class="{done:index<step,current:index===step}"><span class="step-number">{{ index<step?'✓':index+1 }}</span><span>{{ title }}</span></li></ol><p>校验通过后仍需确认，才会正式写入。</p></aside>
        </div>
      </div>
      <div v-show="view==='history'" class="history-section"><div class="history-heading"><div><h2>最近导入批次</h2><p>展示最近 20 个批次；成功记录用于防重，待核实结果请先检查数据库。</p></div><el-button :disabled="working" type="primary" @click="newBatch">新建导入</el-button></div><el-empty v-if="!catalog.history.length" description="暂无导入记录" :image-size="70"/>
        <el-table scrollbar-always-on border v-else :data="catalog.history" size="small" height="100%" class="app-resizable-table quiet-table-borders history-table">
          <el-table-column prop="filename" label="文件" min-width="160" show-overflow-tooltip />
          <el-table-column label="连接" min-width="160"><template #default="{row}">{{ connectionLabel(row) }}</template></el-table-column>
          <el-table-column prop="table" label="目标表" min-width="110" />
          <el-table-column label="写入 / 跳过" width="120"><template #default="{row}">{{ row.inserted_rows ?? row.row_count ?? '—' }} / {{ row.skipped_rows || 0 }}</template></el-table-column>
          <el-table-column label="状态" width="110"><template #default="{row}"><el-tag size="small" effect="plain" :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag></template></el-table-column>
          <el-table-column label="时间" min-width="165"><template #default="{row}">{{ new Date(row.created_at).toLocaleString('zh-CN', {hour12: false}) }}</template></el-table-column>
          <el-table-column label="操作" width="100"><template #default="{row}"><el-button link type="primary" :disabled="working" :aria-label="`查看批次 ${row.filename}`" @click="resume(row.id)">查看批次</el-button></template></el-table-column>
        </el-table>

      </div>
    </template>
  </section>
</template>

<script setup>
import {computed,nextTick,onMounted,ref,watch} from 'vue'
import {ElMessageBox} from 'element-plus'
import {getDataImport,getImportConnections,uploadImport,getImportBatch,validateImport,commitImport,discardImport} from '../api'
import {toast} from '../store'
import {importFileError} from '../importState'
const emit=defineEmits(['committed'])
const duplicatePolicy=ref('block')
const validationTitle=computed(()=>validation.value?.valid?`校验通过 · ${validation.value.insert_count} 条待新增到 ${tableName.value}`:validation.value?.error_count?`发现 ${validation.value.error_count} 个字段错误，尚未写入数据库`:'查重未通过，尚未写入数据库')
const catalog=ref(null),error=ref(''),loading=ref(false),picker=ref(null),view=ref('new'),selectedConnection=ref('default'),dragActive=ref(false)
const validationSection=ref(null)
const batch=ref(null),sheetName=ref(''),tableName=ref(''),mapping=ref({}),validation=ref(null)
const uploading=ref(false),validating=ref(false),committing=ref(false),discarding=ref(false)
const working=computed(()=>uploading.value||validating.value||committing.value||discarding.value)
const sourceSheet=computed(()=>batch.value?.sheets.find(s=>s.name===sheetName.value))
const selectedProfile=computed(()=>catalog.value?.connections.find(c=>c.id===selectedConnection.value))
const targetTable=computed(()=>catalog.value?.tables.find(t=>t.name===tableName.value))
const cleanMapping=computed(()=>Object.fromEntries(Object.entries(mapping.value).filter(([,v])=>v)))
const canEdit=computed(()=>batch.value&&!['completed','committing','uncertain'].includes(batch.value.status))
const canDiscard=computed(()=>canEdit.value)
const steps=['上传表格','字段映射','校验预览','写入完成']
const step=computed(()=>batch.value?.status==='completed'?4:['committing','uncertain'].includes(batch.value?.status)?3:validation.value?.valid?2:batch.value?1:0)
let catalogRequest=0
function autoMap(){const headers=sourceSheet.value?.headers||[];const normalize=value=>value.toLowerCase().replace(/[\s_]/g,'');mapping.value=Object.fromEntries((targetTable.value?.columns||[]).filter(c=>!c.generated).map(c=>{const h=headers.find(h=>normalize(h)===normalize(c.name))||headers.find(h=>c.comment&&normalize(h)===normalize(c.comment));return[c.name,h||'']}))}
watch([sheetName,tableName],autoMap)
watch([selectedConnection,sheetName,tableName,mapping,duplicatePolicy],()=>{validation.value=null},{deep:true})
async function refresh(){const request=++catalogRequest;loading.value=true;error.value='';try{const value=await getDataImport(selectedConnection.value);if(request!==catalogRequest)return false;catalog.value=value;return true}catch(e){if(request===catalogRequest){error.value=e.message;try{const registry=await getImportConnections();if(request===catalogRequest)catalog.value={tables:[],history:catalog.value?.history||[],write_configured:false,connections:registry.connections}}catch{}}return false}finally{if(request===catalogRequest)loading.value=false}}
async function connectionChanged(){validation.value=null;tableName.value='';mapping.value={};catalog.value={...catalog.value,tables:[]};await refresh()}
function setBatch(value,preserveTarget=true){duplicatePolicy.value=value.duplicate_policy||'block';const previous=tableName.value;batch.value=value;validation.value=null;sheetName.value=value.sheet||value.sheets[0]?.name||'';tableName.value=value.table||(preserveTarget?previous:'');autoMap();view.value='new'}
function newBatch(){batch.value=null;validation.value=null;sheetName.value='';mapping.value={};view.value='new'}
async function acceptFile(file){if(!file||working.value)return;const problem=importFileError(file);if(problem){toast('error',problem);return}uploading.value=true;try{setBatch(await uploadImport(file));await refresh();toast('success','文件已暂存，尚未写入数据库')}catch(e){toast('error',e.message)}finally{uploading.value=false}}
function upload(event){const file=event.target.files?.[0];event.target.value='';return acceptFile(file)}
function dropFile(event){dragActive.value=false;const files=event.dataTransfer?.files;if(files?.length!==1){toast('error','请一次上传一个表格文件');return}return acceptFile(files[0])}
async function validate(){validating.value=true;validation.value=null;try{validation.value=await validateImport(batch.value.id,{sheet:sheetName.value,table:tableName.value,mapping:cleanMapping.value,duplicate_policy:duplicatePolicy.value,connection_id:selectedConnection.value});batch.value.status=validation.value.valid?'validated':'invalid';batch.value.connection_id=selectedConnection.value;await refresh();await nextTick();validationSection.value?.scrollIntoView({behavior:'smooth',block:'start'})}catch(e){toast('error',e.message)}finally{validating.value=false}}
async function commit(){if(!validation.value?.valid||working.value)return;try{await ElMessageBox.confirm(`将向“${catalog.value.destination.database}.${tableName.value}”追加 ${validation.value.insert_count} 条记录，跳过 ${duplicatePolicy.value==='skip'?validation.value.dedupe?.duplicate||0:0} 条相同重复。请核对目标连接、查重依据与预览。`,'确认导入业务数据？',{type:'warning',confirmButtonText:'确认追加数据',cancelButtonText:'返回检查'})}catch{return}committing.value=true;try{const result=await commitImport(batch.value.id,validation.value.validation_token,selectedConnection.value);batch.value={...batch.value,...result};validation.value=null;toast('success',`已追加 ${result.inserted_rows} 条，跳过 ${result.skipped_rows || 0} 条相同重复`);emit('committed')}catch(e){toast('error',e.message);try{batch.value=await getImportBatch(batch.value.id)}catch{}validation.value=null}finally{committing.value=false;await refresh()}}
async function resume(id){loading.value=true;try{const value=await getImportBatch(id);selectedConnection.value=value.connection_id||'default';if(await refresh())setBatch(value,false)}catch(e){toast('error',e.message)}finally{loading.value=false}}
async function discard(){try{await ElMessageBox.confirm('删除本批次暂存文件和预览记录，不会修改业务数据库。','放弃导入批次？',{type:'warning',confirmButtonText:'删除暂存批次',cancelButtonText:'取消'})}catch{return}discarding.value=true;try{await discardImport(batch.value.id);newBatch();await refresh()}catch(e){toast('error',e.message)}finally{discarding.value=false}}
function tableLabel(table){return table.name+(table.comment?' · '+table.comment:'')}
function connectionLabel(row){if(!row.connection_id&&!row.destination)return '校验时绑定';const profile=catalog.value.connections.find(c=>c.id===(row.connection_id||'default'));return profile?.label||row.destination?.database||'原连接已停用'}
function statusText(status){return{draft:'已暂存',validated:'待确认写入',invalid:'校验未通过',committing:'写入待核实',uncertain:'结果待核实',completed:'导入成功',failed:'写入失败'}[status]||status}
function statusType(status){return status==='completed'?'success':['failed','invalid'].includes(status)?'danger':'warning'}
async function openForTable(name){
  if(working.value){toast('warning','导入正在处理中，请稍后切换目标表');return}
  if(batch.value && (selectedConnection.value!=='default' || tableName.value!==name)){
    try{await ElMessageBox.confirm('已上传文件仍保留在导入记录中；开始当前表的新导入会重置本页尚未校验的字段映射。','切换批量导入目标？',{confirmButtonText:'开始当前表导入',cancelButtonText:'保留当前工作'})}catch{return}
    newBatch()
  }
  selectedConnection.value='default'
  if(await refresh()){
    if(!catalog.value.tables.some(t=>t.name===name)){toast('error','当前业务表不存在或不可导入');return}
    if(batch.value?.status==='completed')newBatch()
    tableName.value=name;view.value='new'
  }
}
defineExpose({openForTable})
onMounted(refresh)
</script>

<style scoped>
.import-panel{height:100%;min-height:0;display:flex;flex-direction:column;color:var(--text);overflow:hidden}.import-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 0 16px;flex-shrink:0}.import-switch{display:flex;gap:4px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:3px}.import-switch button{border:0;background:transparent;color:var(--text-secondary);font:inherit;font-size:14px;padding:7px 14px;border-radius:5px;cursor:pointer}.import-switch button.active{background:var(--primary-soft);color:var(--primary);font-weight:600}.import-switch button:focus-visible{outline:2px solid var(--primary);outline-offset:2px}.import-switch span{font-size:12px;margin-left:7px}.import-scroll{flex:1;min-height:0;overflow:auto;padding-right:8px}.import-toolbar-end{display:flex;align-items:center;gap:14px}.import-toolbar-end>span{font-size:13px;color:var(--text-secondary)}.target-section{display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:0 0 18px;border-bottom:1px solid var(--border);margin-bottom:20px}.target-section label,.sheet-picker{display:flex;flex-direction:column;gap:8px;font-size:14px;font-weight:500;min-width:0}.target-section label>span{font-size:12px;color:var(--text-secondary);font-weight:400}.target-section .el-select{width:100%}.file-picker{display:none}.upload-zone{min-height:220px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:13px;border:1px dashed var(--border);border-radius:12px;background:var(--bg);padding:24px 20px;transition:border-color .15s,background .15s}.upload-zone.dragging{border-color:var(--primary);background:var(--primary-soft)}.upload-icon{height:54px;width:54px;display:grid;place-items:center;border-radius:12px;background:var(--primary-soft);color:var(--primary)}h2{margin:0;font-size:19px;font-weight:600}h3{font-size:15px;margin:18px 0 10px}p{font-size:13px;line-height:1.7;color:var(--text-secondary);margin:0}.file-limits{display:flex;justify-content:center;flex-wrap:wrap;gap:0;font-size:12px;color:var(--text-secondary);margin-top:2px}.file-limits span+span:before{content:'·';padding:0 10px}.file-guide{margin:16px 0;font-size:13px;color:var(--text-secondary)}summary{cursor:pointer;padding:5px 0}.file-guide p{margin-top:8px}.batch-workspace{display:grid;grid-template-columns:minmax(0,1fr) 155px;gap:20px}.batch-content{min-width:0}.batch-strip{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 0;border-bottom:1px solid var(--border);margin-bottom:16px}.batch-strip strong{font-size:15px;overflow-wrap:anywhere}.batch-strip span{display:block;font-size:12px;color:var(--text-secondary);margin-top:5px}.batch-actions{display:flex;align-items:center;gap:8px;flex-shrink:0}.batch-progress{align-self:start;padding-left:18px;border-left:1px solid var(--border);position:sticky;top:0}.batch-progress h3{margin:0 0 16px;font-size:14px}.batch-progress ol{list-style:none;padding:0;margin:0 0 18px}.batch-progress li{display:flex;align-items:center;gap:9px;padding:0 0 24px;color:var(--text-secondary);font-size:13px}.step-number{width:24px;height:24px;display:grid;place-items:center;border:1px solid var(--border);border-radius:50%;font-size:12px;flex-shrink:0}.batch-progress .current{color:var(--primary);font-weight:600}.current .step-number{background:var(--primary-soft);border-color:var(--primary)}.batch-progress .done{color:var(--success-text)}.batch-progress p{font-size:12px}.sheet-picker{max-width:320px;margin:15px 0}.source-preview{font-size:13px;color:var(--text-secondary);margin:12px 0}.source-preview .el-table{margin-top:10px}.mapping-table small{display:block;font-size:12px;color:var(--text-secondary)}.mapping-section>p{margin:8px 0 12px}.validation-section{margin:22px 0}.validation-actions,.commit-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:15px 0;flex-wrap:wrap}.validation-actions span,.muted{font-size:12px;color:var(--text-secondary)}.notice{margin-top:10px}.required,.error{color:var(--danger-text)}.required{font-size:12px}.error{margin-bottom:12px}.history-section{flex:1;min-height:0;display:flex;flex-direction:column}.history-heading{display:flex;justify-content:space-between;align-items:center;gap:14px;margin:4px 0 18px}.history-heading p{margin-top:7px}.history-table{flex:1;min-height:180px}.import-scroll>.el-alert{margin-bottom:16px}.duplicate-policy{padding:16px 0;border-bottom:1px solid var(--border)}.duplicate-policy>strong{display:block;font-size:14px;margin-bottom:6px}.duplicate-policy .el-radio-group{display:flex;flex-wrap:wrap;gap:0 16px}.duplicate-report{margin:16px 0}.duplicate-counts{display:flex;flex-wrap:wrap;gap:20px;padding:12px 0;font-size:14px}.duplicate-counts strong{font-size:18px;margin-left:8px}.duplicate-report>.el-table{margin:12px 0}@media(max-width:1050px){.batch-workspace{grid-template-columns:minmax(0,1fr)}.batch-progress{grid-row:1;border:0;border-bottom:1px solid var(--border);padding:0;position:static}.batch-progress h3,.batch-progress>p{display:none}.batch-progress ol{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:12px}.batch-progress li{padding:0 0 6px;gap:7px}}@media(max-width:650px){.target-section{grid-template-columns:1fr;gap:14px}.import-toolbar-end>span{display:none}.upload-zone{min-height:200px}.batch-strip{flex-wrap:wrap}.history-heading{align-items:flex-start;flex-wrap:wrap}.import-switch button{padding:7px 10px}}
</style>
