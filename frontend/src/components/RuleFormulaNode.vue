<template>
  <div class="formula-node">
    <el-select :model-value="node.op" @update:model-value="change" aria-label="公式操作">
      <el-option v-for="(label,key) in operations" :key="key" :value="key" :label="label" />
    </el-select>
    <el-select v-if="['field','related_sum'].includes(node.op)" v-model="node.field" filterable aria-label="公式字段"><el-option v-for="f in availableFields" :key="f.key" :value="f.key" :label="f.label" /></el-select>
    <el-input-number v-if="node.op==='number'" v-model="node.value" aria-label="公式数值" :min="-1e12" :max="1e12" />
    <template v-if="['add','sub','mul','div'].includes(node.op)"><RuleFormulaNode v-for="(_,i) in node.args" :key="i" v-model="node.args[i]" :fields="fields" :base-table="baseTable" :depth="depth+1" /></template>
    <div v-if="node.op==='related_sum'" class="related-filters">
      <p>仅汇总关联明细；无匹配记录按零处理。</p>
      <div v-for="(f,i) in node.filters" :key="i" class="related-row">
        <el-select v-model="f.field" filterable aria-label="明细过滤字段"><el-option v-for="field in fields.filter(x=>x.table===node.field?.split('.')[0])" :key="field.key" :label="field.label" :value="field.key" /></el-select>
        <el-select v-model="f.op" aria-label="明细过滤操作" @change="f.value=f.op==='in' ? (Array.isArray(f.value)?f.value:[f.value]) : (Array.isArray(f.value)?f.value.join(','):f.value)"><el-option v-for="(label,key) in operators" :key="key" :label="label" :value="key"/></el-select>
        <el-input :model-value="Array.isArray(f.value)?f.value.join(','):String(f.value ?? '')" @update:model-value="f.value=f.op==='in' ? $event.split(/[,，]/).map(v=>v.trim()).filter(Boolean) : $event" aria-label="明细过滤值" placeholder="多个值用逗号分隔"/><el-button text @click="node.filters.splice(i,1)">移除</el-button>
      </div>
      <el-button size="small" :disabled="node.filters.length>=8" @click="node.filters.push({field:'',op:'eq',value:''})">添加明细过滤</el-button>
    </div>
  </div>
</template>
<script setup>
import {computed} from 'vue'
const props=defineProps({modelValue:Object,fields:Array,baseTable:String,depth:{type:Number,default:0}})
const operators={eq:'等于',ne:'不等于',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于',like:'匹配',in:'属于多个值'}
const emit=defineEmits(['update:modelValue'])
const node=computed(()=>props.modelValue)
const operations=computed(()=>props.depth>=6?{field:'本表数值字段',number:'固定数值',related_sum:'关联明细求和'}:{field:'本表数值字段',number:'固定数值',add:'相加',sub:'相减',mul:'相乘',div:'相除',related_sum:'关联明细求和'})
const availableFields=computed(()=>props.fields.filter(f=>(f.role==='metric'||['number','integer','float','decimal'].includes(f.type))&&(node.value.op==='field'?f.table===props.baseTable:f.table!==props.baseTable)))
function change(op){emit('update:modelValue',['add','sub','mul','div'].includes(op)?{op,args:[{op:'field',field:''},{op:'number',value:0}]}:op==='number'?{op,value:0}:op==='related_sum'?{op,field:'',filters:[]}:{op,field:''})}
</script>
<style scoped>
.formula-node{display:grid;grid-template-columns:minmax(130px,180px) 1fr;gap:8px;padding:10px;border:1px solid var(--border);border-radius:6px;margin-top:8px}.formula-node>.formula-node,.related-filters{grid-column:1/-1}.related-filters p{font-size:13px;color:var(--text-secondary)}.related-row{display:grid;grid-template-columns:1fr 90px 1fr auto;gap:6px;margin-bottom:8px}@media(max-width:650px){.formula-node{grid-template-columns:1fr}.related-row{grid-template-columns:1fr 1fr}}
</style>
