// 上传附件会复制到工作目录；它们不是任务生成的文件。
export function sessionOutputFiles(files, attachments, directory) {
  const root = (directory || '').replace(/\/+$/, '')
  const uploadedPaths = new Set((attachments || []).map(file => `${root}/${file.name}`))
  return (files || [])
    .filter(file => file.type === 'file' && !uploadedPaths.has(file.path) && !file.path.startsWith(`${root}/attachments/`))
    .sort((a, b) => (b.mtime || 0) - (a.mtime || 0))
}

export const ATTACHMENT_ACCEPT = '.csv,.xlsx,.xls,.txt,.md,.doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff'

export function stageAttachments(current, incoming) {
  const files = [...current, ...incoming]
  if (files.length > 20) throw new Error('每批最多选择20个附件')
  if (new Set(files.map(file => file.name)).size !== files.length) throw new Error('文件名重复，请先重命名再选择')
  for (const file of files) {
    if (!/\.(csv|xlsx|xls|txt|md|doc|docx|pdf|png|jpe?g|webp|gif|bmp|tiff?)$/i.test(file.name)) throw new Error(`不支持的附件格式：${file.name}`)
    if (!file.size || file.size > 20 * 1024 * 1024) throw new Error(`附件须非空且不超过20MB：${file.name}`)
  }
  if (files.reduce((sum, file) => sum + file.size, 0) > 100 * 1024 * 1024) throw new Error('每批附件总大小不得超过100MB')
  return files
}

// Upload must finish before the task can read its immutable working copies.
export async function prepareTaskAttachments(threadId, files, upload) {
  if (!files.length) return
  const result = await upload(threadId, files)
  const saved = new Set(result.files || [])
  if (files.some(file => !saved.has(file.name))) throw new Error('部分附件尚未保存，任务未开始，请重试上传')
}

export function comparisonQuery(left, right) {
  if (!left || !right || left === right) throw new Error('请选择两份不同的附件')
  const tables = [left,right].every(name=>/\.(csv|xlsx|xls)$/i.test(name))
  const documents = [left,right].every(name=>/\.(docx|pdf|txt|md)$/i.test(name))
  if (!tables && !documents) throw new Error('请选择两份文档，或两份表格；表格与文档请分别核对')
  const target = tables
    ? '先查看工作表和实际表头，按共有的案号列对齐，列出新增、删除案件与字段变化，附两侧工作表和原始行号。案号缺失、重复或表头无法对应时说明具体问题，不按行位置强行对齐。'
    : '列出新增、删除和修改的内容，保留两侧原文及段落或页码，区分文本差异与技术影响。'
  return `仅比较本会话附件。基准文件：${JSON.stringify(left)}；对比文件：${JSON.stringify(right)}。${target}直接回答，不要生成文件。`
}
