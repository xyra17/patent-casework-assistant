export async function checked(response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = body.detail
    const message = Array.isArray(detail) ? detail.map(item => String(item.msg || '').replace(/^Value error, /,'')).join('；') : typeof detail === 'object' && detail ? detail.message : detail
    const error = new Error(message || body.error || `HTTP ${response.status}`)
    error.detail = detail
    error.status = response.status
    throw error
  }
  return response
}

export async function cancelTask(threadId) {
  return (await checked(await fetch(`/api/task/${encodeURIComponent(threadId)}/cancel`, { method: 'POST' }))).json()
}

export function waitForWS(ws) {
  return new Promise((resolve, reject) => {
    if (ws.readyState === WebSocket.OPEN) return resolve()
    if (ws.readyState >= WebSocket.CLOSING) return reject(new Error('实时连接已断开'))
    const finish = error => {
      clearTimeout(timeout)
      ws.removeEventListener('open', opened)
      ws.removeEventListener('error', failed)
      ws.removeEventListener('close', closed)
      if (error) reject(error)
      else resolve()
    }
    const opened = () => finish()
    const failed = () => finish(new Error('实时连接失败'))
    const closed = () => finish(new Error('实时连接已断开'))
    const timeout = setTimeout(() => { finish(new Error('实时连接超时')); ws.close() }, 10000)
    ws.addEventListener('open', opened, {once: true})
    ws.addEventListener('error', failed, {once: true})
    ws.addEventListener('close', closed, {once: true})
  })
}

// REST 封装（开发期经 Vite 代理转发到 FastAPI）

export async function submitTask(query, threadId, taskQuery = '', requirePdf = false, maxRounds = 3, expectedRevisionId = null, skipRuleVersions = [], roundMode = 'manual', allowedSources = ['db_query', 'knowledge'], clarificationAnswer = null, productContext = null, followup = null) {
  const res = await fetch('/api/task', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      task_query: taskQuery || query,
      require_pdf: Boolean(requirePdf),
      max_rounds: maxRounds,
      round_mode: roundMode,
      expected_revision_id: expectedRevisionId,
      skip_rule_versions: skipRuleVersions,
      thread_id: threadId,
      allowed_sources: allowedSources,
      ...(productContext ? {product_context:productContext} : {}),
      ...(clarificationAnswer ? {clarification_answer:clarificationAnswer} : {}),
      ...(followup ? {followup} : {}),
    }),
  })
  return (await checked(res)).json()
}

export async function semanticQuery(payload) {
  const res = await fetch('/api/semantic/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return (await checked(res)).json()
}

export async function semanticDrilldown(payload) {
  const res = await fetch('/api/semantic/drilldown', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return (await checked(res)).json()
}

export async function getSemantic() {
  const res = await fetch('/api/semantic')
  return (await checked(res)).json()
}

export async function listRuns(limit = 50, offset = 0, tracked = [], before = null) {
  const res = await fetch(`/api/runs?limit=${limit}&offset=${offset}&tracked=${encodeURIComponent(tracked.join(","))}${before === null ? "" : `&before=${before}`}`)
  return (await checked(res)).json()
}

export async function getRun(threadId) {
  const res = await fetch(`/api/runs/${encodeURIComponent(threadId)}`)
  return (await checked(res)).json()
}

export async function getConversation(threadId, before = null) {
  const params = new URLSearchParams({limit:'20'})
  if (before != null) params.set('before', before)
  return (await checked(await fetch(`/api/runs/${encodeURIComponent(threadId)}/conversation?${params}`))).json()
}

export async function getConversationTurn(threadId, turnId) {
  return (await checked(await fetch(`/api/runs/${encodeURIComponent(threadId)}/turns/${encodeURIComponent(turnId)}`))).json()
}

export async function draftTurnRule(threadId, turnId) {
  return (await checked(await fetch('/api/long-term-rules/draft', {
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({thread_id:threadId,turn_id:turnId}),
  }))).json()
}

export async function runDrilldown(threadId, evidenceId, filters) {
  const params = new URLSearchParams({ evidence_id: evidenceId })
  const [first, second] = filters
  params.set('field', first.field)
  params.set('value', String(first.value))
  if (second) {
    params.set('field2', second.field)
    params.set('value2', String(second.value))
  }
  const res = await fetch(`/api/runs/${encodeURIComponent(threadId)}/drilldown?${params}`)
  return (await checked(res)).json()
}

export async function downloadChartReport(threadId) {
  const res = await fetch(`/api/runs/${encodeURIComponent(threadId)}/chart-report`)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || body.error || `HTTP ${res.status}`)
  }
  return res.blob()
}

export async function deleteRun(threadId) {
  const res = await fetch(`/api/runs/${encodeURIComponent(threadId)}`, { method: 'DELETE' })
  return (await checked(res)).json()
}

export async function listTrash(offset = 0) {
  return (await checked(await fetch(`/api/trash?limit=50&offset=${offset}`))).json()
}

export async function restoreRun(threadId) {
  return (await checked(await fetch(`/api/trash/${encodeURIComponent(threadId)}/restore`, {method: 'POST'}))).json()
}

export async function purgeRun(threadId) {
  return (await checked(await fetch(`/api/trash/${encodeURIComponent(threadId)}`, {method: 'DELETE'}))).json()
}

export async function uploadFiles(threadId, files, replace = []) {
  const form = new FormData()
  form.append('thread_id', threadId)
  form.append('replace', JSON.stringify(replace))
  for (const f of files) form.append('files', f)
  const res = await fetch('/api/upload', { method: 'POST', body: form })
  return (await checked(res)).json()
}

export async function listFiles(path, threadId, recursive = false) {
  const res = await fetch(`/api/files?path=${encodeURIComponent(path)}&thread_id=${encodeURIComponent(threadId)}&recursive=${recursive}`)
  return (await checked(res)).json()
}

export async function downloadFile(path, threadId) {
  return checked(await fetch(`/api/download?path=${encodeURIComponent(path)}&thread_id=${encodeURIComponent(threadId)}`))
}

// WebSocket 连接（开发期经 Vite 代理 /ws 到后端）
export function connectWS(threadId, onEvent) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/ws/${threadId}`)
  ws.onmessage = (e) => {
    let data
    try {
      data = JSON.parse(e.data)
    } catch {
      return
    }
    if (data.type === 'monitor_event') onEvent(data)
  }
  ws.onclose = () => console.log('WS 已断开')
  return ws
}

export async function listAttachments(threadId) {
  const params = new URLSearchParams({ thread_id: threadId })
  return (await checked(await fetch(`/api/attachments?${params}`))).json()
}

export async function deleteAttachment(threadId, filename) {
  const params = new URLSearchParams({ thread_id: threadId, filename })
  return (await checked(await fetch(`/api/attachments?${params}`, { method: 'DELETE' }))).json()
}

export async function getKnowledge() {
  return (await checked(await fetch('/api/knowledge'))).json()
}
export async function saveKnowledge(files, replace = '') {
  const form = new FormData()
  files.forEach(file => form.append('files', file))
  if (replace) form.append('replace', replace)
  return (await checked(await fetch('/api/knowledge/documents', {method: 'POST', body: form}))).json()
}
export async function removeKnowledge(name) {
  return (await checked(await fetch(`/api/knowledge/documents?${new URLSearchParams({name})}`, {method: 'DELETE'}))).json()
}
export async function rebuildKnowledge() {
  return (await checked(await fetch('/api/knowledge/rebuild', {method: 'POST'}))).json()
}
export async function saveKnowledgeVersion(name, body) {
  return (await checked(await fetch(`/api/knowledge/documents/version?${new URLSearchParams({name})}`, {
    method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body),
  }))).json()
}
export async function searchKnowledge(query, scope = {}) {
  return (await checked(await fetch('/api/knowledge/search', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query, ...scope}),
  }))).json()
}

export async function getImportConnections() {
  return (await checked(await fetch('/api/data-import/connections'))).json()
}
export async function getDataImport(connectionId='default') {
  return (await checked(await fetch(`/api/data-import?${new URLSearchParams({connection_id:connectionId})}`))).json()
}
export async function uploadImport(file) {
  const form = new FormData()
  form.append('file', file)
  return (await checked(await fetch('/api/data-import/upload', {method: 'POST', body: form}))).json()
}
export async function getImportBatch(id) {
  return (await checked(await fetch(`/api/data-import/batches/${encodeURIComponent(id)}`))).json()
}
export async function validateImport(id, payload) {
  return (await checked(await fetch(`/api/data-import/batches/${encodeURIComponent(id)}/validate`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
  }))).json()
}
export async function commitImport(id, validationToken, connectionId='default') {
  return (await checked(await fetch(`/api/data-import/batches/${encodeURIComponent(id)}/commit`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({validation_token: validationToken,connection_id:connectionId}),
  }))).json()
}
export async function discardImport(id) {
  return (await checked(await fetch(`/api/data-import/batches/${encodeURIComponent(id)}`, {method: 'DELETE'}))).json()
}

export async function saveIntentRevision(threadId, revisedQuery, expectedRevisionId) {
  return (await checked(await fetch(`/api/runs/${encodeURIComponent(threadId)}/intent-revisions`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({revised_query: revisedQuery, expected_revision_id: expectedRevisionId}),
  }))).json()
}

export async function getRuleTemplates() { return (await checked(await fetch('/api/long-term-rules/templates'))).json() }
export async function getLongTermRules() { return (await checked(await fetch('/api/long-term-rules'))).json() }
export async function getRuleCatalog() { return (await checked(await fetch('/api/long-term-rules/catalog'))).json() }
export async function draftLongTermRule(threadId, revisionId) {
  return (await checked(await fetch('/api/long-term-rules/draft', {
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({thread_id:threadId,revision_id:revisionId}),
  }))).json()
}
export async function saveLongTermRule(payload, id='') {
  return (await checked(await fetch(`/api/long-term-rules${id ? '/' + encodeURIComponent(id) : ''}`, {
    method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
  }))).json()
}
export async function deleteLongTermRule(id, revision) {
  return (await checked(await fetch(`/api/long-term-rules/${encodeURIComponent(id)}?${new URLSearchParams({expected_revision:revision})}`,{method:'DELETE'}))).json()
}
export async function getRuleVersions(id) {
  return (await checked(await fetch(`/api/long-term-rules/${encodeURIComponent(id)}/versions`))).json()
}

export async function previewRuleBinding(binding) {
 return (await checked(await fetch('/api/long-term-rules/binding-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({binding})}))).json()
}
export async function draftRuleBinding(content,metric_key) {
 return (await checked(await fetch('/api/long-term-rules/binding-draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,metric_key})}))).json()
}

export async function getBusinessDatabase() {return (await checked(await fetch('/api/business-database'))).json()}
export async function browseBusinessRows(payload) {
 return (await checked(await fetch('/api/business-database/rows',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}))).json()
}

export async function getBusinessRow(table,key) {return (await checked(await fetch('/api/business-database/row-detail',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({table,key})}))).json()}
export async function getBusinessRowTemplate(table) {return (await checked(await fetch('/api/business-database/row-template',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({table})}))).json()}
export async function mutateBusinessRow(mode,payload) {return (await checked(await fetch(mode==='create'?'/api/business-database/create-row':mode==='delete'?'/api/business-database/delete-row':'/api/business-database/row',{method:mode==='delete'||mode==='create'?'POST':'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}))).json()}
export async function getBusinessAudit() {return (await checked(await fetch('/api/business-database/audit'))).json()}

export async function previewBusinessAdvanced(body) {return (await checked(await fetch('/api/business-database/advanced/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}))).json()}
export async function applyBusinessAdvanced(body) {return (await checked(await fetch('/api/business-database/advanced/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}))).json()}
export async function previewBusinessSQL(body) {return (await checked(await fetch('/api/business-database/sql/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}))).json()}
export async function executeBusinessSQL(body) {return (await checked(await fetch('/api/business-database/sql/execute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}))).json()}
export async function getBusinessSQLHistory() {return (await checked(await fetch('/api/business-database/sql/history'))).json()}

export async function getMetricOptions(metrics) {
  const res = await fetch('/api/business-database/metric-options', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({metrics})})
  return (await checked(res)).json()
}

export async function listExecutions(threadId, offset = 0) {
  return (await checked(await fetch(`/api/runs/${encodeURIComponent(threadId)}/executions?offset=${offset}`))).json()
}
export async function getExecution(threadId, executionId) {
  return (await checked(await fetch(`/api/runs/${encodeURIComponent(threadId)}/executions/${encodeURIComponent(executionId)}`))).json()
}
