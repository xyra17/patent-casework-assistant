<template>
  <ul class="directory-branches" :aria-label="label">
    <li v-for="node in nodes" :key="node.key" :class="{'directory-branch':true,'empty-directory':node.type==='folder' && !node.count}">
      <details v-if="node.type==='folder'" open>
        <summary><el-icon class="caret"><ArrowRight /></el-icon><el-icon class="folder-icon"><FolderOpened /></el-icon><span class="directory-name">{{ node.label }}/</span><span class="directory-count">（{{ node.count }}）</span></summary>
        <ArchiveDirectoryTree v-if="node.children.length" :nodes="node.children" :label="node.label" @open="emit('open',$event)" />
      </details>
      <div v-else class="tree-file">
        <button type="button" class="tree-file-open" :aria-label="`查看原文：${node.label}`" @click="emit('open',node.document)"><el-icon><Document /></el-icon><span>{{ node.label }}</span></button>
        <span class="file-state" :class="{failed:['error','failed'].includes(node.document.state)}">{{ stateLabel(node.document.state) }}</span>
        <p v-if="node.document.error" class="file-error">{{ node.document.error }}</p>
      </div>
    </li>
  </ul>
</template>
<script setup>
import {ArrowRight,Document,FolderOpened} from '@element-plus/icons-vue'
defineProps({nodes:{type:Array,required:true},label:{type:String,default:'案件目录树'}})
const emit=defineEmits(['open'])
const stateLabel=state=>({parsed:'已解析',inventory:'仅登记附件',error:'解析失败',failed:'解析失败',unsupported:'未解析'}[state] || state)
</script>
<style scoped>
.directory-branches{list-style:none;padding:0 0 0 22px;margin:0 0 0 9px;border-left:1px solid var(--border)}.directory-branch{position:relative;padding:2px 0}.directory-branch::before{content:'';position:absolute;left:-22px;top:20px;width:18px;border-top:1px solid var(--border)}summary{list-style:none;display:flex;align-items:center;gap:7px;min-height:38px;cursor:pointer;font-size:14px;line-height:1.6;border-radius:5px;padding-right:8px}summary::-webkit-details-marker{display:none}summary:hover{background:var(--bg)}summary:focus-visible,.tree-file-open:focus-visible{outline:2px solid var(--primary);outline-offset:2px}.caret{font-size:11px;color:var(--text-secondary);transition:transform .15s}.folder-icon{color:var(--primary);font-size:17px;flex-shrink:0}details[open]>summary .caret{transform:rotate(90deg)}.directory-name{overflow-wrap:anywhere;font-weight:600}.directory-count{color:var(--text-secondary);font-size:12px;white-space:nowrap}.empty-directory .directory-name{font-weight:500}.tree-file{display:flex;align-items:center;gap:9px;flex-wrap:wrap;min-height:36px;padding:2px 6px}.tree-file-open{font:inherit;font-size:13px;background:none;border:0;color:var(--text-primary);padding:5px 0;display:flex;align-items:center;gap:9px;text-align:left;cursor:pointer;min-width:0}.tree-file-open span{overflow-wrap:anywhere}.tree-file-open .el-icon{color:var(--text-secondary);flex-shrink:0}.tree-file-open:hover{color:var(--primary)}.file-state{font-size:11px;color:var(--text-secondary);margin-left:auto;white-space:nowrap}.failed,.file-error{color:var(--danger-text)}.file-error{width:100%;margin:0 0 5px 24px;font-size:12px;overflow-wrap:anywhere}@media(prefers-reduced-motion:reduce){.caret{transition:none}}
</style>
