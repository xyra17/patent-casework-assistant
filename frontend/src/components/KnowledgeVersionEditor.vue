<template>
  <section class="version-editor" aria-label="版本与适用信息">
    <header><div><h2>版本与适用信息</h2><p>{{ document.name }}</p></div><el-button :disabled="saving" @click="emit('close')">关闭</el-button></header>
    <p>新旧版请分别添加为独立文档。同一制度使用相同标识；空的组织、地区和业务范围表示不限。失效日期当日不再适用。</p>
    <el-form label-position="top" @submit.prevent="save">
      <div class="fields">
        <el-form-item label="资料类型"><el-select v-model="form.kind"><el-option label="参考资料" value="reference"/><el-option label="制度规则" value="regulation"/></el-select></el-form-item>
        <el-form-item label="资料标识"><el-input v-model="form.document_id" maxlength="200" placeholder="同一资料的不同版本使用相同标识"/></el-form-item>
        <el-form-item label="版本名称"><el-input v-model="form.version_label" maxlength="100" placeholder="例如：2026修订版"/></el-form-item>
        <el-form-item label="发布日期"><el-date-picker v-model="form.published_on" type="date" value-format="YYYY-MM-DD"/></el-form-item>
        <el-form-item label="生效日期"><el-date-picker v-model="form.effective_from" type="date" value-format="YYYY-MM-DD"/></el-form-item>
        <el-form-item label="失效日期"><el-date-picker v-model="form.effective_until" type="date" value-format="YYYY-MM-DD"/></el-form-item>
        <el-form-item v-for="field in rangeFields" :key="field.key" :label="field.label"><el-select v-model="form[field.key]" multiple filterable allow-create default-first-option placeholder="输入名称后按回车"><el-option v-for="value in form[field.key]" :key="value" :label="value" :value="value"/></el-select></el-form-item>
      </div>
      <el-form-item label="适用说明"><el-input v-model="form.scope_note" type="textarea" maxlength="1000" placeholder="补充适用条件；文字说明不会自动转成筛选条件"/></el-form-item>
      <div class="relation-heading"><h3>与其他资料的关系</h3><el-button size="small" :disabled="form.relations.length>=50" @click="addRelation">添加关系</el-button></div>
      <p>关系由你核对确认。整份替代会在适用查询中排除目标版本；局部修订保留双方并标记待核实。关系依据须摘录本文件原文。</p>
      <fieldset v-for="(rel,index) in form.relations" :key="index">
        <legend>关系 {{ index+1 }}</legend>
        <div class="fields">
          <el-form-item label="关系类型"><el-select v-model="rel.kind"><el-option label="整份替代" value="replaces"/><el-option label="局部修订" value="amends"/><el-option label="补充" value="supplements"/><el-option label="引用" value="references"/></el-select></el-form-item>
          <el-form-item label="目标文档"><el-select v-model="rel.target" filterable @change="bindTarget(rel)"><el-option v-for="doc in targets" :key="doc.name" :label="`${doc.name} · ${doc.version_info?.version_label || '未命名'}`" :value="doc.name"/></el-select></el-form-item>
          <el-form-item label="本文件条款定位"><el-input v-model="rel.source_clause" maxlength="400" placeholder="局部修订必填"/></el-form-item>
          <el-form-item label="目标文件条款定位"><el-input v-model="rel.target_clause" maxlength="400" placeholder="局部修订必填"/></el-form-item>
        </div>
        <el-form-item label="关系依据原文"><el-input v-model="rel.basis" type="textarea" maxlength="2000"/></el-form-item>
        <el-button size="small" type="danger" plain @click="form.relations.splice(index,1)">移除此关系</el-button>
      </fieldset>
      <el-checkbox v-model="form.confirmed">我已核对原文、版本、适用范围及关系；信息不明时请取消勾选</el-checkbox>
      <p v-if="error" role="alert" class="error">{{ error }}</p>
      <div class="footer"><el-button type="primary" native-type="submit" :loading="saving">保存版本信息</el-button><span>保存后需更新索引，已发布索引保持原有状态。</span></div>
    </el-form>
  </section>
</template>
<script setup>
import {computed,ref} from 'vue'
import {ElDatePicker} from 'element-plus'
import {saveKnowledgeVersion} from '../api'
const props=defineProps({document:{type:Object,required:true},documents:{type:Array,required:true}})
const emit=defineEmits(['close','saved'])
const form=ref(JSON.parse(JSON.stringify(props.document.version_info)))
const saving=ref(false), error=ref('')
const rangeFields=[{key:'organizations',label:'适用组织'},{key:'regions',label:'适用地区'},{key:'businesses',label:'适用业务'}]
const targets=computed(()=>props.documents.filter(d=>d.name!==props.document.name))
function addRelation(){form.value.relations.push({kind:'references',target:'',target_revision:'',basis:'',source_clause:'',target_clause:''})}
function bindTarget(rel){rel.target_revision=targets.value.find(d=>d.name===rel.target)?.revision || ''}
async function save(){
  saving.value=true;error.value=''
  try{await saveKnowledgeVersion(props.document.name,{expected_revision:props.document.revision,metadata_token:props.document.metadata_token,info:form.value});emit('saved')}
  catch(e){error.value=e.message}
  finally{saving.value=false}
}
</script>
<style scoped>
.version-editor { margin-top:24px; padding:20px; border:1px solid var(--border); border-radius:8px; background:var(--bg); }
header,.relation-heading,.footer { display:flex; justify-content:space-between; align-items:center; gap:12px; }
h2 { margin:0 0 6px; font-size:16px; } h3 { font-size:14px; }
p,.footer span { font-size:12px; line-height:1.7; color:var(--text-secondary); overflow-wrap:anywhere; }
.fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:0 16px; }
.el-form { margin-top:16px; } .el-select,:deep(.el-date-editor) { width:100%; }
fieldset { border:1px solid var(--border); margin:14px 0; padding:14px; border-radius:6px; min-width:0; }
legend { font-size:12px; color:var(--text-secondary); }
.footer { justify-content:flex-start; flex-wrap:wrap; margin-top:16px; }
.error { color:var(--danger-text); } :deep(.el-checkbox) { height:auto; white-space:normal; }
</style>
