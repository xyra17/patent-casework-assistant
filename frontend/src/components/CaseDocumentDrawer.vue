<template>
  <el-drawer v-model="visible" class="source-document-drawer" title="来源原文" size="min(800px, 96vw)" append-to-body @close="cancelLoad">
    <p v-if="error" role="alert" class="source-error">{{ error }} <el-button link @click="load()">重试</el-button></p>
    <div v-loading="loading" class="source-body">
      <template v-if="doc">
        <header class="source-heading">
          <div class="source-file-title"><el-icon aria-hidden="true"><Document /></el-icon><h2>{{ filename }}</h2></div>
          <div class="source-summary"><span>所编号 {{ doc.case_id || '未识别' }}</span><span>{{ kindLabel }}</span><el-tag :type="statusTone" effect="plain" size="small">{{ statusLabel }}</el-tag></div>
        </header>

        <div v-if="notice" class="source-notice" :class="statusTone" role="status"><p>{{ notice }}</p><el-button v-if="canViewCurrent" link type="primary" @click="showCurrent">查看当前解析</el-button></div>
        <div class="source-toolbar">
          <label v-if="doc.history.length" class="source-version"><span>解析记录</span><el-select v-model="versionIndex" aria-label="解析版本" @change="changeVersion"><el-option v-for="(v,i) in doc.history" :key="v.revision+v.parser_key" :value="i" :label="versionLabel(v)" /></el-select></label>
          <el-button :icon="Download" :disabled="!doc.download_available || loading" :loading="downloading" @click="download">下载原件</el-button>
        </div>
        <div class="source-reading-note"><span>{{ doc.version_status==='inventory' ? '附件登记信息 · 可下载原件查看' : doc.source_status==='verified' ? '已核对磁盘原件，内容一致' : '展示已保存的解析文本' }} · 非模型改写</span><el-button v-if="!viewingReference" link type="primary" @click="showReference">返回最初引用</el-button></div>

        <details class="source-metadata"><summary>文件路径与版本信息</summary><dl><div><dt>相对路径</dt><dd>{{ doc.relative_path }}</dd></div><div><dt>解析时间</dt><dd>{{ sourceTime(doc.parsed_at) }}</dd></div><div><dt>原件内容版本</dt><dd><code>{{ doc.revision }}</code></dd></div><div><dt>解析标识</dt><dd><code>{{ doc.parser_key }}</code></dd></div></dl></details>
        <el-alert v-if="doc.warnings?.length && doc.version_status!=='inventory'" type="warning" :closable="false" title="解析存在待核对项"><p v-for="(w,i) in doc.warnings" :key="i">{{ w.reason }} · {{ w.location }}</p></el-alert>

        <p v-if="doc.focus_status==='not_found'" class="source-notice" role="status">未能在所选解析版本定位这条引用，请在下方分页核对正文。</p>
        <section class="source-text" aria-label="解析正文"><div class="source-text-heading"><h3>{{ focusedView ? '引用及相邻段落' : '原文内容' }}</h3><span v-if="!focusedView">共 {{ doc.total_chunks }} 个片段</span><el-button v-if="doc.focus_status==='located'" link type="primary" @click="focusedView=!focusedView">{{ focusedView ? '展开本页正文' : '回到引用位置' }}</el-button></div>
          <article v-for="(chunk,i) in displayedChunks" :key="chunk.ordinal ?? i" class="source-chunk" :class="{'is-cited':chunk.cited}"><header><strong>{{ chunk.cited ? '引用段落' : '片段 '+((chunk.ordinal ?? offset+i)+1) }}</strong><span>{{ sourceLocationSummary(chunk.location) }}</span></header>
            <dl v-if="chunk.fields" class="source-fields"><div v-for="(field,j) in chunk.fields" :key="j"><dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></div></dl>
            <pre v-else><template v-for="(part,j) in highlightSegments(chunk.text,selected.matched_terms || [])" :key="j"><mark v-if="part.matched">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></pre>
          </article>
          <el-empty v-if="!doc.chunks?.length" :description="doc.kind==='drawing' ? '未提取到图中文字，可下载原件查看绘图' : '这份文件没有可展示的解析正文'" :image-size="60" />
        </section>
        <nav v-if="!focusedView && doc.total_chunks>10" class="source-pagination" aria-label="正文分页"><el-button :disabled="offset===0 || loading" @click="page(-1)">上一页</el-button><span>{{ offset+1 }}–{{ Math.min(offset+10,doc.total_chunks) }} / {{ doc.total_chunks }} 个片段</span><el-button :disabled="!doc.has_more || loading" @click="page(1)">下一页</el-button></nav>
        <p class="source-footnote">解析文本可能未覆盖图片、扫描 PDF 或未识别的字段。下载时会再次核对原件与所选内容版本。</p>
      </template>
    </div>
  </el-drawer>
</template>
<script setup>
import {computed,ref,onBeforeUnmount} from 'vue'
import {Document,Download} from '@element-plus/icons-vue'
import {casework} from '../caseworkApi'
import {sourceFieldRows,sourceTime} from '../sourceDocumentPresentation'
import {sourceLocationSummary} from '../sourcePresentation'
import {highlightSegments} from '../archivePresentation'
import {ElMessage} from 'element-plus'
const visible=ref(false),doc=ref(null),error=ref(''),loading=ref(false),downloading=ref(false),versionIndex=ref(0),offset=ref(0),reference=ref(null)
const selected=ref({}),focusedView=ref(false)
let controller
const filename=computed(()=>doc.value?.relative_path.split('/').pop() || '')
const kindLabel=computed(()=>({intake:'立案资料',ledger:'案件信息',disclosure:'技术交底',application:'申请文件',meeting:'三方会议记录',receipt:'回执',filing:'交局记录',drawing:'绘图文件'}[doc.value?.kind] || '案卷文件'))
const displayedChunks=computed(()=>(focusedView.value ? doc.value?.focus_context || [] : doc.value?.chunks || []).map(chunk=>({...chunk,fields:doc.value?.kind==='drawing' ? null : sourceFieldRows(chunk.text)})))
const statusTone=computed(()=>doc.value?.source_notice ? 'warning' : doc.value?.version_status==='inventory' ? 'info' : doc.value?.current ? 'success' : doc.value?.version_status==='parser_updated' ? 'info':'warning')
const statusLabel=computed(()=>{
  if(doc.value?.source_status==='changed')return '原件待同步'
  if(doc.value?.source_status==='unavailable')return '原件暂不可读'
  return {inventory:'仅登记附件',current:'当前解析',parser_updated:'原件内容未变',content_changed:'历史内容',disabled:'目录已停用',removed:'原件已移除',parse_failed:'最新解析未成功'}[doc.value?.version_status] || '待核对'
})
const notice=computed(()=>doc.value?.source_notice || (!doc.value?.current || doc.value?.version_status==='inventory' ? doc.value?.version_note : ''))
const canViewCurrent=computed(()=>doc.value && !doc.value.current && doc.value.active && doc.value.enabled && doc.value.state==='parsed')
const viewingReference=computed(()=>!reference.value || (doc.value?.revision===reference.value.revision && doc.value?.parser_key===reference.value.parser_key && doc.value?.focus_index===reference.value.focus_index && offset.value===reference.value.offset))
function versionLabel(version){return (version.revision===doc.value.current_revision && version.parser_key===doc.value.current_parser_key ? '当前解析' : version.revision===doc.value.current_revision ? '同一原件的旧解析' : '历史内容')+' · '+sourceTime(version.created_at)}
async function load(locate=true){
  controller?.abort();controller=new AbortController();const current=controller
  loading.value=true;error.value=''
  try{
    const proof=selected.value
    const focus=locate && !proof.association && !proof.location?.startsWith('文件登记') ? {focus_chunk_id:proof.chunk_id,focus_location:proof.location,focus_excerpt:proof.excerpt || proof.snippet} : {}
    const value=await casework('/documents/'+encodeURIComponent(proof.file_id),{params:{revision:proof.revision,parser_key:proof.parser_key,offset:offset.value,...focus},signal:current.signal})
    if(current!==controller)return
    doc.value=value;offset.value=value.offset;focusedView.value=value.focus_status==='located'
    selected.value={...proof,revision:value.revision,parser_key:value.parser_key}
    if(!reference.value)reference.value={...selected.value,focus_index:value.focus_index,offset:value.offset}
    versionIndex.value=Math.max(0,value.history.findIndex(v=>v.revision===value.revision && v.parser_key===value.parser_key))
  }catch(e){if(current===controller && e.name!=='AbortError'){error.value=e.message;doc.value=null}}
  finally{if(current===controller)loading.value=false}
}
function open(proof){selected.value={...proof};reference.value=null;offset.value=0;doc.value=null;visible.value=true;load()}
function page(step){offset.value+=step*10;load(false)}
function changeVersion(i){selected.value={...selected.value,...doc.value.history[i],chunk_id:undefined,location:undefined};offset.value=0;load()}
function showCurrent(){selected.value={...selected.value,revision:doc.value.current_revision,parser_key:doc.value.current_parser_key,chunk_id:undefined,location:undefined};offset.value=0;load()}
function showReference(){selected.value={...reference.value};offset.value=0;load()}
async function download(){
  const snapshot=doc.value;if(!snapshot?.download_available || downloading.value)return
  downloading.value=true
  try{
    const query=new URLSearchParams({revision:snapshot.revision})
    const res=await fetch('/api/casework/documents/'+encodeURIComponent(snapshot.id)+'/download?'+query)
    if(!res.ok){const data=await res.json();throw new Error(data.detail)}
    const url=URL.createObjectURL(await res.blob()),a=document.createElement('a');a.href=url;a.download=snapshot.relative_path.split('/').pop();a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  }catch(e){ElMessage.error(e.message)}finally{downloading.value=false}
}
function cancelLoad(){controller?.abort();controller=undefined;loading.value=false}
onBeforeUnmount(cancelLoad);defineExpose({open})
</script>
<style scoped>
:global(.source-document-drawer .el-drawer__header){margin-bottom:0;padding:20px 24px 16px;border-bottom:1px solid var(--border)}
:global(.source-document-drawer .el-drawer__body){padding:20px 24px}
.source-body{min-height:180px;display:flex;flex-direction:column;gap:18px;font-size:14px;color:var(--text)}
.source-file-title{display:flex;align-items:flex-start;gap:10px}.source-file-title>.el-icon{font-size:21px;color:var(--text-secondary);margin-top:3px;flex-shrink:0}.source-heading h2{font-size:19px;font-weight:600;overflow-wrap:anywhere;line-height:1.5;margin:0}
.source-summary{display:flex;align-items:center;flex-wrap:wrap;gap:8px 16px;color:var(--text-secondary);font-size:13px;margin-top:12px;line-height:1.6}
.source-notice{border:1px solid var(--border);border-radius:8px;background:var(--bg);padding:12px 14px;line-height:1.75;font-size:13px}.source-notice.warning{border-color:var(--el-color-warning-light-7);background:var(--el-color-warning-light-9)}.source-notice p{margin:0}.source-notice>.el-button{margin:6px 0 0}
.source-toolbar{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap}.source-version{display:flex;flex:1;min-width:220px;flex-direction:column;gap:8px}.source-version>span{font-size:13px;color:var(--text-secondary)}.source-version .el-select{width:100%}.source-toolbar>.el-button{flex-shrink:0}
.source-reading-note{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px 12px;font-size:12px;color:var(--text-secondary);line-height:1.7;margin-top:-8px}
.source-metadata{border-bottom:1px solid var(--border);padding-bottom:16px}.source-metadata summary{color:var(--text-secondary);font-size:13px;cursor:pointer;line-height:1.7}.source-metadata dl{margin:14px 0 0;display:grid;gap:10px}.source-metadata dl>div{display:grid;grid-template-columns:95px minmax(0,1fr);gap:12px;font-size:12px;line-height:1.7}.source-metadata dt{color:var(--text-secondary)}.source-metadata dd{margin:0;overflow-wrap:anywhere}.source-metadata code{font-size:11px}
.source-text-heading{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:12px}.source-text-heading h3{font-size:15px;font-weight:600;margin:0}.source-text-heading>span{font-size:12px;color:var(--text-secondary)}
.source-chunk{border:1px solid var(--border);border-radius:8px;padding:16px;margin-top:12px}.source-chunk>header{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 12px;color:var(--text-secondary);font-size:12px;margin-bottom:12px}.source-chunk>header strong{color:var(--text);font-size:13px;font-weight:500}.source-chunk pre{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:14px;line-height:1.9;margin:0}
.source-chunk.is-cited{border-color:var(--primary);border-inline-start-width:3px}.source-chunk mark{background:var(--el-color-primary-light-9);color:var(--text);font-weight:600;border-radius:2px;padding:1px 0}
.source-fields{margin:0}.source-fields>div{display:grid;grid-template-columns:125px minmax(0,1fr);gap:16px;padding:10px 0;border-top:1px solid var(--border);line-height:1.75}.source-fields>div:first-child{border-top:0;padding-top:0}.source-fields>div:last-child{padding-bottom:0}.source-fields dt{color:var(--text-secondary);font-size:13px;overflow-wrap:anywhere}.source-fields dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
.source-pagination{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:10px;border-top:1px solid var(--border);padding-top:16px}.source-pagination>span{font-size:12px;color:var(--text-secondary)}.source-pagination :deep(.el-button+.el-button){margin-left:0}
.source-footnote{font-size:12px;line-height:1.8;color:var(--text-secondary);margin:0}.source-error{color:var(--el-color-danger);font-size:14px;line-height:1.7}
@media(max-width:560px){:global(.source-document-drawer .el-drawer__header){padding:16px}:global(.source-document-drawer .el-drawer__body){padding:16px}.source-version{min-width:0;width:100%;flex-basis:100%}.source-toolbar>.el-button{margin-left:auto}.source-heading h2{font-size:17px}.source-chunk{padding:12px}.source-fields>div{grid-template-columns:94px minmax(0,1fr);gap:12px}.source-metadata dl>div{grid-template-columns:minmax(0,1fr);gap:3px}.source-summary{gap:8px 12px}}
</style>
