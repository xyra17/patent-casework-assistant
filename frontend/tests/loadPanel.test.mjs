import test from 'node:test'
import assert from 'node:assert/strict'
import { createRenderer, h } from 'vue'
import { loadPanel } from '../src/loadPanel.js'

function mountPanel(loader) {
  const node = (type, text = '') => ({ type, text, children: [], props: {} })
  const renderer = createRenderer({
    createElement: type => node(type),
    createText: text => node('text', text),
    createComment: () => node('comment'),
    setText: (el, text) => { el.text = text },
    setElementText: (el, text) => { el.text = text; el.children = [] },
    patchProp: (el, key, before, value) => { el.props[key] = value },
    parentNode: el => el.parent,
    nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1],
    insert(el, parent, anchor) {
      el.parent = parent
      const index = parent.children.indexOf(anchor)
      parent.children.splice(index < 0 ? parent.children.length : index, 0, el)
    },
    remove(el) {
      const index = el.parent?.children.indexOf(el) ?? -1
      if (index >= 0) el.parent.children.splice(index, 1)
    },
  })
  const root = node('root')
  const app = renderer.createApp(loadPanel(loader, '执行详情'))
  const errors = []
  app.config.errorHandler = error => errors.push(error)
  app.mount(root)
  const text = el => el.text + el.children.map(text).join('')
  return { root, app, errors, text: () => text(root) }
}

const settle = async () => {
  await new Promise(resolve => setImmediate(resolve))
  await new Promise(resolve => setImmediate(resolve))
}

test('旧版组件资源加载失败时显示可操作的错误提示，不再留白或自动刷新', async () => {
  const failure = new TypeError('Failed to fetch dynamically imported module: EventTree-old.js')
  const panel = mountPanel(() => Promise.reject(failure))
  try {
    await settle()
    assert.deepEqual(panel.errors, [failure])
    assert.match(panel.text(), /执行详情加载失败/)
    assert.match(panel.text(), /保留未提交内容/)
    const alert = panel.root.children.find(el => el.props.role === 'alert')
    assert.ok(alert)
    const button = alert.children.find(el => el.type === 'button')
    assert.equal(button.text, '刷新页面')
    assert.equal(typeof button.props.onClick, 'function')
    // Node 没有 window；到这里未报错也验证了未自动刷新。
  } finally { panel.app.unmount() }
})

test('正常加载仍渲染执行详情内容', async () => {
  const panel = mountPanel(() => Promise.resolve({ render: () => h('section', '执行时间线') }))
  try {
    await settle()
    assert.equal(panel.text(), '执行时间线')
    assert.deepEqual(panel.errors, [])
  } finally { panel.app.unmount() }
})
