<template>
  <section class="archive-results" aria-label="相似案件检索结果">
    <div v-if="result.results.length" class="archive-result-toolbar">
      <div><strong>{{ expanded ? '本批参考案件' : '优先查看' }}</strong><span>显示 {{ visibleRows.length }} / {{ result.results.length }} 件</span></div>
      <el-button :disabled="copying" @click="copyList">复制这 {{ visibleRows.length }} 件的参考清单</el-button>
    </div>
    <p v-if="result.results.length" class="archive-scope">{{ result.results.every(row=>!row.sources.length) ? '以下附件按所编号关联，可查看登记信息并下载原件；附件登记未作为技术正文依据。' : '先核对匹配原文，需要时再查看相邻段落。当前为检索候选，不能据此认定已找齐所有案件。' }}</p>
    <el-empty v-else description="当前接入资料未找到可靠匹配，可换一种技术描述或检查同步范围" />
    <article v-for="(row,index) in visibleRows" :key="row.case_key" class="archive-result">
      <header class="archive-case-heading">
        <span class="archive-rank" aria-hidden="true">{{ index+1 }}</span>
        <div><button type="button" class="archive-case-link" @click="emit('open-case',row.case_key)">{{ row.case_id }} · {{ row.title || '名称未登记' }}</button><p>{{ row.owner_name || '代理师未登记' }} · {{ row.technical_domain || '领域未登记' }} · {{ row.stage || '状态未知' }}</p></div>
      </header>
      <p class="archive-match"><span>匹配依据</span>{{ row.match_basis==='case_id_attachment' ? '按所编号关联附件，未作为技术正文依据' : row.matched_tags?.length ? row.matched_tags.join('、') : '正文相关性匹配，请核对具体机制' }}</p>
      <template v-for="(proof,i) in row.sources.slice(0,1)" :key="proof.chunk_id || i">
        <blockquote class="archive-excerpt"><template v-for="(part,j) in highlightSegments(proof.excerpt,proof.matched_terms)" :key="j"><mark v-if="part.matched">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></blockquote>
        <div class="archive-source-line"><span>{{ sourceName(proof) }} · {{ sourceLocationSummary(proof.location) }}</span><button type="button" @click="emit('open-source',proof)">定位原文与上下文 <span aria-hidden="true">→</span></button></div>
      </template>
      <details v-if="row.sources.length>1" class="archive-more-sources"><summary>其他原文依据（{{ row.sources.length-1 }} 条）</summary><div v-for="(proof,i) in row.sources.slice(1)" :key="proof.chunk_id || i"><blockquote class="archive-excerpt">{{ proof.excerpt }}</blockquote><div class="archive-source-line"><span>{{ sourceName(proof) }} · {{ sourceLocationSummary(proof.location) }}</span><button type="button" @click="emit('open-source',proof)">定位原文与上下文 <span aria-hidden="true">→</span></button></div></div></details>
      <section v-if="row.related_drawings?.length" class="archive-drawings" aria-label="关联绘图附件">
        <h4>绘图附件 <span>{{ row.related_drawings.length }} 份 · 按所编号关联</span></h4>
        <div v-for="drawing in row.related_drawings" :key="drawing.file_id" class="archive-source-line"><span>{{ sourceName(drawing) }}<small>{{ drawing.scope }}</small></span><button type="button" @click="emit('open-source',drawing)">查看附件 <span aria-hidden="true">→</span></button></div>
      </section>
    </article>
    <div v-if="result.results.length>REFERENCE_PREVIEW_COUNT" class="archive-expand"><el-button :aria-expanded="expanded" @click="expanded=!expanded">{{ expanded ? '收起为前 '+REFERENCE_PREVIEW_COUNT+' 件' : '展开本批其余 '+(result.results.length-REFERENCE_PREVIEW_COUNT)+' 件' }}</el-button></div>
    <p v-if="result.has_more" class="archive-scope">还有未返回的候选；本批最多展示 {{ result.results.length }} 件。可按代理师、领域或补充技术描述缩小范围。</p>
    <details class="archive-method"><summary>检索方式与范围</summary><p>{{ searchMethod }} · {{ result.note }}</p><p>{{ result.meaning }}</p></details>
  </section>
</template>

<script setup>
import {computed,ref,watch} from 'vue'
import {ElMessage} from 'element-plus'
import {REFERENCE_PREVIEW_COUNT,highlightSegments,archiveReferenceList} from '../archivePresentation'
import {sourceName,sourceLocationSummary} from '../sourcePresentation'
const props=defineProps({result:{type:Object,required:true}})
const searchMethod=computed(()=>({
  'BM25＋技术同义词':'按关键词与技术近义词检索',
  '本地向量＋BM25＋技术同义词':'按内容相似度、关键词与技术近义词检索',
})[props.result.strategy] || '按相关内容检索')
const emit=defineEmits(['open-case','open-source'])
const expanded=ref(false),copying=ref(false)
const visibleRows=computed(()=>expanded.value ? props.result.results : props.result.results.slice(0,REFERENCE_PREVIEW_COUNT))
watch(()=>props.result,()=>{expanded.value=false})
async function copyList(){
  copying.value=true
  try{await navigator.clipboard.writeText(archiveReferenceList(props.result,visibleRows.value));ElMessage.success('已复制案号、原文与来源，引用前请核对')}
  catch{ElMessage.error('复制失败，请检查浏览器剪贴板权限')}
  finally{copying.value=false}
}
</script>

<style scoped>
.archive-results{font-size:14px;color:var(--text)}
.archive-result-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:20px}
.archive-result-toolbar>div{display:flex;align-items:baseline;gap:12px}.archive-result-toolbar strong{font-size:16px;font-weight:600}.archive-result-toolbar span{color:var(--text-secondary);font-size:13px;font-variant-numeric:tabular-nums}
.archive-scope{font-size:13px;line-height:1.7;color:var(--text-secondary);margin:10px 0 14px}
.archive-result{padding:18px 0;border-top:1px solid var(--border);min-width:0}
.archive-case-heading{display:flex;gap:12px;align-items:flex-start}.archive-case-heading>div{min-width:0;flex:1}
.archive-rank{font-size:13px;color:var(--text-secondary);background:var(--bg);border:1px solid var(--border);width:28px;line-height:28px;text-align:center;border-radius:6px;flex:none}
.archive-case-link{font:inherit;font-size:15px;font-weight:600;color:var(--text);text-align:left;border:0;background:none;padding:2px 0;cursor:pointer;line-height:1.6;overflow-wrap:anywhere}
.archive-case-link:hover{color:var(--primary)}.archive-case-heading p{margin:4px 0 0;color:var(--text-secondary);font-size:13px;line-height:1.7}
.archive-match{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px;line-height:1.7;margin:12px 0 8px;font-size:13px}.archive-match>span{color:var(--text-secondary)}
.archive-excerpt{border-inline-start:2px solid var(--border);background:var(--bg);padding:12px 14px;margin:0;white-space:pre-wrap;line-height:1.85;overflow-wrap:anywhere;font-size:14px}
mark{background:var(--el-color-primary-light-9);color:var(--text);font-weight:600;border-radius:2px;padding:1px 0}
.archive-source-line{display:flex;align-items:baseline;justify-content:space-between;gap:8px 16px;margin-top:9px;flex-wrap:wrap;font-size:12px;line-height:1.7}.archive-source-line>span{color:var(--text-secondary);overflow-wrap:anywhere;flex:1;min-width:180px}
.archive-source-line button{font:inherit;font-size:13px;color:var(--primary);padding:2px 0;border:0;background:none;cursor:pointer;text-align:left}.archive-source-line button:hover{text-decoration:underline;text-underline-offset:3px}
button:focus-visible,summary:focus-visible{outline:2px solid var(--primary);outline-offset:3px;border-radius:3px}
.archive-more-sources{margin-top:12px;color:var(--text-secondary);font-size:13px}.archive-more-sources summary,.archive-method summary{cursor:pointer;line-height:1.7}.archive-more-sources>div{margin-top:12px;color:var(--text)}
.archive-expand{display:flex;justify-content:center;border-top:1px solid var(--border);padding:14px 0}
.archive-drawings{margin-top:16px;padding-top:12px;border-top:1px solid var(--border)}.archive-drawings h4{font-size:13px;font-weight:500;margin:0;line-height:1.7}.archive-drawings h4>span{font-size:12px;font-weight:400;color:var(--text-secondary);margin-left:8px}.archive-drawings small{display:block;font-size:12px;margin-top:2px}
.archive-method{font-size:12px;color:var(--text-secondary);line-height:1.7;margin-top:12px}.archive-method p{margin:8px 0 0}
@media(max-width:640px){.archive-result-toolbar{align-items:flex-start}.archive-result-toolbar>.el-button{margin-left:0}.archive-source-line{align-items:flex-start}.archive-source-line>span{flex-basis:100%}.archive-case-link{font-size:14px}}
</style>
