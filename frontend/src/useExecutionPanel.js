import { computed, reactive, watch } from 'vue'

export function useExecutionPanel(session) {
  const views = reactive(new Map())
  watch(() => [session.value?.thread_id, session.value?.execution_id, session.value?.status], ([id, execution, status]) => {
    if (!id) return
    const previous = views.get(id)
    const newFinishedExecution = previous?.execution && execution && previous.execution !== execution && status !== 'running'
    const reset = !previous || previous.status !== status || newFinishedExecution
    views.set(id, { execution, status, collapsed: reset ? status !== 'running' : previous.collapsed })
  }, { immediate: true, flush: 'sync' })

  const collapsed = computed({
    get: () => views.get(session.value?.thread_id)?.collapsed ?? true,
    set: value => {
      const view = views.get(session.value?.thread_id)
      if (view) view.collapsed = value
    },
  })
  return { collapsed }
}
