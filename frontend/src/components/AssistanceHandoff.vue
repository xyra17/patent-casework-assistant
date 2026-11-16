<template>
  <section class="handoff" aria-label="证据与模型判断">
    <header><h3>证据与模型判断</h3><span>{{ packet.manual_checks.length }} 项待核对</span></header>
    <p class="handoff-intro">对照原文核对模型分析，确认后再用于专业判断。</p>
    <details>
      <summary>查看原文证据 · {{ packet.sources.length }} 条</summary>
      <article v-for="source in packet.sources" :key="source.id" class="handoff-source">
        <button type="button" @click="emit('show-evidence',source.id)">[{{ source.id }}] {{ source.case_id }} {{ source.title }}</button>
        <p>{{ source.source || source.title }}</p><p>位置：{{ location(source.location) }}</p>
        <blockquote>{{ source.excerpt || '请打开来源查看完整记录。' }}</blockquote>
      </article>
      <p v-if="packet.more_sources">另有 {{ packet.more_sources }} 条来源保存在执行记录中。</p>
    </details>
    <details>
      <summary>查看模型分析草稿 · 未确认</summary>
      <p>草稿可能包含下方列出的问题，不能直接作为已核验事实或专业结论。</p>
      <pre class="handoff-draft">{{ packet.model_draft.text }}</pre>
      <p v-if="packet.model_draft.truncated">此处仅展示前部，完整草稿已按哈希保存在执行记录中。</p>
    </details>
    <details v-if="packet.manual_checks.length" class="handoff-checks" :open="packet.objective_issues.length > 0">
      <summary>需要核对什么 · {{ packet.manual_checks.length }} 项</summary>
      <ul><li v-for="(check,index) in packet.manual_checks" :key="index"><span>{{ check.kind === 'model_review_finding' ? '自动审查意见（待复核）' : check.kind === 'objective_issue' ? '已发现的输出问题' : '待确认判断' }}</span>{{ check.item }}</li></ul>
    </details>
    <details>
      <summary>原因与处理建议</summary>
      <article v-for="item in packet.diagnoses" :key="item.layer" class="handoff-cause"><strong>{{ causeLabel(item.layer) }}</strong><p>{{ item.finding }}</p><p>{{ item.next_action }}</p></article>
    </details>
  </section>
</template>

<script setup>
defineProps({packet:{type:Object,required:true}})
const emit=defineEmits(['show-evidence'])
const location=value=>typeof value==='object' ? (value?.kind==='physical_text_lines' ? `文本第 ${value.first_line} 行起，共 ${value.line_count} 行` : value?.url || JSON.stringify(value)) : value || '位置尚未绑定'
const causeLabel=value=>({program_or_output:'程序与输出',evaluation:'评判标准与审查',evidence:'内容与检索',model_capability:'模型能力判断'})[value] || value
</script>

<style scoped>
.handoff{margin:20px 0;padding:18px 20px;border:1px solid var(--border);border-radius:10px;background:var(--card-bg);font-size:13px;line-height:1.75;color:var(--text)}
.handoff header{display:flex;align-items:baseline;gap:16px;justify-content:space-between}.handoff h3{font-size:15px;margin:0;font-weight:600}.handoff header>span,.handoff-intro{color:var(--chart-muted);font-size:12px}.handoff-intro{margin:6px 0 12px}
.handoff details{border-top:1px solid var(--border);padding:4px 0}.handoff summary{min-height:40px;display:list-item;align-content:center;cursor:pointer}.handoff summary:focus-visible,.handoff button:focus-visible{outline:2px solid var(--primary);outline-offset:3px}
.handoff-source,.handoff-cause{padding:12px 0;border-top:1px solid var(--border)}.handoff-source:first-of-type,.handoff-cause:first-of-type{border-top:0}.handoff-source button{font:inherit;color:var(--primary);background:none;border:0;padding:3px 0;text-align:left;cursor:pointer}.handoff p{margin:4px 0;overflow-wrap:anywhere}.handoff blockquote{margin:8px 0;padding:8px 12px;border-left:2px solid var(--border);white-space:pre-wrap;overflow-wrap:anywhere;color:var(--chart-muted)}
.handoff-draft{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;background:var(--bg);border-radius:6px;padding:12px;max-height:440px;overflow:auto}.handoff-checks ul{padding-left:20px;margin:0 0 12px}.handoff-checks li{margin:8px 0;overflow-wrap:anywhere}.handoff-checks li>span{display:block;font-size:12px;color:var(--chart-muted)}
@media(max-width:600px){.handoff{padding:14px}.handoff summary,.handoff-source button{min-height:44px}}
</style>
