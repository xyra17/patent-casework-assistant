import test from 'node:test'
import assert from 'node:assert/strict'
import { paneLayout, paneBounds, resizedPane } from '../src/paneLayout.js'

test('saved pane widths fit the viewport and preserve readable middle and side panels', () => {
  for (const viewport of [1201, 1280, 1440, 1920]) {
    for (const saved of [{}, { left: 9999, right: 9999 }, { left: -1, right: -1 }, { left: 'bad', right: Infinity }]) {
      const layout = paneLayout(viewport, true, false, saved)
      assert.ok(layout.left >= 260 && layout.left <= 480)
      assert.ok(layout.right >= 320 && layout.right <= 600)
      assert.ok(layout.available - layout.left - layout.right >= 400)
    }
  }
})

test('dragging either boundary clamps at its limits without resizing the opposite pane', () => {
  const before = paneLayout(1280, true, false)
  for (const side of ['left', 'right']) {
    const other = side === 'left' ? 'right' : 'left'
    const bounds = paneBounds(before, side)
    assert.equal(resizedPane(before, side, -9999), bounds.min)
    assert.equal(resizedPane(before, side, 9999), bounds.max)
    const next = paneLayout(1280, true, false, { ...before, [side]: bounds.max })
    assert.equal(next[other], before[other])
    assert.ok(next.available - next.left - next.right >= 400)
  }
})

test('collapsed and absent right panels leave their width available to the result', () => {
  const saved = { left: 360, right: 450 }
  assert.equal(paneLayout(1440, true, true, saved).right, 0)
  assert.equal(paneLayout(1280, true, true, saved).right, 0)
  assert.deepEqual(paneLayout(1440, true, true, saved), paneLayout(1440, false, false, saved))
  assert.equal(paneLayout(1440, false, false, saved).right, 0)
  assert.equal(paneLayout(1440, true, false, saved).right, 450)
})
