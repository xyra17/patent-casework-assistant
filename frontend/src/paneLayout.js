export const PANE_LIMITS = { left: { min: 260, max: 480 }, right: { min: 320, max: 600 }, main: 400 }
const clamp = (value, min, max) => Math.min(Math.max(value, min), max)
const validWidth = (value, fallback) => Number.isFinite(value) ? value : fallback

export function paneLayout(viewport, hasLog, collapsed, saved = {}) {
  const compact = viewport <= 1380
  const gap = compact ? 12 : 14
  // Small screens use a stacked layout and hide the separators.
  const expanded = hasLog && !collapsed
  const available = Math.max(viewport, 1201) - gap * 2 - gap * (expanded ? 2 : 1)
  const defaults = { left: compact ? 300 : 320, right: compact ? 360 : 380 }
  const right = expanded
    ? clamp(validWidth(saved.right, defaults.right), PANE_LIMITS.right.min,
        Math.min(PANE_LIMITS.right.max, available - PANE_LIMITS.left.min - PANE_LIMITS.main))
    : 0
  const left = clamp(validWidth(saved.left, defaults.left), PANE_LIMITS.left.min,
    Math.min(PANE_LIMITS.left.max, available - right - PANE_LIMITS.main))
  return { left, right, available, defaults }
}

export function paneBounds(layout, side) {
  const other = side === 'left' ? layout.right : layout.left
  return { min: PANE_LIMITS[side].min, max: Math.min(PANE_LIMITS[side].max, layout.available - other - PANE_LIMITS.main) }
}

export function resizedPane(layout, side, width) {
  const { min, max } = paneBounds(layout, side)
  return Math.round(clamp(width, min, max))
}
