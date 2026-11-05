export function selectCurrentPdf(files, startedAt) {
  const start = Date.parse(startedAt)
  if (!Number.isFinite(start)) return null
  return files.filter(file => file.type === 'file' && /\.pdf$/i.test(file.name) && Number(file.mtime) * 1000 >= start)
    .sort((a, b) => b.mtime - a.mtime)[0] || null
}

export function resultOutputSummary({pdfUrl='',pdfRequired=false,status='',preparing=false,chartCount=0}={}) {
  const parts=[]
  if(pdfUrl)parts.push('已生成 PDF')
  else if(pdfRequired)parts.push(status==='running' && !preparing ? 'PDF 待生成' : '暂无 PDF 文件')
  if(chartCount>0)parts.push(`含 ${chartCount} 张图表`)
  return parts.join(' · ')
}


export function executionResultContent(session) {
  return session?.result || ''
}

export function answerSourceItems(session) {
  const ids = new Set(Array.from((session?.result || '').matchAll(/\[(S\d+)\]/g), match=>match[1]))
  return (session?.evidence || []).filter(item=>ids.has(item.id))
}
