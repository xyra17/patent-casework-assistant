import { marked, Renderer } from 'marked'
import { caseFieldLabel } from './sourcePresentation.js'

const renderer = new Renderer()
const table = renderer.table
const tablecell = renderer.tablecell
const heading = renderer.heading
const link = renderer.link

renderer.link = function (token) {
  // GFM can include adjacent Chinese prose in an automatically detected URL.
  // Explicit Markdown links keep their authored target, including Unicode paths.
  if (/^https?:\/\//.test(token.raw) && token.raw === token.href) {
    const boundary = token.href.search(/[（），。；！？【】]/)
    if (boundary >= 0) {
      const href = token.href.slice(0, boundary)
      const suffix = token.href.slice(boundary).replace(/[&<>"']/g, value => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[value])
      return link.call(this, { ...token, href, text: href, tokens: [{ type: 'text', raw: href, text: href }] }) + suffix
    }
  }
  return link.call(this, token)
}

renderer.table = function (token) {
  return `<div class="result-table-scroll" role="region" aria-label="查询结果表格，可左右滚动" tabindex="0">${table.call(this, token)}</div>`
}
renderer.tablecell = function (token) {
  const html = tablecell.call(this, token)
  if (token.header && token.text.trim() === '办理阶段') return html.replace('办理阶段', caseFieldLabel(token.text.trim()))
  const atomic = /^(?:\d{4}-\d{2}-\d{2}|[A-Z][A-Z\d]*(?:-[A-Z\d]+)+|S\d{2}[A-Z]\d{6})$/i.test(token.text.trim())
  return atomic ? html.replace(/^<(td|th)/, '<$1 class="result-table-atomic"') : html
}

renderer.heading = function (token) {
  const html = heading.call(this, token)
  const label = ['案件信息查询', '案件台账查询'].find(value => token.text.startsWith(value))
  return label ? html.replace(label, '案件查询结果') : html
}

export function parseResultMarkdown(value) {
  return marked.parse(value || '', { renderer })
}
