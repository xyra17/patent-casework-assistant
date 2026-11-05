<template>
  <section v-if="items.length" class="answer-sources" aria-label="查询结果来源">
    <h3>引用来源 <span>{{ items.length }}</span></h3>
    <ul>
      <li v-for="source in items" :key="source.id" class="source-row">
        <span class="source-id">{{ source.id }}</span>
        <div class="source-content">
          <div class="source-heading">
            <strong>{{ source.title || '未命名来源' }}</strong>
            <span v-if="source.name && source.name !== source.title" class="source-name">{{ source.name }}</span>
          </div>
          <details v-if="source.location || source.source || source.url" :key="`${sessionId}:${source.id}`" class="source-details">
            <summary :aria-label="`${source.id} 定位详情：${source.summary || '查看完整路径'}`"><span>{{ source.summary || '查看完整路径' }}</span></summary>
            <dl>
              <template v-if="source.source || source.url"><dt>来源路径</dt><dd>{{ source.source || source.url }}</dd></template>
              <template v-if="source.location"><dt>完整定位</dt><dd>{{ source.location }}</dd></template>
            </dl>
          </details>
        </div>
        <button type="button" class="source-open" :aria-label="`查看来源 ${source.id}：${source.title || '未命名来源'}`" @click="emit('show-evidence', source.id)">查看来源<span aria-hidden="true"> →</span></button>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { sourceName, sourceLocationSummary, evidenceTitle } from '../sourcePresentation.js'

const props = defineProps({ sources: { type: Array, default: () => [] }, sessionId: String })
const emit = defineEmits(['show-evidence'])
const items = computed(() => props.sources.map(source => ({
  ...source,
  title: evidenceTitle(source),
  name: sourceName(source),
  summary: sourceLocationSummary(source.location),
})))
</script>

<style scoped>
.answer-sources { padding-top: 14px; border-top: 1px solid var(--border); color: var(--text); }
h3 { display: flex; align-items: center; gap: 8px; margin: 0 0 6px; font-size: 14px; font-weight: 600; }
h3 span { font-size: 13px; font-weight: 400; color: var(--chart-muted); }
ul { margin: 0; padding: 0; list-style: none; }
.source-row { display: grid; grid-template-columns: 34px minmax(0, 1fr) auto; align-items: start; gap: 10px; padding: 11px 0; }
.source-row + .source-row { border-top: 1px solid var(--border); }
.source-id { margin-top: 1px; padding: 2px 0; border-radius: 4px; background: var(--bg); color: var(--chart-muted); font-size: 12px; font-weight: 600; line-height: 20px; text-align: center; }
.source-content { min-width: 0; }
.source-heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; line-height: 24px; overflow-wrap: anywhere; }
.source-heading strong { font-size: 14px; font-weight: 500; }
.source-name { font-size: 13px; color: var(--chart-muted); }
.source-details { margin-top: 2px; font-size: 13px; line-height: 22px; color: var(--chart-muted); }
summary { display: flex; align-items: baseline; gap: 6px; cursor: pointer; list-style: none; border-radius: 2px; }
summary::-webkit-details-marker { display: none; }
summary::before { content: '›'; flex: none; font-size: 16px; transition: transform .15s; }
details[open] summary::before { transform: rotate(90deg); }
summary span { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
summary:hover { color: var(--text); }
dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 5px 10px; margin: 7px 0 0; padding: 9px 11px; background: var(--bg); border-radius: 6px; font-size: 13px; }
dt { color: var(--chart-muted); }
dd { margin: 0; color: var(--text); overflow-wrap: anywhere; white-space: pre-wrap; }
.source-open { margin-top: 1px; padding: 2px 0 2px 8px; background: none; border: 0; border-radius: 2px; font: inherit; font-size: 13px; line-height: 22px; color: var(--primary); white-space: nowrap; cursor: pointer; }
.source-open:hover { text-decoration: underline; text-underline-offset: 3px; }
.source-open:focus-visible, summary:focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
@media (max-width: 640px) {
  .source-row { grid-template-columns: 30px minmax(0, 1fr) auto; gap: 7px; }
  .source-name { flex-basis: 100%; }
  .source-open span { display: none; }
  dl { grid-template-columns: minmax(0, 1fr); gap: 2px; }
  dd + dt { margin-top: 5px; }
}
@media (prefers-reduced-motion: reduce) { summary::before { transition: none; } }
</style>
