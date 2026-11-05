<template>
  <el-popover v-model:visible="visible" :teleported="teleported" placement="top" :width="320" trigger="click">
    <template #reference>
      <button type="button" class="source-trigger" :class="{online: normalized.includes('web_search')}" :disabled="disabled" :aria-expanded="visible" aria-label="选择可用数据源">
        <span class="source-label">数据源</span><span>{{ sourceSummary(normalized) }}</span><el-icon><ArrowDown /></el-icon>
      </button>
    </template>
    <section class="source-picker" aria-label="本次可用数据源">
      <h3>可用来源</h3>
      <el-checkbox-group :model-value="normalized" aria-label="数据源选项" @update:model-value="emit('update:modelValue', $event)">
        <div v-for="source in SOURCE_OPTIONS" :key="source.id" class="source-option">
          <el-checkbox :value="source.id" :disabled="disabled || (source.id === 'web_search' && !webAvailable && !normalized.includes('web_search'))">{{ source.label }}</el-checkbox><p>{{ source.id === 'web_search' && !webAvailable ? webReason : source.detail }}</p>
        </div>
      </el-checkbox-group>
      <p class="source-note">未勾选时，仅使用问题与附件。<template v-if="rememberNotice">联网仅对本次任务生效。</template></p>
      <el-button size="small" text @click="emit('update:modelValue', [...DEFAULT_SOURCES])">恢复默认：内部来源，关闭联网</el-button>
    </section>
  </el-popover>
</template>
<script setup>
import {computed, onMounted, ref, watch} from 'vue'
import {ArrowDown} from '@element-plus/icons-vue'
import {DEFAULT_SOURCES, SOURCE_OPTIONS, normalizeSources, sourceSummary} from '../sourceSelection'
const props=defineProps({modelValue:Array, disabled:Boolean, rememberNotice:Boolean, teleported:{type:Boolean,default:true}})
const emit=defineEmits(['update:modelValue'])
const visible=ref(false)
const normalized=computed(()=>normalizeSources(props.modelValue))
const webAvailable=ref(false),webReason=ref('正在确认联网检索可用性')
async function checkAvailability(){
  webAvailable.value=false
  try{
    const response=await fetch('/api/task-sources')
    if(!response.ok)throw Error('unavailable')
    const options=await response.json()
    webAvailable.value=options.web_search?.available === true
    webReason.value=options.web_search?.reason || '联网检索暂不可用'
  }catch{webReason.value='无法确认联网可用性，暂时保持关闭；可刷新重试。'}
}
onMounted(checkAvailability)
watch(visible,value=>{if(value)checkAvailability()})
watch(()=>props.disabled,value=>{if(value)visible.value=false})
</script>
<style scoped>
.source-trigger{display:inline-flex;align-items:center;gap:7px;height:32px;padding:0 10px;border:1px solid var(--border);border-radius:7px;background:var(--bg);color:var(--text-secondary);font:inherit;font-size:13px;cursor:pointer;white-space:nowrap}
.source-trigger .el-icon{font-size:11px;margin-left:1px}.source-label{font-weight:600}.source-trigger.online{border-color:var(--primary);color:var(--primary);background:var(--primary-soft)}.source-trigger:focus-visible{outline:2px solid var(--primary);outline-offset:2px}.source-trigger:disabled{opacity:.6;cursor:default}
.source-picker h3{font-size:14px;margin:0 0 5px}.source-picker p{font-size:12px;color:var(--el-text-color-secondary);line-height:1.6;margin:0 0 10px}.source-option{padding:5px 0;border-bottom:1px solid var(--el-border-color-lighter)}.source-option p{margin:0 0 7px 24px}.source-option .el-checkbox{height:28px}.source-note{margin-top:10px!important}.source-picker .el-button{padding:6px 0;font-size:12px}
</style>
