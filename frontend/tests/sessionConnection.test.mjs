import test from 'node:test'
import assert from 'node:assert/strict'
import {createSessionConnections} from '../src/sessionConnection.js'
import {selectCurrentPdf} from '../src/previewState.js'
import {waitForWS} from '../src/api.js'

class Socket extends EventTarget {
  readyState = 0
  close() { this.readyState = 3; this.onclose?.(); this.dispatchEvent(new Event('close')) }
  open() { this.readyState = 1; this.dispatchEvent(new Event('open')) }
}
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b}); return {promise,resolve,reject} }
const flush = async () => { await Promise.resolve(); await Promise.resolve() }
const event = (id, name='tool_end') => ({timestamp:`2026-10-23T00:00:0${id}Z`,event:name,message:'',data:{id}})
function fixture(load = async () => ({status:'running',events:[]})) {
  const sockets = [], timers = new Map(), session = {thread_id:'task',status:'running',events:[]}
  let timerId = 0
  const connections = createSessionConnections({
    connectWS: (_id, receive) => { const socket = new Socket(); sockets.push({socket, receive}); return socket },
    getRun: load,
    applyRun: (target, run) => Object.assign(target, run),
    onEvent: (target, e) => { target.events.push(e); if (e.event === 'task_result') target.status = 'completed' },
    schedule: fn => { timers.set(++timerId, fn); return timerId },
    unschedule: id => timers.delete(id),
  })
  return {connections,sockets,timers,session}
}
test('initial task sockets reconnect after unexpected disconnects', () => {
  const f=fixture(); f.connections.open(f.session); f.sockets[0].socket.close()
  assert.equal(f.timers.size,1)
  f.timers.values().next().value()
  assert.equal(f.sockets.length,2)
})
test('old close callbacks and messages cannot destroy successor connection', () => {
  const f=fixture(); f.connections.open(f.session); const old=f.sockets[0]
  f.connections.open(f.session); old.socket.onclose(); old.receive(event(1))
  assert.equal(f.connections.has('task'),true)
  assert.equal(f.timers.size,0)
  assert.equal(f.session.events.length,0)
})
test('REST reconciliation buffers live events and deduplicates persisted ones', async () => {
  const request=deferred(),f=fixture(() => request.promise)
  f.connections.open(f.session,true); const s=f.sockets[0]; s.socket.open()
  s.receive(event(1)); s.receive(event(2)); request.resolve({status:'running',events:[event(1)]})
  await flush()
  assert.deepEqual(f.session.events,[event(1),event(2)])
  s.receive(event(3)); assert.equal(f.session.events.length,3)
})
test('a terminal event during REST recovery wins over an older running snapshot', async () => {
  const request=deferred(),f=fixture(() => request.promise)
  f.connections.open(f.session,true); const s=f.sockets[0]; s.socket.open()
  s.receive(event(1,'task_result')); request.resolve({status:'running',events:[]}); await flush()
  assert.equal(f.session.status,'completed'); assert.equal(f.connections.has('task'),false)
  assert.equal(f.timers.size,0)
})
test('recovery notices a task that finished while disconnected', async () => {
  const f=fixture(async () => ({status:'completed',events:[event(1,'task_result')]}))
  f.connections.open(f.session,true); f.sockets[0].socket.open(); await flush()
  assert.equal(f.session.status,'completed'); assert.equal(f.connections.has('task'),false)
})

test('recovery restores a durable clarification and closes without reconnecting or marking completion', async () => {
  const question={id:'question',message:'缺少相关历史依据'}
  const f=fixture(async()=>({status:'awaiting_input',clarification:question,events:[event(1,'task_clarification')]}))
  f.connections.open(f.session,true);f.sockets[0].socket.open();await flush()
  assert.equal(f.session.status,'awaiting_input')
  assert.deepEqual(f.session.clarification,question)
  assert.equal(f.connections.has('task'),false)
  assert.equal(f.timers.size,0)
})
test('intentional close cancels retries and stale REST responses', async () => {
  const request=deferred(),f=fixture(() => request.promise)
  f.connections.open(f.session,true); f.sockets[0].socket.open(); f.connections.close('task')
  request.resolve({status:'completed',events:[event(1)]}); await flush()
  assert.equal(f.session.status,'running'); assert.equal(f.session.events.length,0)
  f.connections.open(f.session); f.sockets[1].socket.close(); assert.equal(f.timers.size,1)
  f.connections.close('task'); assert.equal(f.timers.size,0)
})
test('REST failure preserves buffered events and schedules another recovery', async () => {
  const request=deferred(),f=fixture(() => request.promise)
  f.connections.open(f.session,true); f.sockets[0].socket.open(); f.sockets[0].receive(event(1))
  request.reject(new Error('temporary')); await flush()
  assert.deepEqual(f.session.events,[event(1)]); assert.equal(f.timers.size,1)
})
test('PDF preview excludes old execution outputs and picks the latest current report', () => {
  const start='2026-10-23T01:00:00Z',seconds=Date.parse(start)/1000
  const files=[{name:'old.pdf',type:'file',mtime:seconds-1},
    {name:'new.pdf',type:'file',mtime:seconds+1},
    {name:'latest.PDF',type:'file',mtime:seconds+2},
    {name:'folder.pdf',type:'dir',mtime:seconds+3}]
  assert.equal(selectCurrentPdf(files,start).name,'latest.PDF')
  assert.equal(selectCurrentPdf(files.slice(0,1),start),null)
  assert.equal(selectCurrentPdf(files,''),null)
})
test('socket closed before opening rejects promptly instead of waiting ten seconds', async () => {
  const before=globalThis.WebSocket; globalThis.WebSocket={OPEN:1,CLOSING:2}
  try { const socket=new Socket(); const waiting=waitForWS(socket); socket.close(); await assert.rejects(waiting,/已断开/) }
  finally { globalThis.WebSocket=before }
})
