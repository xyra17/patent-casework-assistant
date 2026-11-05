// 以事件到达顺序展示执行阶段；运行 ID 用于归属和状态，不按助手名称重新归组。
export const EXECUTION_FILTER_OPTIONS = [
  {value:'all', label:'全部事件', traceLabel:'执行时间线', emptyLabel:'暂无执行记录'},
  {value:'main', label:'只看主智能体', traceLabel:'主智能体执行记录', emptyLabel:'当前筛选下暂无主智能体记录'},
  {value:'assistant', label:'只看子智能体', traceLabel:'子智能体执行记录', emptyLabel:'当前筛选下暂无可确认归属的子智能体记录'},
  {value:'tools', label:'只看工具', traceLabel:'工具调用记录', emptyLabel:'当前筛选下暂无工具调用记录'},
  {value:'errors', label:'只看异常', traceLabel:'异常记录', emptyLabel:'当前筛选下暂无异常记录'},
]
const MAIN = '主智能体'
const SOURCE = {web_search:'网络检索', db_query:'数据库', knowledge:'知识库'}
const TASK = {qa:'直接问答', analysis:'数据分析', report:'报告生成'}
const OWNER = {internet_search:'网络检索助手', list_sql_tables:'数据库查询助手', get_table_data:'数据库查询助手', execute_sql_query:'数据库查询助手', semantic_query:'数据库查询助手', semantic_drilldown:'数据库查询助手', search_knowledge_base:'内部知识库助手'}
const HIDDEN = new Set(['write_todos','ls','glob'])
const STOPS = {single_retrieval:'单次检索完成（未做覆盖验证）', evidence_sufficient:'证据覆盖检查通过', no_hits:'没有相关片段', no_new_evidence:'没有新增证据', repeated_query:'查询重复', budget_exhausted:'检索预算耗尽', max_hops:'达到跳数上限', controller_unavailable:'覆盖检查不可用', missing_evidence:'仍有证据缺口', context_budget:'上下文预算耗尽', index_changed:'索引版本变化'}
const DECISIONS = {approved:'批准补查', denied:'未开放来源', unavailable:'评估不可用', unchanged:'保留计划', budget_exhausted:'评估预算已用完'}
const time = value => value ? new Date(value).toLocaleTimeString('zh-CN',{hour12:false}) : ''
const duration = (start,end) => {
  const ms = new Date(end) - new Date(start)
  return !Number.isFinite(ms) || ms < 0 ? '' : ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}
const argsText = args => Object.entries(args || {}).map(([k,v]) => {
  const text = typeof v === 'string' ? v : JSON.stringify(v)
  return `${k}=${text?.length > 30 ? text.slice(0,30) + '…' : text}`
}).join(' · ')

export function buildExecutionTrace(events = [], {filter = 'all', meta = {}} = {}) {
  const selection = EXECUTION_FILTER_OPTIONS.find(option => option.value === filter)
  const root = {type:'timeline', label:selection?.traceLabel || '未知筛选', children:[], recordCount:0}
  const runs = new Map(), pending = new Map(), activeTools = new Map()
  let current = null
  const main = round => ({agent_name:MAIN, agent_run_id:`main:${round || ''}`, round})
  function owner(data, toolName) {
    if (data.agent_name) return data
    const name = OWNER[toolName]
    if (name) {
      const matches = [...pending.entries()].filter(([,entry]) => entry.name === name)
      if (matches.length === 1) return {agent_name:name,agent_run_id:matches[0][0],legacy:true}
      return {agent_name:`${name}（历史调用未关联）`,agent_run_id:`legacy:${name}`,legacy:true,ownershipKnown:false}
    }
    if (!pending.size && ['generate_markdown','convert_md_to_pdf','read_file_content'].includes(toolName)) return main(data.round)
    return {agent_name:'执行归属未记录',agent_run_id:'unknown',legacy:true,ownershipKnown:false}
  }
  function add(actor, node, ev) {
    const round = actor.round ?? null
    const key = actor.agent_name === MAIN ? 'main' : actor.agent_run_id || actor.agent_name
    if (!current || current.actorKey !== key || (round !== null && current.round !== null && current.round !== round)) {
      const primary = actor.agent_name === MAIN
      const known = actor.ownershipKnown !== false && actor.agent_name !== '执行归属未记录'
      const style = meta[actor.agent_name] || {}
      current = {key:`stage:${root.children.length}:${key}`, actorKey:key, stage:true,
        type:!known ? 'unknown' : primary ? 'main' : 'assistant', actor:actor.agent_name, round,
        label:!known ? `归属待确认 · ${actor.agent_name}` : primary ? '主智能体执行' : `子智能体执行 · ${actor.agent_name}`,
        color:style.color, icon:style.icon, timestamp:time(ev.timestamp), children:[]}
      root.children.push(current)
    }
    if (current.round === null && round !== null) current.round = round
    current.children.push(node)
    return node
  }
  function complete(node, ev, error = false) {
    if (!node) return
    const summary = ev.data?.summary || ''
    const failedResult = node.type === 'tool' && /^(连接数据库失败|网络检索失败|知识库检索失败|语义查询失败|语义下钻失败|禁止执行)/.test(summary.trim())
    node.status = error || failedResult ? 'error' : 'done'
    if (node.type === 'tool' && /^(本轮网络检索已达到上限|知识库索引尚未构建|非法的表名)/.test(summary.trim())) node.severity = 'warning'
    node.duration = duration(node.startedAt, ev.timestamp)
    node.summary = error ? (ev.data?.error || ev.message || '执行失败') : (ev.data?.summary || '')
  }
  for (const [index,ev] of events.entries()) {
    const data = ev.data || {}
    const simple = (label,type='step',children=[]) => ({key:`event:${index}`,type,label,children,initialExpanded:!['plan','tool','dispatch','return'].includes(type),timestamp:time(ev.timestamp)})
    if (ev.event === 'agent_activity') {
      if (data.phase === 'start') {
        const node = simple('处理与决策','activity')
        Object.assign(node,{status:'running',startedAt:ev.timestamp})
        add(owner(data),node,ev)
        runs.set(data.run_id,node)
      } else complete(runs.get(data.run_id),ev,data.phase === 'error')
    } else if (ev.event === 'assistant_call') {
      const caller = data.agent_name ? data : main(data.round)
      const name = data.assistant_name || '子智能体'
      const node = simple(`委派任务 → ${name}`,'dispatch')
      Object.assign(node,{status:'running',args:{任务:data.args || ''},startedAt:ev.timestamp})
      add(caller,node,ev)
      runs.set(data.run_id,node)
      pending.set(data.run_id,{name,caller,node})
    } else if (ev.event === 'tool_start') {
      const actor = owner(data,data.tool_name)
      activeTools.set(data.run_id,{...actor,tool:data.tool_name})
      if (HIDDEN.has(data.tool_name)) continue
      const node = simple(data.tool_name || '工具','tool')
      Object.assign(node,{args:data.args || {},argsSummary:argsText(data.args),status:'running',startedAt:ev.timestamp})
      add(actor,node,ev)
      runs.set(data.run_id,node)
    } else if (ev.event === 'tool_end' || ev.event === 'tool_error') {
      const failed = ev.event === 'tool_error'
      complete(runs.get(data.run_id),ev,failed)
      activeTools.delete(data.run_id)
      const delegated = pending.get(data.run_id)
      if (delegated) {
        const returned = simple(`接收 ${delegated.name} ${failed ? '的失败信息' : '的结果'}`,'return')
        returned.summary = data.summary || (failed ? ev.message : '')
        returned.status = failed ? 'error' : 'done'
        add(delegated.caller,returned,ev)
        pending.delete(data.run_id)
      }
    } else if (ev.event === 'retrieval_hop' || ev.event === 'retrieval_summary') {
      const matches = [...activeTools.values()].filter(tool => tool.tool === 'search_knowledge_base')
      const actor = data.agent_name ? data : matches.length === 1 ? matches[0] : owner(data,'search_knowledge_base')
      const children = ev.event === 'retrieval_hop'
        ? (data.evidence_ids || []).map(id=>({type:'step',label:`原文证据 ${id}`,children:[]}))
        : (data.missing || []).map(text=>({type:'step',label:`未核实：${text}`,children:[]}))
      const node = simple(ev.event === 'retrieval_hop' ? `知识库第 ${data.hop} 跳：${data.query}` : `知识库 · ${data.strategy === 'multi' ? '多跳' : '单次'} · ${STOPS[data.stop_reason] || data.stop_reason}`,'plan',children)
      if (ev.event === 'retrieval_summary' && data.stop_reason && !['single_retrieval','evidence_sufficient'].includes(data.stop_reason)) node.severity = 'warning'
      add(actor,node,ev)
    } else if (ev.event === 'rules_context') {
      const children=[...(data.applied || []).map(r=>({type:'step',label:`采用 ${r.title} v${r.revision}：${r.content}`,children:[]})),...(data.overridden || []).map(r=>({type:'step',label:`跳过 ${r.title}：${r.reason}`,children:[]}))]
      add(main(data.round),simple(`长期规则 · 采用 ${(data.applied || []).length} 条 · 跳过 ${(data.overridden || []).length} 条`,'plan',children),ev)
    } else if (ev.event === 'plan_created' || ev.event === 'plan_updated') {
      const steps = (data.steps || []).map((text,i)=>({type:'step',label:`${i+1}. ${text}`,children:[]}))
      if (data.forbidden_sources?.length) steps.push({type:'step',label:`禁止来源：${data.forbidden_sources.map(s=>SOURCE[s] || s).join('、')}`,children:[]})
      if (data.reason) steps.push({type:'step',label:`调整原因：${data.reason}`,children:[]})
      add(main(data.round),simple(`${ev.event === 'plan_updated' ? '计划更新' : '任务计划'} · ${TASK[data.task_type] || data.task_type || '未知'} · 数据源：${(data.required_sources || []).map(s=>SOURCE[s] || s).join('、') || '无'}`,'plan',steps),ev)
    } else if (ev.event === 'source_gap' || ev.event === 'routing_decision') {
      const node = simple(ev.event === 'source_gap' ? `证据缺口 ${data.id} · ${data.missing}` : `路由评估 · ${DECISIONS[data.status] || data.status}`,'plan',[
        {type:'step',label:ev.event === 'source_gap' ? `建议来源：${SOURCE[data.requested_source] || '未指定'}；依据：${(data.evidence_ids || []).join('、') || '无引用'}` : `${data.reason || ''}${data.add_sources?.length ? '；补查：' + data.add_sources.map(s=>SOURCE[s] || s).join('、') : ''}`,children:[]}
      ])
      if ((ev.event === 'routing_decision' && ['denied','unavailable','budget_exhausted'].includes(data.status)) || (ev.event === 'source_gap' && ['source_denied','tool_error'].includes(data.reason))) node.severity = 'warning'
      add(main(data.round),node,ev)
    } else if (ev.event === 'reflection') {
      const passed = !data.hard_issues?.length && !data.soft_issue && data.soft_review_status === 'passed'
      const node = simple(`第 ${data.round} 轮质量校验 · ${passed ? '通过' : '查看反思校验详情'}`,'plan')
      if (data.hard_issues?.length || data.soft_issue || data.soft_review_status === 'skipped' || data.soft_review_status === 'failed') node.severity = 'warning'
      add(main(data.round),node,ev)
    } else if (ev.event === 'session_created') {
      add(main(),simple('会话已创建','session'),ev)
    } else if (ev.event === 'task_clarification') {
      const node = simple('关键依据不足 · 等待你确认下一步','plan')
      node.severity = 'warning'
      node.summary = data.clarification?.message || ev.message
      add(main(),node,ev)
    } else if (ev.event === 'task_result' || ['error', 'task_cancelled'].includes(ev.event)) {
      for (const node of runs.values()) if (node.status === 'running') node.status = ['error', 'task_cancelled'].includes(ev.event) ? 'interrupted' : 'unknown'
      // 缺失结束事件不能补判成功；任务结果与单次调用状态分别显示。
      const node = simple(['error', 'task_cancelled'].includes(ev.event) ? ev.message || '执行失败' : data.degraded ? '任务结束（存在未达标项）' : '任务完成',['error', 'task_cancelled'].includes(ev.event) ? 'error' : 'result')
      if (data.degraded) node.severity = 'warning'
      add(main(),node,ev)
    }
  }
  // 先完成状态关联再筛选；只返回匹配记录，不能把原始阶段的其它子项一起带回。
  if (!selection) root.children = []
  else if (filter !== 'all') root.children = root.children.flatMap(stage => {
    if (filter === 'main') return stage.type === 'main' ? [stage] : []
    if (filter === 'assistant') return stage.type === 'assistant' ? [stage] : []
    const children = stage.children.filter(node => filter === 'tools'
      ? node.type === 'tool'
      : node.type === 'error' || ['error','interrupted','unknown'].includes(node.status) || node.severity === 'warning')
    return children.length ? [{...stage,children}] : []
  })
  root.recordCount = root.children.reduce((total,stage) => total + stage.children.length,0)
  return root
}
