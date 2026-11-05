<template>
  <div class="chart-card">
    <div class="chart-head">
      <h3 class="chart-title"><span v-if="position" class="chart-position">{{ position }}</span><span>{{ spec.title || '数据图表' }}</span></h3>
      <p class="chart-meta" :title="fieldSummary">
        {{ spec.reason || '系统推荐图表' }}
        <template v-if="spec.rowCount"> · {{ spec.rowCount }} 条结果</template>
        <template v-if="spec.truncated"> · 展示前 {{ spec.xAxis?.length || 40 }} 行</template>
        <span v-if="spec.sourceIds?.length" class="chart-sources"> · 来源 <template v-for="(sourceId, index) in spec.sourceIds" :key="sourceId"><span v-if="index" aria-hidden="true">、</span><button type="button" class="chart-source-link" :aria-label="`查看图表来源 ${sourceId}`" @click="emit('show-evidence', sourceId)">{{ sourceId }}</button></template></span>
      </p>
      <label class="chart-type-control"><span>图表类型</span><el-select v-model="chartType" size="small" class="type-select" title="切换图表类型" aria-label="切换图表类型">
        <el-option label="推荐" value="auto" />
        <el-option
          v-for="option in TYPE_OPTIONS"
          :key="option.value"
          :label="option.label"
          :value="option.value"
          :disabled="option.value === 'heatmap' && !spec.heatmap"
        />
      </el-select></label>
      <div class="chart-exports" role="group" aria-label="导出当前图表"><el-button size="small" @click="downloadPng" aria-label="下载当前图表 PNG" title="导出图表图片（PNG）">
        <el-icon aria-hidden="true"><Download /></el-icon><span>PNG</span>
      </el-button>
      <span class="chart-export-divider" aria-hidden="true"></span>
      <el-button size="small" @click="downloadCsv" aria-label="下载当前图表 CSV" title="导出图表数据（CSV）">
        <el-icon aria-hidden="true"><Document /></el-icon><span>CSV</span>
      </el-button></div>
    </div>
    <p v-if="spec.completeness" class="chart-meta">{{ evidenceScopeText(spec) }}</p>
    <div ref="chartEl" class="chart-canvas"></div>
  </div>
</template>

<script setup>
import { evidenceScopeText } from '../evidenceScope.js'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CHART_PALETTES, store } from '../store'

const TYPE_OPTIONS = [
  { value: 'bar', label: '柱状图' },
  { value: 'hbar', label: '条形图' },
  { value: 'line', label: '折线图' },
  { value: 'area', label: '面积图' },
  { value: 'stackedBar', label: '堆叠柱' },
  { value: 'pie', label: '饼图' },
  { value: 'donut', label: '环形图' },
  { value: 'scatter', label: '散点图' },
  { value: 'heatmap', label: '热力图' },
]

const props = defineProps({ spec: { type: Object, required: true }, position: { type: String, default: '' } })
const emit = defineEmits(['drilldown', 'show-evidence'])
const chartEl = ref(null)
const chartType = ref('auto')
const effectiveType = computed(() => (chartType.value === 'auto' ? (props.spec.type || 'bar') : chartType.value))
const fieldSummary = computed(() => {
  const fields = props.spec.fields || []
  return fields.length
    ? fields.map((field) => `${field.name}（${field.role}）`).join(' · ')
    : ''
})

function heatmapView() {
  const source = props.spec.heatmap
  if (!source) return null
  const xAxis = [...new Set((source.xAxis || []).map((v) => String(v).trim() || '—'))]
  const yAxis = [...new Set((source.yAxis || []).map((v) => String(v).trim() || '—'))]
  const data = (source.data || []).map((cell) => {
    const xv = String(source.xAxis?.[cell[0]] ?? '').trim() || '—'
    const yv = String(source.yAxis?.[cell[1]] ?? '').trim() || '—'
    return [xAxis.indexOf(xv), yAxis.indexOf(yv), cell[2]]
  }).filter((cell) => cell[0] >= 0 && cell[1] >= 0)
  return { ...source, xAxis, yAxis, data }
}
let renderVersion = 0
let disposed = false
let chart = null
let resizeObserver = null
let echartsModule = null

async function ensureEcharts() {
  if (echartsModule) return echartsModule
  const [core, charts, components, renderers] = await Promise.all([
    import('echarts/core'),
    import('echarts/charts'),
    import('echarts/components'),
    import('echarts/renderers'),
  ])
  core.use([
    charts.BarChart,
    charts.LineChart,
    charts.PieChart,
    charts.ScatterChart,
    charts.HeatmapChart,
    components.GridComponent,
    components.TooltipComponent,
    components.LegendComponent,
    components.DataZoomComponent,
    components.VisualMapComponent,
    renderers.CanvasRenderer,
  ])
  echartsModule = core
  return core
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

function alpha(hex, opacity) {
  const value = hex.replace('#', '')
  if (value.length !== 6) return hex
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${opacity})`
}

function verticalGradient(color) {
  return {
    type: 'linear',
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: alpha(color, 0.98) },
      { offset: 1, color: alpha(color, 0.45) },
    ],
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]))
}

function buildOption(type) {
  const textColor = cssVar('--chart-text', '#27364b')
  const muted = cssVar('--chart-muted', '#65748b')
  const border = cssVar('--chart-grid', '#e7edf4')
  const dark = store.theme === 'dark'
  const palette = (CHART_PALETTES[store.chartPalette] || CHART_PALETTES.nature).colors
  const xData = props.spec.xAxis || []
  const series = props.spec.series || []
  const isCategory = ['bar', 'hbar', 'line', 'area', 'stackedBar'].includes(type)
  const base = {
    backgroundColor: 'transparent',
    color: palette,
    animationDuration: 500,
    textStyle: { color: textColor, fontFamily: 'inherit' },
    tooltip: {
      trigger: isCategory ? 'axis' : 'item',
      axisPointer: { type: 'shadow' },
      backgroundColor: dark ? '#1f242e' : '#ffffff',
      borderColor: border,
      borderWidth: 1,
      textStyle: { color: textColor, fontSize: 12 },
    },
    legend: {
      top: 2,
      right: 4,
      type: 'scroll',
      textStyle: { color: muted, fontSize: 11 },
      itemWidth: 10,
      itemHeight: 7,
    },
    grid: { left: 50, right: 24, top: 38, bottom: 48, containLabel: true },
  }

  if (type === 'heatmap' && props.spec.heatmap) {
    const heatmap = heatmapView()
    const unit = series[0]?.unit || ''
    const values = (heatmap.data || []).map((cell) => Number(cell[2]) || 0)
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: 'item', formatter: (params) => `${escapeHtml(heatmap.xAxis[params.value[0]])} · ${escapeHtml(heatmap.yAxis[params.value[1]])}<br/>${escapeHtml(heatmap.metric)}：${escapeHtml(params.value[2])}${escapeHtml(unit)}` },
      grid: { left: 78, right: 28, top: 34, bottom: 58, containLabel: true },
      xAxis: {
        type: 'category', data: heatmap.xAxis,
        axisLabel: { color: muted, fontSize: 8.5, interval: 0, rotate: heatmap.xAxis.length > 5 ? 42 : 0 },
        axisLine: { lineStyle: { color: border } }, axisTick: { show: false },
      },
      yAxis: {
        type: 'category', data: heatmap.yAxis,
        axisLabel: { color: muted, fontSize: 10 },
        axisLine: { lineStyle: { color: border } }, axisTick: { show: false },
      },
      visualMap: {
        min: Math.min(...values, 0),
        max: Math.max(...values, 1),
        calculable: false,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        itemWidth: 10,
        itemHeight: 80,
        textStyle: { color: muted, fontSize: 9 },
        inRange: { color: [alpha(palette[0], 0.08), palette[0], palette[1] || palette[0]] },
      },
      series: [{
        name: heatmap.metric,
        type: 'heatmap',
        data: heatmap.data,
        label: { show: heatmap.data.length <= 20, color: textColor, fontSize: 9, formatter: (params) => `${params.value[2]}${unit}` },
        itemStyle: { borderColor: dark ? '#181c24' : '#ffffff', borderWidth: 2, borderRadius: 4 },
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,.18)' } },
      }],
    }
  }

  if (type === 'pie' || type === 'donut') {
    const first = series[0] || { name: '', data: [] }
    const values = first.data || []
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: 'item', formatter: '{b}<br/>{c}（{d}%）' },
      legend: { ...base.legend, bottom: 0, top: 'auto', left: 'center', itemGap: 8 },
      grid: undefined,
      series: [{
        name: first.name,
        type: 'pie',
        radius: type === 'donut' ? ['40%', '66%'] : ['0%', '64%'],
        center: ['50%', '43%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: dark ? '#181c24' : '#ffffff', borderWidth: 2, borderRadius: 5 },
        label: { show: true, position: 'inside', color: '#fff', fontSize: 9, fontWeight: 600, formatter: '{d}%' },
        labelLine: { show: false },
        data: xData.map((name, index) => ({ name, value: values[index] ?? 0 })),
      }],
    }
  }

  if (type === 'scatter') {
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: 'item', formatter: (params) => `${props.spec.xName || 'X'}：${params.value[0]}<br/>${props.spec.yName || params.seriesName}：${params.value[1]}` },
      grid: { left: 58, right: 28, top: 38, bottom: 52, containLabel: true },
      xAxis: {
        type: 'value',
        name: props.spec.xName || '',
        nameTextStyle: { color: muted, fontSize: 10 },
        axisLabel: { color: muted, fontSize: 10 },
        axisLine: { lineStyle: { color: border } },
        splitLine: { lineStyle: { color: border, type: 'dashed' } },
      },
      yAxis: {
        type: 'value',
        name: props.spec.yName || '',
        nameTextStyle: { color: muted, fontSize: 10 },
        axisLabel: { color: muted, fontSize: 10 },
        axisLine: { lineStyle: { color: border } },
        splitLine: { lineStyle: { color: border, type: 'dashed' } },
      },
      series: series.map((item, index) => ({
        name: item.name,
        type: 'scatter',
        data: xData.map((x, i) => [x, item.data?.[i] ?? 0]),
        symbolSize: 8,
        itemStyle: { color: palette[index % palette.length], opacity: 0.8, borderColor: dark ? '#181c24' : '#fff', borderWidth: 1 },
      })),
    }
  }

  const horizontal = type === 'hbar'
  const categoryAxis = {
    type: 'category',
    data: xData,
    inverse: horizontal,
    boundaryGap: type !== 'line' && type !== 'area',
    axisLabel: { color: muted, fontSize: 10, interval: horizontal ? 0 : 'auto', rotate: !horizontal && xData.length > 10 ? 30 : 0 },
    axisLine: { lineStyle: { color: border } },
    axisTick: { show: false },
  }
  const valueAxis = {
    type: 'value',
    axisLabel: { color: muted, fontSize: 10, formatter: (value) => `${value}${series[0]?.unit || ''}` },
    axisLine: { show: false },
    splitLine: { lineStyle: { color: border, type: 'dashed' } },
  }
  const categorySeries = series.map((item, index) => {
    const color = palette[index % palette.length]
    if (type === 'line' || type === 'area') {
      return {
        name: item.name,
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        data: item.data,
        lineStyle: { width: 3, color },
        itemStyle: { color, borderColor: dark ? '#181c24' : '#fff', borderWidth: 2 },
        areaStyle: type === 'area' ? {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: alpha(color, 0.32) },
              { offset: 1, color: alpha(color, 0.02) },
            ],
          },
        } : undefined,
      }
    }
    return {
      name: item.name,
      type: 'bar',
      stack: type === 'stackedBar' ? 'total' : undefined,
      barMaxWidth: horizontal ? 18 : 32,
      data: item.data,
      itemStyle: {
        color: verticalGradient(color),
        borderRadius: horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0],
      },
      emphasis: { itemStyle: { color } },
    }
  })
  return {
    ...base,
    grid: { left: horizontal ? 88 : 50, right: 24, top: 38, bottom: 48, containLabel: true },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? categoryAxis : valueAxis,
    dataZoom: xData.length > 14 ? [{ type: 'inside', start: 0, end: 100 }] : undefined,
    series: categorySeries,
  }
}

async function render() {
  const version = ++renderVersion
  await nextTick()
  if (!chartEl.value || disposed) return
  const core = await ensureEcharts()
  if (!chartEl.value || disposed || version !== renderVersion) return
  if (chart) {
    chart.dispose()
    chart = null
  }
  chart = core.init(chartEl.value)
  chart.setOption(buildOption(effectiveType.value), true)
  chart.on('click', (params) => {
    if (!props.spec.evidenceId) return
    if (effectiveType.value === 'heatmap' && props.spec.heatmap) {
      const cell = params.value || []
      const view = heatmapView()
      emit('drilldown', {
        evidenceId: props.spec.evidenceId,
        filters: [
          { field: view.xField, value: view.xAxis[cell[0]] },
          { field: view.yField, value: view.yAxis[cell[1]] },
        ],
      })
    } else if (props.spec.xField && params.name != null) {
      emit('drilldown', {
        evidenceId: props.spec.evidenceId,
        filters: props.spec.pointFilters?.[params.dataIndex]?.length
          ? props.spec.pointFilters[params.dataIndex]
          : [{ field: props.spec.xField, value: params.name }],
      })
    }
  })
  if (resizeObserver) resizeObserver.disconnect()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartEl.value)
}

function downloadPng() {
  if (!chart) return
  const url = chart.getDataURL({
    type: 'png',
    pixelRatio: 2,
    backgroundColor: store.theme === 'dark' ? '#181c24' : '#ffffff',
  })
  const a = document.createElement('a')
  a.href = url
  a.download = `${(props.spec.title || 'chart').replace(/[\\/:*?"<>|]/g, '_')}.png`
  a.click()
}

function downloadCsv() {
  let header = ['维度', ...(props.spec.series || []).map((s) => s.name)]
  let rows = (props.spec.xAxis || []).map((x, i) => [
    x,
    ...(props.spec.series || []).map((s) => s.data?.[i] ?? ''),
  ])
  if (props.spec.sourceRows?.length && props.spec.fields?.length) {
    header = props.spec.fields.map((f) => `${f.label || f.name}${f.unit ? `（${f.unit}）` : ''}`)
    rows = props.spec.sourceRows
  } else if (props.spec.heatmap) {
    const heatmap = heatmapView()
    header = [heatmap.xField, heatmap.yField, heatmap.metric]
    rows = heatmap.data.map(([x, y, value]) => [heatmap.xAxis[x], heatmap.yAxis[y], value])
  }
  const csv = [header, ...rows]
    .map((row) => row.map((cell) => `"${String(cell ?? '').replaceAll('"', '""')}"`).join(','))
    .join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${(props.spec.title || 'chart').replace(/[\\/:*?"<>|]/g, '_')}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

watch(() => props.spec, () => { chartType.value = 'auto'; render() }, { deep: true })
watch(chartType, render)
watch(() => store.theme, render)
watch(() => store.chartPalette, render)
onMounted(render)
onBeforeUnmount(() => {
  disposed = true
  ++renderVersion
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.chart-card {
  container-type: inline-size;
  container-name: chart-card;
  min-width: 0;
  border: 1px solid var(--chart-border);
  border-radius: 12px;
  background: var(--chart-surface);
  padding: 0 12px 8px;
  box-shadow: 0 2px 8px rgba(20, 37, 63, 0.035);
}
.chart-head { display:grid;grid-template-columns:minmax(0,1fr) 144px;align-items:start;gap:6px 20px;margin:0 -12px 8px;padding:12px 16px;border-radius:11px 11px 0 0;border-bottom:1px solid var(--chart-border);background:var(--chart-header); }
.chart-position{flex-shrink:0;align-self:center;padding:0 6px;border:1px solid var(--chart-border);border-radius:4px;font-size:12px;font-weight:400;line-height:20px;color:var(--chart-muted);white-space:nowrap}
.chart-title{grid-column:1;grid-row:1;display:flex;align-items:baseline;gap:8px;min-width:0;min-height:28px;font-size:15px;font-weight:600;color:var(--chart-text);margin:0;line-height:28px;overflow-wrap:anywhere}
.chart-title > span:last-child{min-width:0}
.chart-meta{grid-column:1;grid-row:2;min-width:0;font-size:12px;line-height:28px;color:var(--chart-muted);margin:0}
.chart-source-link{display:inline;border:0;border-radius:2px;padding:0 1px;background:transparent;color:var(--primary);font:inherit;cursor:pointer}
.chart-source-link:hover{text-decoration:underline;text-underline-offset:3px}
.chart-source-link:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
.chart-type-control{grid-column:2;grid-row:1;display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:28px;font-size:12px;white-space:nowrap;color:var(--chart-muted)}
.type-select{width:88px}
.type-select :deep(.el-select__wrapper){min-height:28px}
.chart-exports{grid-column:2;grid-row:2;display:flex;align-items:center;gap:4px;min-height:28px}
.chart-exports .el-button{flex:1;min-width:0;height:28px;padding:0 4px;border:0;border-radius:4px;background:transparent;color:var(--chart-muted);font-size:12px;line-height:16px;transition:background-color .15s,color .15s}
.chart-export-divider{height:13px;width:1px;background:var(--chart-border);flex-shrink:0}
.chart-exports .el-button :deep(> span){display:inline-flex;align-items:center;gap:5px}
.chart-exports .el-button:hover{background:var(--primary-soft);color:var(--primary)}
.chart-exports .el-button:focus-visible{outline:2px solid var(--primary);outline-offset:-2px}
.chart-head .el-button{margin-left:0}
.chart-canvas {
  width: 100%;
  height: 260px;
}
@container chart-card (max-width:420px){
  .chart-head{grid-template-columns:minmax(0,1fr) 128px;gap:8px}
  .chart-title{grid-column:1/-1;grid-row:1}
  .chart-meta{grid-column:1/-1;grid-row:2}
  .chart-type-control{grid-column:1;grid-row:3;width:144px}
  .chart-exports{grid-column:2;grid-row:3}
}
@container chart-card (max-width:280px){
  .chart-head{grid-template-columns:minmax(0,1fr)}
  .chart-type-control{grid-column:1;grid-row:3}
  .chart-exports{grid-column:1;grid-row:4;width:144px}
}
@media (max-width: 700px) {
  .chart-canvas {
    height: 230px;
  }
  .chart-head { padding:12px; }

}
</style>
