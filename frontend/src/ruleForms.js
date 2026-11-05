export const RULE_CATEGORIES = {business:'业务口径',term:'术语含义',preference:'回答偏好'}
export function rulePayload(rule) {
  return Object.fromEntries(['title','category','subject','content','match_mode','match_terms','aliases','scope_note','binding','active'].map(key => [key,rule[key]]))
}
export function blankRule() {
  return {title:'',category:'preference',subject:'',content:'',match_mode:'keywords',match_terms:[],aliases:[],scope_note:'本项目后续匹配关键词的任务',binding:null,active:false}
}

export function bindingPayload(binding) {
 const result=JSON.parse(JSON.stringify(binding))
 result.field ||= null;result.aggregation ||= null;result.time_field ||= null
 result.filters=(result.filters||[]).map(({field,op,valueText,value})=>({field,op,value:valueText===undefined?value:op==='in'?valueText.split(/[,，]/).map(v=>v.trim()).filter(Boolean):valueText}))
 return result
}
export function formulaLabel(node) {
 if(!node)return '沿用指标定义'
 if(node.op==='number')return String(node.value)
 if(node.op==='field')return node.field || '待选字段'
 if(node.op==='related_sum')return `关联求和(${node.field || '待选字段'}${node.filters?.length?'；含明细过滤':''})`
 return `(${formulaLabel(node.args?.[0])} ${{add:'+',sub:'−',mul:'×',div:'÷'}[node.op] || '?'} ${formulaLabel(node.args?.[1])})`
}
