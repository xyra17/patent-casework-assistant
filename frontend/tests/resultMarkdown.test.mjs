import test from 'node:test'
import assert from 'node:assert/strict'
import { parseResultMarkdown } from '../src/resultMarkdown.js'

test('结果表格可独立横向滚动，保留完整编号与日期', () => {
  const html = parseResultMarkdown('| 所编号 | 客户案号 | 初稿期限 | 来源 |\n| --- | --- | --- | --- |\n| DEMO-2026-014 | DEMO-CLIENT-018 | 2026-10-30 | [S1] |')
  assert.match(html, /class="result-table-scroll"[^>]*role="region"[^>]*tabindex="0"/)
  for (const value of ['DEMO-2026-014','DEMO-CLIENT-018','2026-10-30']) {
    assert.ok(html.includes(`<td class="result-table-atomic">${value}</td>`))
  }
  assert.match(html, /<td>\[S1\]<\/td>/)
  assert.match(html, /<\/table>\s*<\/div>/)
})

test('表格仍按 Markdown 渲染格式与转义内容', () => {
  const html = parseResultMarkdown('| 说明 |\n| --- |\n| **待核实** &lt;script&gt; |')
  assert.match(html, /<strong>待核实<\/strong>/)
  assert.match(html, /&lt;script&gt;/)
  assert.equal(parseResultMarkdown('普通回答').includes('result-table-scroll'), false)
})

test('公开来源裸链接排除相邻中文括号和说明', () => {
  const html = parseResultMarkdown('https://arxiv.org/abs/1603.09320（PDF 与摘要同一版本：https://arxiv.org/pdf/1603.09320）')
  assert.match(html, /href="https:\/\/arxiv.org\/abs\/1603\.09320"/)
  assert.match(html, /href="https:\/\/arxiv.org\/pdf\/1603\.09320"/)
  assert.ok(html.includes('</a>（PDF'))
  assert.ok(html.includes('</a>）'))
  assert.doesNotMatch(html, /href="[^"]*(?:%EF%BC|（|）)/)
})

test('显式链接和合法中文路径保留原目标', () => {
  const html = parseResultMarkdown('[文档](https://example.test/文档（修订）) https://example.test/中文路径?q=1&v=2')
  assert.ok(html.includes(encodeURI('https://example.test/文档（修订）')))
  assert.ok(html.includes(encodeURI('https://example.test/中文路径?q=1&v=2')))
})
