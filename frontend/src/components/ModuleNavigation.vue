<template>
  <Teleport :to="target || 'body'" :disabled="!target" defer>
    <nav class="module-nav" :aria-label="`${title}功能`">
      <h2>{{ title }}</h2>
      <slot />
    </nav>
  </Teleport>
</template>

<script setup>
defineProps({ title: { type: String, required: true }, target: { type: String, default: '' } })
</script>

<style scoped>
.module-nav { padding:6px 2px; }
h2 { margin:0 10px 22px; color:var(--text); font-size:16px; font-weight:600; }
.module-nav :deep(.nav-links) { display:flex; flex-direction:column; gap:4px; }
.module-nav :deep(.nav-secondary) { margin-top:24px; padding-top:18px; border-top:1px solid var(--border); }
.module-nav :deep(button) { display:flex; align-items:center; gap:10px; width:100%; min-height:42px; padding:10px 12px; border:0; border-radius:7px; background:transparent; color:var(--text-secondary); font:inherit; font-size:14px; letter-spacing:.015em; text-align:left; cursor:pointer; }
.module-nav :deep(button .el-icon) { flex-shrink:0; font-size:17px; }
.module-nav :deep(button:hover) { color:var(--text); background:var(--primary-soft); }
.module-nav :deep(button.active) { color:var(--primary); background:var(--primary-soft); font-weight:600; box-shadow:inset 3px 0 var(--primary); }
.module-nav :deep(button:focus-visible) { outline:2px solid var(--primary); outline-offset:-2px; }
.module-nav :deep(button:disabled) { opacity:.55; cursor:default; }
.module-nav :deep(.nav-count) { margin-left:auto; font-size:12px; font-weight:400; font-variant-numeric:tabular-nums; }
.module-nav :deep(.nav-label) { min-width:0; overflow-wrap:anywhere; }
.module-nav :deep(.el-dropdown) { display:flex; }
@media(max-width:1100px) {
  .module-nav { padding:0; }
  h2 { display:none; }
  .module-nav :deep(.nav-links) { flex-direction:row; overflow-x:auto; padding:2px; }
  .module-nav :deep(.nav-secondary) { margin-top:4px; padding-top:4px; }
  .module-nav :deep(button) { width:auto; min-height:36px; padding:8px 10px; gap:6px; flex-shrink:0; white-space:nowrap; }
  .module-nav :deep(button.active) { box-shadow:inset 0 -2px var(--primary); }
  .module-nav :deep(.nav-label) { white-space:nowrap; }
  .module-nav :deep(.nav-count) { margin-left:4px; }
  .module-nav :deep(.el-dropdown) { flex-shrink:0; }
}
</style>
