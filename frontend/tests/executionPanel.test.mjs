import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref } from 'vue'
import { useExecutionPanel } from '../src/useExecutionPanel.js'

function setup(initial) {
  const scope = effectScope(), session = ref(initial)
  const panel = scope.run(() => useExecutionPanel(session))
  return { session, collapsed: panel.collapsed, stop: () => scope.stop() }
}

test('历史会话默认收起，所有终止状态均释放右侧面板', () => {
  for (const status of ['completed', 'failed', 'cancelled', 'awaiting_input']) {
    const panel = setup({ thread_id: 'one', execution_id: 'a', status: 'running' })
    assert.equal(panel.collapsed.value, false)
    panel.session.value.status = status
    assert.equal(panel.collapsed.value, true)
    panel.stop()
    const history = setup({ thread_id: 'one', status })
    assert.equal(history.collapsed.value, true)
    history.stop()
  }
})

test('手动选择按会话保留，不被运行事件、元数据回填或会话切换覆盖', () => {
  const panel = setup({ thread_id: 'one', status: 'running', events: [] })
  panel.collapsed.value = true
  panel.session.value.events.push({ event: 'plan_created' })
  panel.session.value.execution_id = 'a'
  assert.equal(panel.collapsed.value, true)
  panel.session.value.status = 'completed'
  panel.collapsed.value = false
  const first = panel.session.value
  panel.session.value = { thread_id: 'two', status: 'completed' }
  assert.equal(panel.collapsed.value, true)
  panel.session.value = first
  assert.equal(panel.collapsed.value, false)
  panel.stop()
})

test('重跑自动展开，结束再次收起；离线后同步到另一轮已完成记录也收起', () => {
  const panel = setup({ thread_id: 'one', execution_id: 'a', status: 'completed' })
  panel.session.value.status = 'running'
  assert.equal(panel.collapsed.value, false)
  panel.session.value.execution_id = 'b'
  panel.session.value.status = 'completed'
  assert.equal(panel.collapsed.value, true)
  panel.collapsed.value = false
  panel.session.value.execution_id = 'c'
  assert.equal(panel.collapsed.value, true)
  panel.stop()
})
