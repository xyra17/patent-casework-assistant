<template>
  <div class="archive-panel">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="archive-layout">
      <aside class="case-list" aria-label="案件文件夹">
        <form class="archive-search" @submit.prevent="loadPage(0)"><el-input v-model="query" clearable aria-label="搜索案件档案" placeholder="所号、名称、客户或代理师" /><el-button native-type="submit" :loading="loading">查找</el-button></form>
        <div class="archive-year" v-if="domain"><el-select v-model="year" placeholder="全部年份" aria-label="案件年份" @change="loadPage(0)"><el-option label="全部年份" value="" /><el-option v-for="item in selectedYears" :key="item.year" :label="`${item.year} 年（${item.count}）`" :value="item.year" /></el-select></div>
        <div class="case-list-scroll" v-loading="loading">
          <button v-for="item in catalog.rows" :key="item.case_key" type="button" class="case-folder" :class="{selected:selectedKey===item.case_key}" :aria-pressed="selectedKey===item.case_key" @click="openCase(item.case_key)"><span><strong>{{ item.case_id }}</strong><small>{{ item.file_count }} 份文件</small></span><p>{{ item.title || '名称未登记' }}</p></button>
          <el-empty v-if="!loading && !catalog.rows.length" description="没有匹配的案件" :image-size="45" />
        </div>
        <div class="archive-pages"><span>{{ catalog.total }} 件 · 第 {{ Math.floor(offset/20)+1 }} 页</span><el-button size="small" :disabled="loading || offset===0" @click="loadPage(offset-20)">上一页</el-button><el-button size="small" :disabled="loading || offset+20>=catalog.total" @click="loadPage(offset+20)">下一页</el-button></div>
        <el-button v-if="catalog.unassigned_count" class="unassigned" @click="openUnassigned">未关联案件的文件（{{ catalog.unassigned_count }}）</el-button>
      </aside>
      <section class="case-folders" v-loading="detailLoading" aria-label="案件分类文件夹">
        <template v-if="detail">
          <header class="archive-case-heading"><div class="archive-breadcrumb">{{ detail.case.technical_domain || '领域未登记' }} / {{ detail.case.year || '年份待核实' }} / {{ detail.case.case_id }}</div><h2>{{ detail.case.title || detail.case.case_id }}</h2><small>{{ detail.case.owner_name || '代理师未登记' }}<template v-if="detail.case.client_alias"> · {{ detail.case.client_alias }}</template></small></header>
          <div class="tree-root"><el-icon><FolderOpened /></el-icon><strong>{{ detail.case.case_id }}/</strong><span>{{ fileTree.reduce((n,f)=>n+f.count,0) }} 份文件</span></div>
          <ArchiveDirectoryTree :key="detail.case.case_key" :nodes="fileTree" @open="doc=>source.open({file_id:doc.id})" />
        </template>
        <el-empty v-else-if="!detailLoading" description="选择案件，查看分类资料" :image-size="65" />
      </section>
    </div>
    <CaseDocumentDrawer ref="source" />
    <el-dialog v-model="unassignedVisible" title="未关联案件的文件" width="min(760px,94vw)"><p class="unassigned-note">这些文件未能关联到当前案件，请在原件中核对所号后重新同步。</p><div v-for="doc in unassigned" :key="doc.id" class="archive-file"><span>{{ doc.relative_path }}</span><el-button link @click="source.open({file_id:doc.id})">查看文件</el-button></div></el-dialog>
  </div>
</template>
<script setup>
import {computed,onMounted,onUnmounted,ref} from 'vue'
import {FolderOpened} from '@element-plus/icons-vue'
import {casework} from '../caseworkApi'
import CaseDocumentDrawer from './CaseDocumentDrawer.vue'
import ArchiveDirectoryTree from './ArchiveDirectoryTree.vue'
import {caseArchiveTree} from '../caseArchiveTree'
const catalog=ref({rows:[],total:0,case_count:0,file_count:0,unassigned_count:0}),query=ref(''),offset=ref(0),selectedKey=ref(''),detail=ref(null),loading=ref(false),detailLoading=ref(false),error=ref(''),source=ref(null),unassignedVisible=ref(false),unassigned=ref([])
const domain=ref(''),year=ref('')
const selectedYears=computed(()=>catalog.value.domains?.find(item=>item.domain===domain.value)?.years || [])
function chooseDomain(value){domain.value=value;year.value='';loadPage(0)}
const fileTree=computed(()=>caseArchiveTree(detail.value?.folders))
let pageRequest=0,detailRequest=0
async function loadPage(start){const request=++pageRequest;loading.value=true;error.value='';try{const value=await casework('/archive',{params:{query:query.value,offset:start,limit:20,domain:domain.value,year:year.value}});if(request!==pageRequest)return;catalog.value=value;offset.value=value.offset;const chosen=value.rows.find(item=>item.case_key===selectedKey.value)||value.rows[0];if(chosen)await openCase(chosen.case_key);else{++detailRequest;selectedKey.value='';detail.value=null;detailLoading.value=false}}catch(e){if(request===pageRequest)error.value=e.message}finally{if(request===pageRequest)loading.value=false}}
async function openCase(key){const request=++detailRequest;selectedKey.value=key;detail.value=null;detailLoading.value=true;error.value='';try{const value=await casework('/archive/'+encodeURIComponent(key));if(request!==detailRequest)return;detail.value=value}catch(e){if(request===detailRequest)error.value=e.message}finally{if(request===detailRequest)detailLoading.value=false}}
async function openUnassigned(){try{unassigned.value=(await casework('/archive/unassigned')).documents;unassignedVisible.value=true}catch(e){error.value=e.message}}
defineExpose({catalog,loading,domain,chooseDomain,refresh:()=>loadPage(offset.value)})
onMounted(()=>loadPage(0));onUnmounted(()=>{pageRequest++;detailRequest++})
</script>
<style scoped>
.archive-year{padding:0 12px 12px}.archive-year .el-select{width:100%}.archive-breadcrumb{font-size:12px;color:var(--text-secondary);margin-bottom:10px}

.tree-root{display:flex;align-items:center;gap:9px;padding:10px 0;font-size:15px}.tree-root .el-icon{color:var(--primary);font-size:18px}.tree-root span{font-size:12px;font-weight:400;color:var(--text-secondary);margin-left:auto}

.archive-panel{height:100%;min-height:0;display:flex;flex-direction:column;gap:12px}.archive-layout{flex:1;min-height:0;display:grid;grid-template-columns:280px minmax(0,1fr);border:1px solid var(--border);border-radius:10px;overflow:hidden}.case-list{display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--border);background:var(--bg)}.archive-search{display:flex;gap:6px;padding:12px}.case-list-scroll{flex:1;min-height:0;overflow:auto}.case-folder{display:block;width:100%;padding:14px;text-align:left;background:transparent;border:0;border-bottom:1px solid var(--border);color:var(--text-primary);font:inherit;cursor:pointer}.case-folder>span{display:flex;justify-content:space-between;align-items:center;gap:6px}.case-folder strong{font-size:14px}.case-folder small{font-size:12px;color:var(--text-secondary);white-space:nowrap}.case-folder p{font-size:13px;line-height:1.6;margin:7px 0 0;overflow-wrap:anywhere}.case-folder:hover{background:var(--card-bg)}.case-folder.selected{background:color-mix(in srgb,var(--primary) 9%,var(--card-bg));box-shadow:inset 3px 0 var(--primary)}.case-folder:focus-visible{outline:2px solid var(--primary);outline-offset:-3px}.archive-pages{display:flex;align-items:center;gap:5px;padding:12px 8px;border-top:1px solid var(--border);font-size:12px;flex-wrap:wrap}.archive-pages .el-button+.el-button{margin:0}.archive-pages span{margin-right:auto}.case-folders{padding:18px 22px;overflow:auto;min-height:0}.archive-case-heading{margin-bottom:12px}.archive-case-heading h2{font-size:16px;line-height:1.6;margin:0 0 6px}.archive-case-heading small{color:var(--text-secondary)}.archive-file{display:flex;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid var(--border);font-size:14px}.archive-file>div{min-width:0;flex:1}.archive-file strong{display:block;font-weight:500;overflow-wrap:anywhere}.archive-file small{display:block;color:var(--text-secondary);margin-top:5px;font-size:12px}.archive-file>.el-button{margin-left:auto;flex-shrink:0}.unassigned-note{color:var(--text-secondary);font-size:13px}.file-error{font-size:12px;color:var(--el-color-danger);overflow-wrap:anywhere}.unassigned{margin:8px;white-space:normal}@media(max-width:800px){.archive-layout{grid-template-columns:220px minmax(0,1fr)}.case-folders{padding:14px}}@media(max-width:600px){.archive-layout{display:flex;flex-direction:column;overflow:auto}.case-list{min-height:240px;max-height:300px;border-right:0;border-bottom:1px solid var(--border)}.case-folders{min-height:320px;overflow:visible}}
</style>
