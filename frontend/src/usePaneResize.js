import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { paneLayout, paneBounds, resizedPane } from './paneLayout'

const STORAGE_KEY = 'data-platform-pane-widths-v1'

export function usePaneResize(hasLog, collapsed) {
  const viewport = ref(window.innerWidth)
  let initial = {}
  try { initial = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {} } catch { /* Use defaults. */ }
  const saved = ref(initial)
  const resizing = ref('')
  const layout = computed(() => paneLayout(viewport.value, hasLog.value, collapsed.value, saved.value))
  const paneStyle = computed(() => viewport.value > 1200 ? {
    '--sidebar-size': `${layout.value.left}px`, '--log-size': `${layout.value.right}px`,
  } : {})
  const leftBounds = computed(() => paneBounds(layout.value, 'left'))
  const rightBounds = computed(() => paneBounds(layout.value, 'right'))
  let drag = null

  function persist() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(saved.value)) } catch { /* Resizing works without storage. */ }
  }
  function finishResize() {
    if (!drag) return
    const previous = drag
    drag = null
    resizing.value = ''
    if (previous.target.hasPointerCapture(previous.pointerId)) previous.target.releasePointerCapture(previous.pointerId)
    persist()
  }
  function startResize(event, side) {
    if (event.button !== 0 || viewport.value <= 1200) return
    event.preventDefault()
    finishResize()
    event.currentTarget.focus()
    drag = { side, x: event.clientX, width: layout.value[side], target: event.currentTarget, pointerId: event.pointerId }
    event.currentTarget.setPointerCapture(event.pointerId)
    resizing.value = side
  }
  function moveResize(event) {
    if (!drag || event.pointerId !== drag.pointerId) return
    const delta = (event.clientX - drag.x) * (drag.side === 'left' ? 1 : -1)
    saved.value = { ...saved.value, [drag.side]: resizedPane(layout.value, drag.side, drag.width + delta) }
  }
  function resetPane(side) {
    saved.value = { ...saved.value, [side]: layout.value.defaults[side] }
    persist()
  }
  function resizeWithKey(event, side) {
    const bounds = paneBounds(layout.value, side)
    let width = layout.value[side]
    if (event.key === 'Home') width = bounds.min
    else if (event.key === 'End') width = bounds.max
    else if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
      width += (event.key === 'ArrowRight' ? 1 : -1) * (side === 'left' ? 1 : -1) * (event.shiftKey ? 40 : 10)
    } else return
    event.preventDefault()
    saved.value = { ...saved.value, [side]: resizedPane(layout.value, side, width) }
    persist()
  }
  function onResize() {
    finishResize()
    viewport.value = window.innerWidth
  }
  onMounted(() => {
    window.addEventListener('resize', onResize)
    window.addEventListener('blur', finishResize)
  })
  onBeforeUnmount(() => {
    finishResize()
    window.removeEventListener('resize', onResize)
    window.removeEventListener('blur', finishResize)
  })
  return { layout, paneStyle, resizing, leftBounds, rightBounds, startResize, moveResize, finishResize, resetPane, resizeWithKey }
}
