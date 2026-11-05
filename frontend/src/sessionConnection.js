// A socket belongs to one connection attempt. Older callbacks cannot alter its successor.
const eventKey = event => JSON.stringify([event.timestamp, event.event, event.message || '', event.data || {}])

export function createSessionConnections({connectWS, getRun, applyRun, onEvent,
  isPresent = () => true, schedule = setTimeout, unschedule = clearTimeout}) {
  const connections = new Map()
  const retries = new Map()
  const current = state => connections.get(state.session.thread_id) === state

  function close(threadId) {
    const state = connections.get(threadId)
    connections.delete(threadId)
    if (retries.has(threadId)) unschedule(retries.get(threadId))
    retries.delete(threadId)
    state?.socket.close()
  }

  function open(session, reconcile = false) {
    const threadId = session.thread_id
    close(threadId)
    const state = {session, buffering: reconcile, pending: [], socket: null}
    connections.set(threadId, state)
    try {
      state.socket = connectWS(threadId, event => {
        if (!current(state)) return
        if (state.buffering) state.pending.push(event)
        else onEvent(session, event)
      })
    } catch (error) {
      connections.delete(threadId)
      throw error
    }
    state.socket.onclose = () => {
      if (!current(state)) return
      connections.delete(threadId)
      if (session.status === 'running' && isPresent(session)) {
        retries.set(threadId, schedule(() => {
          retries.delete(threadId)
          if (session.status === 'running' && isPresent(session)) open(session, true)
        }, 1500))
      }
    }
    if (reconcile) state.socket.addEventListener('open', async () => {
      try {
        const run = await getRun(threadId)
        if (!current(state)) return
        applyRun(session, run)
        const known = new Set((run.events || []).map(eventKey))
        state.buffering = false
        for (const event of state.pending) {
          if (!current(state)) break
          const key = eventKey(event)
          if (!known.has(key)) { known.add(key); onEvent(session, event) }
        }
        state.pending = []
        if (current(state) && session.status !== 'running') close(threadId)
      } catch {
        if (!current(state)) return
        // Keep events received while REST was unavailable, then retry the connection.
        state.buffering = false
        for (const event of state.pending) {
          if (!current(state)) break
          onEvent(session, event)
        }
        state.pending = []
        if (current(state)) state.socket.close()
      }
    }, {once: true})
    return state.socket
  }
  return {open, close, has: threadId => connections.has(threadId) || retries.has(threadId)}
}
