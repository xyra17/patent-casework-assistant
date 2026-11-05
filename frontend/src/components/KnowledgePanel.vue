<template>
  <section class="knowledge-workspace glass-card" aria-label="知识库">
    <ModuleNavigation title="知识库" :target="sidebarTarget">
      <div class="nav-links">
        <button type="button" :class="{active:tab==='cases'}" :aria-current="tab==='cases'?'page':undefined" @click="tab='cases'"><el-icon aria-hidden="true"><FolderOpened /></el-icon>案件档案</button>
        <button type="button" :class="{active:tab==='documents'}" :aria-current="tab==='documents'?'page':undefined" @click="tab='documents'"><el-icon aria-hidden="true"><Document /></el-icon>公司文档</button>
      </div>
      <div v-if="tab==='cases' && archive?.catalog.domains?.length" class="nav-links nav-secondary" role="group" aria-label="案件领域">
        <button type="button" :class="{active:!archive.domain}" :aria-pressed="!archive.domain" @click="archive.chooseDomain('')"><el-icon aria-hidden="true"><Grid /></el-icon>全部领域<span class="nav-count">{{ archive.catalog.case_count }}</span></button>
        <button v-for="item in archive.catalog.domains" :key="item.domain" type="button" :class="{active:archive.domain===item.domain}" :aria-pressed="archive.domain===item.domain" @click="archive.chooseDomain(item.domain)"><el-icon aria-hidden="true"><FolderOpened /></el-icon><span class="nav-label">{{ item.directory }}</span><span class="nav-count">{{ item.count }}</span></button>
      </div>
      <div v-if="tab==='documents' && company?.catalog" class="nav-links nav-secondary" role="group" aria-label="公司文档分类">
        <button type="button" :class="{active:!company.group}" :aria-pressed="!company.group" @click="company.chooseGroup('')"><el-icon aria-hidden="true"><Grid /></el-icon>全部资料<span class="nav-count">{{ company.catalog.documents.length }}</span></button>
        <button v-for="item in company.groups" :key="item.label" type="button" :class="{active:company.group===item.label}" :aria-pressed="company.group===item.label" @click="company.chooseGroup(item.label)"><el-icon aria-hidden="true"><FolderOpened /></el-icon><span class="nav-label">{{ item.label }}</span><span class="nav-count">{{ item.count }}</span></button>
      </div>
    </ModuleNavigation>
    <header class="knowledge-header">
      <div class="knowledge-heading"><h1>知识库</h1><span class="current-view">/ {{ tab==='cases'?'案件档案':'公司文档' }}</span><span v-if="tab==='cases'" class="knowledge-count" role="status">{{ archive?.catalog.case_count ?? 0 }} 件案件 · {{ archive?.catalog.file_count ?? 0 }} 份文件</span></div>
      <el-button v-if="tab==='cases'" :loading="archive?.loading" :disabled="!archive" @click="archive?.refresh()">刷新档案</el-button>
    </header>
    <div v-show="tab==='cases'" class="knowledge-view"><CaseArchivePanel ref="archive" /></div>
    <div v-if="documentsVisited" v-show="tab==='documents'" class="knowledge-view"><CompanyDocumentsPanel ref="company" /></div>
  </section>
</template>
<script setup>
import {ref,watch,defineAsyncComponent} from 'vue'
import {Document,FolderOpened,Grid} from '@element-plus/icons-vue'
import CaseArchivePanel from './CaseArchivePanel.vue'
import ModuleNavigation from './ModuleNavigation.vue'
const CompanyDocumentsPanel=defineAsyncComponent(()=>import('./CompanyDocumentsPanel.vue'))
defineProps({sidebarTarget:{type:String,default:''}})
const tab=ref('cases'),archive=ref(null),company=ref(null),documentsVisited=ref(false)
watch(tab,value=>{if(value==='documents')documentsVisited.value=true})
</script>
<style scoped>
.knowledge-workspace{height:100%;min-height:0;display:flex;flex-direction:column;padding:16px 20px;overflow:hidden}
.knowledge-header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-shrink:0;margin-bottom:14px;min-height:32px}
.knowledge-heading{display:flex;align-items:baseline;gap:12px;min-width:0;flex-wrap:wrap}
.knowledge-heading h1{font-size:20px;line-height:28px;font-weight:600;margin:0}
.current-view{font-size:14px;color:var(--text-secondary)}
.knowledge-count{font-size:13px;line-height:20px;color:var(--text-secondary)}
.knowledge-header>.el-button{flex-shrink:0}
.knowledge-view{flex:1;min-height:0;overflow:auto}
@media(max-width:650px){.knowledge-workspace{padding:12px}.knowledge-heading{gap:2px 8px}.knowledge-count{font-size:12px;flex-basis:100%}.knowledge-header>.el-button{padding:8px 10px}}
</style>
