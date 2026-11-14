<template>
  <section class="conversation-panel" aria-label="业务对话与分层记忆">
    <header class="conversation-heading">
      <div class="conversation-title"><span class="title-mark"><el-icon aria-hidden="true"><ChatLineRound /></el-icon></span><h2>案件对话</h2><span class="turn-count">{{ data?.total || 0 }} 轮</span></div>
      <div class="heading-actions">
        <button class="quiet-button memory-button" type="button" aria-label="查看会话记忆" :disabled="!data?.memory" @click="inspector='memory'"><el-icon aria-hidden="true"><Collection /></el-icon><span>会话记忆</span></button>
        <span class="toolbar-divider" aria-hidden="true"></span>
        <button class="quiet-button new-task-button" type="button" @click="emit('new-task')"><el-icon aria-hidden="true"><Plus /></el-icon><span>新任务</span></button>
      </div>
    </header>

    <div class="conversation-navigation">
      <div class="turn-jump"><el-icon aria-hidden="true"><Clock /></el-icon><label class="sr-only" :for="`turn-jump-${session.thread_id}`">跳转对话</label><select :id="`turn-jump-${session.thread_id}`" v-model="jumpTarget" @change="jumpToTurn(jumpTarget)"><option value="">浏览对话记录</option><option v-for="turn in data?.turns || []" :key="turn.turn_id" :value="turn.turn_id">第 {{ turn.ordinal }} 轮 · {{ turn.user_text.slice(0,28) }}</option></select><el-icon class="select-arrow" aria-hidden="true"><ArrowDown /></el-icon></div>
      <span class="sync-state"><el-icon aria-hidden="true"><Loading v-if="loading" class="is-loading"/><Check v-else /></el-icon>{{ loading ? '同步中' : data ? '记录已保存' : '等待读取' }}</span>
    </div>

    <div ref="scroll" class="conversation-scroll" tabindex="0" aria-label="逐轮对话记录，可滚动">
      <div class="message-column">
        <button v-if="data?.next_before" class="load-earlier quiet-button" type="button" :disabled="loading" @click="load(true)"><el-icon aria-hidden="true"><Clock /></el-icon>{{ loading ? '读取中…' : '查看更早的对话' }}</button>
        <div v-if="loading && !data" class="conversation-empty" role="status"><el-icon class="is-loading"><Loading /></el-icon><p>正在恢复对话记录…</p></div>
        <div v-else-if="!data?.turns.length" class="conversation-empty"><el-icon><ChatLineRound /></el-icon><h3>从当前结果继续</h3><p>补充条件、核对依据，或进一步缩小案件范围。</p></div>
        <article v-for="turn in data?.turns || []" :key="turn.turn_id" :data-turn-id="turn.turn_id" class="conversation-turn" :aria-label="`第 ${turn.ordinal} 轮对话`">
          <div v-if="turn.kind === 'new_topic'" class="topic-break"><span></span><span>新话题</span><span></span></div>
          <div class="user-message">
            <div class="user-meta"><span>你</span><time :datetime="turn.created_at">{{ formatTime(turn.created_at) }}</time></div>
            <p>{{ turn.user_text }}</p>
          </div>
          <div class="assistant-message">
            <div class="assistant-heading"><span class="assistant-mark"><el-icon aria-hidden="true"><ChatLineRound /></el-icon></span><strong>案件助手</strong><span class="message-turn">第 {{ turn.ordinal }} 轮</span><span class="message-status" :class="{'status-warning':turn.snapshot?.degraded || ['failed','awaiting_input'].includes(turn.status)}"><el-icon aria-hidden="true"><Loading v-if="turn.status === 'running'" class="is-loading"/><Warning v-else-if="turn.snapshot?.degraded || turn.status === 'failed'"/><Check v-else /></el-icon>{{ statusLabel(turn.status,turn.snapshot?.degraded) }}</span></div>
            <div class="assistant-content">
              <div v-if="turn.kind === 'history'" class="history-notice"><el-icon aria-hidden="true"><Clock /></el-icon>{{ historyNotice(turn) }}</div>
              <p v-if="turn.snapshot?.degraded" class="answer-notice"><el-icon aria-hidden="true"><Warning /></el-icon>这轮结果尚未通过完整校验，请结合来源和未完成项阅读。</p>
              <div v-if="turn.snapshot?.result" class="answer-reading" :class="{'answer-collapsed':isLongAnswer(turn) && !expandedAnswers.has(turn.turn_id)}">
                <div class="md-body historical-answer" v-html="render(answerText(turn))" @click="onAnswerClick($event,turn)"></div>
              </div>
              <div v-else class="empty-answer" :class="{'answer-failure':turn.status === 'failed'}"><span v-if="turn.status === 'running'" class="thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span><p>{{ turn.status === 'running' ? '正在核对条件与来源…' : turn.snapshot?.error || '本轮尚无可展示的回答。' }}</p></div>
              <button v-if="turn.snapshot?.result && isLongAnswer(turn)" class="expand-answer" type="button" :aria-expanded="expandedAnswers.has(turn.turn_id)" @click="toggleAnswer(turn.turn_id)">{{ expandedAnswers.has(turn.turn_id) ? '收起长回答' : '展开完整回答' }}<el-icon :class="{'rotated':expandedAnswers.has(turn.turn_id)}" aria-hidden="true"><ArrowDown /></el-icon></button>
              <div class="answer-tools" aria-label="本轮回答操作">
                <button v-if="turn.snapshot?.evidence?.length" class="source-button" type="button" @click="inspectTurn('sources',turn)"><el-icon aria-hidden="true"><Link /></el-icon>{{ turn.snapshot.evidence.length }} 个来源</button>
                <button type="button" class="quiet-button" @click="inspectTurn('task',turn)"><el-icon aria-hidden="true"><Document /></el-icon>本轮口径</button>
                <button v-if="turn.snapshot?.result" class="quiet-button icon-action" type="button" :aria-label="`复制第 ${turn.ordinal} 轮回答`" :title="copiedTurn === turn.turn_id ? '已复制' : '复制回答'" @click="copyTurn(turn)"><el-icon aria-hidden="true"><Check v-if="copiedTurn === turn.turn_id"/><CopyDocument v-else /></el-icon></button>
                <el-dropdown v-if="turn.status !== 'running'" trigger="click" @command="onTurnAction($event,turn)"><button class="quiet-button icon-action" type="button" :aria-label="`第 ${turn.ordinal} 轮更多操作`"><el-icon aria-hidden="true"><MoreFilled /></el-icon></button><template #dropdown><el-dropdown-menu><el-dropdown-item command="reference">基于这轮追问</el-dropdown-item><el-dropdown-item command="history">回看这轮快照</el-dropdown-item><el-dropdown-item command="rule" :disabled="busy || drafting">拟为项目长期规则</el-dropdown-item></el-dropdown-menu></template></el-dropdown>
              </div>
            </div>
          </div>
        </article>
      </div>
    </div>

    <div class="composer-area">
      <div class="composer-column">
        <div v-if="data?.pending" class="pending-notice" role="status"><el-icon aria-hidden="true"><Warning /></el-icon><div><strong>需要补充一点信息</strong><p>{{ data.pending.question }}</p></div></div>
        <p v-if="error && error !== data?.pending?.question" class="error" role="alert">{{ error }} <button v-if="loadFailed" class="quiet-button" type="button" @click="load()">重新读取</button></p>
        <form class="followup-form" :class="{'is-busy':busy || session.status === 'running'}" @submit.prevent="send">
          <div v-if="reference" class="reference-context"><el-icon aria-hidden="true"><Link /></el-icon><span>参考第 {{ referenceTurn?.ordinal || '—' }} 轮：{{ referenceTurn?.user_text }}</span><button type="button" class="quiet-button icon-action" aria-label="取消指定参考轮次" @click="reference=''" :disabled="busy"><el-icon><Close /></el-icon></button></div>
          <label class="sr-only" :for="`followup-${session.thread_id}`">{{ mode === 'history' ? '读取历史快照，不执行新的分析' : '继续追问或补充条件' }}</label>
          <textarea :id="`followup-${session.thread_id}`" ref="input" v-model="text" :rows="2" maxlength="4000" :disabled="busy || session.status === 'running'" :placeholder="composerPlaceholder" @keydown.ctrl.enter.prevent="send" @keydown.meta.enter.prevent="send"></textarea>
          <div class="composer-toolbar">
            <div class="composer-controls">
              <div class="composer-select" :class="{'selected-mode':mode !== 'continue'}"><el-icon aria-hidden="true"><Clock v-if="mode === 'history'"/><RefreshRight v-else-if="mode === 'new_topic'"/><ChatLineRound v-else /></el-icon><label class="sr-only" :for="`conversation-mode-${session.thread_id}`">本轮方式</label><select :id="`conversation-mode-${session.thread_id}`" v-model="mode" :disabled="busy"><option value="continue">继续追问</option><option value="new_topic">切换话题</option><option value="history">回看快照</option></select><el-icon class="select-arrow" aria-hidden="true"><ArrowDown /></el-icon></div>
              <span class="composer-divider" aria-hidden="true"></span>
              <div class="composer-select reference-select"><el-icon aria-hidden="true"><Link /></el-icon><label class="sr-only" :for="`conversation-reference-${session.thread_id}`">参考轮次</label><select :id="`conversation-reference-${session.thread_id}`" v-model="reference" :disabled="busy || mode === 'new_topic'"><option value="">最近一轮</option><option v-for="turn in data?.turns.filter(t=>t.snapshot) || []" :key="turn.turn_id" :value="turn.turn_id">第 {{ turn.ordinal }} 轮 · {{ turn.user_text.slice(0,24) }}</option></select><el-icon class="select-arrow" aria-hidden="true"><ArrowDown /></el-icon></div>
            </div>
            <div class="composer-submit"><span v-if="text.length > 3200" class="character-count">{{ text.length }}/4000</span><button type="submit" class="send-button" :disabled="!data || loadFailed || session.preparing || busy || session.status === 'running' || (mode !== 'history' && !text.trim())" :aria-label="mode === 'history' ? '读取历史快照' : '发送追问'" :title="mode === 'history' ? '读取历史快照' : '发送追问（Ctrl / ⌘ + Enter）'"><el-icon aria-hidden="true"><Loading v-if="busy || session.status === 'running'" class="is-loading"/><ArrowUp v-else /></el-icon></button></div>
          </div>
        </form>
        <div class="composer-footnote"><span>{{ mode === 'new_topic' ? '新话题将重置本会话的筛选条件' : mode === 'history' ? '回看原始记录，不重新查询' : '沿用当前口径 · 动态信息重新查询' }}</span><span class="keyboard-hint">Ctrl / ⌘ + Enter 发送</span></div>
      </div>
    </div>

    <el-drawer v-model="inspectorVisible" class="conversation-inspector" :class="{'memory-inspector':inspector === 'memory'}" :title="inspectorTitle" :size="inspector === 'memory' ? 'min(520px, 100vw)' : 'min(440px, 100vw)'">
      <template v-if="inspector === 'memory' && data?.memory">
        <ConversationMemory :key="session.thread_id" :memory="data.memory" :thread-id="session.thread_id" :turns="data.turns" @open-turn="openMemoryTurn" />
      </template>
      <template v-else-if="selectedTurn && inspector === 'sources'">
        <p class="inspector-intro">第 {{ selectedTurn.ordinal }} 轮 · {{ selectedTurn.snapshot?.evidence?.length || 0 }} 个来源<br>编号、位置和版本均对应本轮回答。</p>
        <article v-for="source in selectedTurn.snapshot?.evidence || []" :key="source.id" class="turn-source" :class="{'source-highlight':source.id === highlightedSource}"><header><span class="source-id">{{ source.id }}</span><h3>{{ source.title || source.case_id || '查询来源' }}</h3></header><p v-if="source.case_id" class="inspector-muted">{{ source.case_id }}</p><p class="source-path">{{ source.source || '结构化查询记录' }}</p><blockquote v-if="sourceExcerpt(source)">{{ sourceExcerpt(source) }}</blockquote><dl v-if="Object.keys(sourceConditions(source)).length" class="condition-list source-conditions"><template v-for="(value,key) in sourceConditions(source)" :key="key"><dt>{{ conditionLabel(key) }}</dt><dd>{{ value }}</dd></template></dl><div v-if="source.columns?.length && source.rows?.length" class="source-data-scroll" tabindex="0" aria-label="本轮来源数据预览，可左右滚动"><table><thead><tr><th v-for="(column,index) in source.columns" :key="index">{{ column }}</th></tr></thead><tbody><tr v-for="(row,index) in source.rows.slice(0,5)" :key="index"><td v-for="(column,columnIndex) in source.columns" :key="columnIndex">{{ Array.isArray(row) ? row[columnIndex] ?? '未记录' : row[column] ?? '未记录' }}</td></tr></tbody></table></div><p v-if="source.rows?.length" class="inspector-muted">已保存 {{ source.rows.length }} 行{{ source.rows.length > 5 ? '，此处预览前 5 行' : '' }}{{ source.completeness?.has_more ? '；查询范围尚有更多记录' : '' }}。</p><dl class="source-facts"><dt>定位</dt><dd>{{ typeof source.location === 'object' ? JSON.stringify(source.location) : source.location || '未单独记录' }}</dd><dt>版本</dt><dd>{{ source.revision || source.content_sha256 || '来源未记录版本' }}</dd></dl><details class="raw-record"><summary>查看完整来源记录</summary><pre>{{ JSON.stringify(source,null,2) }}</pre></details></article>
      </template>
      <template v-else-if="selectedTurn && inspector === 'task'">
        <p class="inspector-intro">第 {{ selectedTurn.ordinal }} 轮 · {{ kindLabel(selectedTurn.kind) }}</p>
        <section class="task-inspection"><h3>你的要求</h3><p>{{ selectedTurn.user_text }}</p><h3>本轮任务</h3><p class="literal">{{ selectedTurn.state.goal || selectedTurn.task }}</p><details class="raw-record"><summary>查看完整任务记录</summary><p class="literal">{{ selectedTurn.task }}</p></details><h3>本轮筛选与口径</h3><dl v-if="Object.keys(selectedTurn.state.conditions || {}).length" class="condition-list"><template v-for="(value,key) in selectedTurn.state.conditions" :key="key"><dt>{{ conditionLabel(key) }}</dt><dd>{{ value }}</dd></template></dl><p v-else class="inspector-muted">没有附加筛选条件。</p><h3>案件范围</h3><div v-if="selectedTurn.state.case_ids?.length" class="case-chips"><span v-for="id in selectedTurn.state.case_ids" :key="id">{{ id }}</span></div><p v-else class="inspector-muted">{{ selectedTurn.state.case_ids == null ? '未限定具体案号集合。' : '本轮限定范围为空。' }}</p><p v-for="item in selectedTurn.state.unresolved || []" :key="item" class="inspector-warning">待核实：{{ item }}</p></section>
      </template>
    </el-drawer>
    <RuleEditor v-model:visible="editorVisible" :draft="ruleDraft?.draft" :source-thread="ruleDraft?.source_thread_id" :source-turn="ruleDraft?.source_turn_id" :source-snapshot="ruleDraft?.source_snapshot" :source-query="ruleDraft?.source_query" @saved="toast('success','项目长期规则已保存，后续匹配任务可采用')" />
  </section>
</template>

<script setup>
import {ref, computed, watch, nextTick} from 'vue'
import {ElMessageBox} from 'element-plus'
import {parseResultMarkdown} from '../resultMarkdown'
import {ChatLineRound,Collection,Plus,Clock,ArrowDown,ArrowUp,Loading,Check,Warning,Link,Document,CopyDocument,MoreFilled,Close,RefreshRight} from '@element-plus/icons-vue'
import DOMPurify from 'dompurify'
import {getConversation,getConversationTurn,submitTask,draftTurnRule} from '../api'
import {toast} from '../store'
import RuleEditor from './RuleEditor.vue'
import ConversationMemory from './ConversationMemory.vue'
const props=defineProps({session:{type:Object,required:true},active:Boolean})
const emit=defineEmits(['refresh','new-task'])
const scroll=ref(null),input=ref(null)
const inspector=ref(''),selectedTurnId=ref(''),highlightedSource=ref(''),copiedTurn=ref(''),jumpTarget=ref('')
const expandedAnswers=ref(new Set())
const data=ref(null),text=ref(''),mode=ref('continue'),reference=ref(''),error=ref(''),busy=ref(false),loading=ref(false),loadFailed=ref(false)
const editorVisible=ref(false),ruleDraft=ref(null),drafting=ref(false)
let generation=0
const render=value=>DOMPurify.sanitize(parseResultMarkdown((value || '').replace(/\[(S[1-9]\d*)\]/g,(_,id)=>`<button type="button" class="citation-button" data-source="${id}" aria-label="查看来源 ${id}">${id}</button>`)))
const selectedTurn=computed(()=>data.value?.turns.find(t=>t.turn_id===selectedTurnId.value))
const referenceTurn=computed(()=>data.value?.turns.find(t=>t.turn_id===reference.value))
const inspectorVisible=computed({get:()=>Boolean(inspector.value),set:value=>{if(!value)inspector.value=''}})
const inspectorTitle=computed(()=>({memory:'会话记忆',sources:`第 ${selectedTurn.value?.ordinal || '—'} 轮来源`,task:'本轮口径'}[inspector.value] || '对话详情'))
const composerPlaceholder=computed(()=>mode.value==='new_topic'?'开始一个新话题…':mode.value==='history'?'选择参考轮次，回看当时的完整回答…':data.value?.pending?'补充具体案号或查询条件…':'继续追问，或补充案件筛选条件…')
const conditionLabel=key=>({owner:'承办人',client:'客户',stage:'案件状态',domain:'技术领域',due_field:'期限节点',due_status:'期限条件',missing_date_field:'日期缺口',start_month:'开始月份',end_month:'结束月份',as_of:'基准日',output_format:'输出格式',fields:'展示字段',metric:'统计指标',time_range:'时间范围'}[key] || key)
function parsedSnippet(source){try{const value=JSON.parse(source.snippet || '');return value && typeof value==='object'?value:null}catch{return null}}
function sourceExcerpt(source){return source.excerpt || (!parsedSnippet(source)?source.snippet:'') || ''}
function sourceConditions(source){const values=source.casework_filters || parsedSnippet(source) || {};return Object.fromEntries(Object.entries(values).filter(([key,value])=>['owner','client','stage','domain','due_field','due_status','as_of','start_month','end_month','missing_date_field'].includes(key) && value != null && value !== ''))}
const formatTime=value=>new Date(value).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})
const snapshotHeader=/^历史快照 · 第(\d+)轮 · ([^\n]+)\n\n以下为当时原回答，未重新核验，不代表当前状态。\n\n/
const answerText=turn=>turn.kind==='history'?(turn.snapshot?.result || '').replace(snapshotHeader,''):turn.snapshot?.result || ''
function historyNotice(turn){const match=(turn.snapshot?.result || '').match(snapshotHeader);return match?`第 ${match[1]} 轮快照 · ${new Date(match[2]).toLocaleDateString('zh-CN')} · 未查询当前数据`:'历史快照 · 以下为当时记录，未查询当前数据'}
const isLongAnswer=turn=>(turn.snapshot?.result || '').length>1500 || (turn.snapshot?.result || '').split('\n').length>22
function toggleAnswer(id){const next=new Set(expandedAnswers.value);if(next.has(id))next.delete(id);else next.add(id);expandedAnswers.value=next}
function inspectTurn(type,turn,source=''){selectedTurnId.value=turn.turn_id;highlightedSource.value=source;inspector.value=type}
async function openMemoryTurn(item){
  const id=props.session.thread_id
  try{
    const turn=await getConversationTurn(id,item.turn_id)
    if(id!==props.session.thread_id || !data.value)return
    data.value.turns=[...new Map([...data.value.turns,turn].map(t=>[t.turn_id,t])).values()].sort((a,b)=>a.ordinal-b.ordinal)
    inspector.value='';jumpTarget.value=turn.turn_id;await jumpToTurn(turn.turn_id)
  }catch(e){toast('error',e.message || '历史轮次暂不可用')}
}
function onAnswerClick(event,turn){const button=event.target.closest('[data-source]');if(button){event.preventDefault();inspectTurn('sources',turn,button.dataset.source)}}
async function copyTurn(turn){try{await navigator.clipboard.writeText(turn.snapshot.result);copiedTurn.value=turn.turn_id}catch{toast('error','复制失败，请选择文本手动复制')}}
async function jumpToTurn(id){await nextTick();const node=scroll.value?.querySelector(`[data-turn-id="${id}"]`);if(node)scroll.value.scrollTop+=node.getBoundingClientRect().top-scroll.value.getBoundingClientRect().top-12}
async function onTurnAction(action,turn){if(action==='rule')return draftRule(turn);mode.value=action==='history'?'history':'continue';reference.value=turn.turn_id;await nextTick();input.value?.focus()}

const kindLabel=value=>({task:'任务',followup:'追问',retry:'重新执行',revision:'修改任务',clarification:'补充条件',new_topic:'新话题',history:'历史快照'}[value] || value)
const statusLabel=(value,degraded=false)=>value==='completed' && degraded?'未达标':({completed:'已完成',running:'执行中',failed:'失败',cancelled:'已终止',awaiting_input:'待补充'}[value] || value)
async function load(more=false){
  const id=props.session.thread_id,token=++generation
  loading.value=true
  try{
    const next=await getConversation(id,more?data.value?.next_before:null)
    if(token!==generation || id!==props.session.thread_id)return
    if(more)next.turns=[...new Map([...next.turns,...data.value.turns].map(t=>[t.turn_id,t])).values()].sort((a,b)=>a.ordinal-b.ordinal)
    const changed=data.value?.current_turn_id!==next.current_turn_id
    data.value=next;loadFailed.value=false
    if(changed && !more){jumpTarget.value=next.current_turn_id;await jumpToTurn(next.current_turn_id)}
  }catch(e){if(token===generation){loadFailed.value=true;error.value=e.message}}
  finally{if(token===generation)loading.value=false}
}
watch(()=>props.session.thread_id,()=>{generation++;data.value=null;text.value='';reference.value='';mode.value='continue';error.value='';editorVisible.value=false;inspector.value='';expandedAnswers.value=new Set();jumpTarget.value=''})
watch(()=>[props.active,props.session.thread_id,props.session.execution_id,props.session.status],()=>{if(props.active)load()},{immediate:true})
watch(mode,value=>{if(value==='new_topic')reference.value=''})
async function send(){
  if(busy.value || !data.value || loadFailed.value || props.session.preparing || props.session.status==='running' || (mode.value!=='history' && !text.value.trim()))return
  const session=props.session,id=session.thread_id,parent=data.value.current_turn_id
  const followup={text:text.value.trim() || '查看本轮原始快照',mode:mode.value,expected_execution_id:session.execution_id,expected_turn_id:parent,reference_turn_id:reference.value || null}
  busy.value=true;error.value=''
  try{
    const skips=[]
    let accepted=false
    for(let i=0;i<4;i++){
      try{
        await submitTask(session.query,id,session.task_query || session.query,false,session.max_rounds || 3,session.intent_revisions?.[0]?.id ?? null,skips,session.round_mode || 'manual',session.allowed_sources || ['db_query','knowledge'],null,null,followup)
        accepted=true;break
      }catch(e){
        if(e.detail?.code!=='rule_conflict')throw e
        const conflicts=e.detail.rule_context.conflicts || []
        await ElMessageBox.confirm(conflicts.map(r=>`${r.title}（v${r.revision}）：${r.reason}`).join('\n'),'核对长期规则',{confirmButtonText:'本次跳过并继续',cancelButtonText:'返回修改'})
        for(const rule of conflicts)if(!skips.some(r=>r.id===rule.id && r.revision===rule.revision))skips.push({id:rule.id,revision:rule.revision})
      }
    }
    if(!accepted)throw new Error('规则持续变化，请刷新后重试')
    if(id===props.session.thread_id){text.value='';reference.value='';emit('refresh',id);await load()}
  }catch(e){
    if(id!==props.session.thread_id)return
    error.value=typeof e==='string'?'已取消提交，历史记录未改变':e.message
    await load()
    // Reconcile a lost response before allowing another submission.
    if(data.value?.current_turn_id!==parent){emit('refresh',id);error.value='已同步服务端最新轮次，请查看结果后继续。'}
  }finally{busy.value=false}
}
async function draftRule(turn){
  const id=props.session.thread_id;drafting.value=true;error.value=''
  try{const draft=await draftTurnRule(id,turn.turn_id);if(id===props.session.thread_id){ruleDraft.value=draft;editorVisible.value=true}}
  catch(e){if(id===props.session.thread_id)error.value=e.message}
  finally{drafting.value=false}
}
</script>

<style scoped>
.conversation-panel{
  --chat-muted:color-mix(in srgb,var(--text) 72%,var(--card-bg));
  --chat-answer:var(--card-bg);
  --chat-canvas:color-mix(in srgb,var(--bg) 38%,var(--card-bg));
  --chat-user:color-mix(in srgb,var(--primary) 6%,var(--card-bg));
  --chat-user-border:color-mix(in srgb,var(--primary) 14%,var(--card-bg));
  --chat-surface:color-mix(in srgb,var(--bg) 78%,var(--card-bg));
  --chat-line:color-mix(in srgb,var(--text) 12%,var(--card-bg));
  display:flex;
  flex-direction:column;
  height:100%;
  min-height:0;
  color:var(--text);
  font-size:15px;
  font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif
}
.conversation-panel button,.conversation-panel select,.conversation-panel textarea{
  font:inherit
}
.conversation-panel button{
  cursor:pointer
}
.conversation-panel button:disabled{
  cursor:default
}
.conversation-panel :is(button,select,textarea,summary):focus-visible{
  outline:2px solid var(--primary);
  outline-offset:3px
}
.sr-only{
  position:absolute;
  width:1px;
  height:1px;
  padding:0;
  margin:-1px;
  overflow:hidden;
  clip:rect(0,0,0,0);
  white-space:nowrap;
  border:0
}
.conversation-heading{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  min-height:64px;
  flex-shrink:0;
  border-bottom:1px solid var(--border);
  padding:0 16px
}
.conversation-title,.heading-actions{
  display:flex;
  align-items:center;
  gap:10px;
  min-width:0
}
.conversation-title h2{
  font-size:18px;
  letter-spacing:.01em;
  margin:0;
  font-weight:600;
  white-space:nowrap
}
.title-mark{
  display:grid;
  place-items:center;
  color:var(--primary);
  font-size:20px
}
.turn-count{
  font-size:12px;
  color:var(--chat-muted);
  border-left:1px solid var(--chat-line);
  padding-left:10px;
  white-space:nowrap
}
.heading-actions{
  gap:8px
}
.toolbar-divider{
  height:16px;
  background:var(--chat-line);
  width:1px;
  margin:0 2px
}
.quiet-button{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  gap:6px;
  background:transparent;
  border:0;
  border-radius:6px;
  min-height:32px;
  padding:5px 8px;
  color:var(--chat-muted);
  font-size:13px!important;
  white-space:nowrap;
  line-height:1.4
}
.quiet-button:hover{
  background:var(--chat-surface);
  color:var(--text)
}
.quiet-button:disabled{
  opacity:.45
}
.quiet-button .el-icon{
  font-size:15px
}
.new-task-button{
  color:var(--text)
}
.conversation-navigation{
  display:flex;
  align-items:center;
  justify-content:space-between;
  min-height:38px;
  padding:0 18px;
  flex-shrink:0;
  color:var(--chat-muted);
  font-size:12px
}
.turn-jump{
  position:relative;
  display:flex;
  align-items:center;
  gap:6px;
  max-width:65%
}
.turn-jump>select{
  border:0;
  outline:none;
  background:transparent;
  color:inherit;
  appearance:none;
  max-width:230px;
  width:100%;
  padding:6px 18px 6px 0;
  cursor:pointer;
  font-size:12px
}
.select-arrow{
  font-size:10px!important;
  pointer-events:none
}
.turn-jump .select-arrow{
  position:absolute;
  right:2px
}
.sync-state{
  display:flex;
  align-items:center;
  gap:5px;
  font-size:12px;
  white-space:nowrap
}
.sync-state .el-icon{
  font-size:12px
}
.conversation-scroll{
  flex:1;
  min-height:0;
  overflow:auto;
  overscroll-behavior:contain;
  scrollbar-gutter:stable;
  background:var(--chat-canvas);
  border-radius:12px 12px 0 0
}
.message-column{
  max-width:860px;
  margin:0 auto;
  padding:24px 28px 32px;
  min-width:0
}
.conversation-turn{
  margin-bottom:32px;
  scroll-margin-top:18px
}
.conversation-turn:last-child{
  margin-bottom:0
}
.load-earlier{
  margin:0 auto 22px;
  display:flex;
  color:var(--chat-muted)
}
.conversation-empty{
  padding:50px 20px;
  text-align:center;
  color:var(--chat-muted)
}
.conversation-empty>.el-icon{
  font-size:25px;
  color:var(--primary)
}
.conversation-empty h3{
  font-size:16px;
  font-weight:500
}
.conversation-empty p{
  font-size:13px;
  line-height:1.7
}
.topic-break{
  display:flex;
  align-items:center;
  gap:14px;
  margin:28px 0 24px;
  color:var(--chat-muted);
  font-size:11px
}
.topic-break span:first-child,.topic-break span:last-child{
  flex:1;
  height:1px;
  background:var(--border)
}
.user-message{
  margin-left:auto;
  width:fit-content;
  max-width:86%;
  margin-bottom:24px
}
.user-meta{
  display:flex;
  gap:9px;
  align-items:center;
  justify-content:flex-end;
  font-size:12px;
  color:var(--chat-muted);
  margin-bottom:8px;
  padding-right:3px
}
.user-meta>span{
  color:var(--text);
  font-size:13px;
  font-weight:500
}
.user-message>p{
  margin:0;
  background:var(--chat-user);
  border:1px solid var(--chat-user-border);
  box-shadow:0 2px 5px color-mix(in srgb,var(--primary) 3%,transparent);
  border-radius:18px 18px 5px 18px;
  padding:14px 18px;
  white-space:pre-wrap;
  overflow-wrap:anywhere;
  line-height:1.8;
  font-size:15px
}
.assistant-heading{
  display:flex;
  align-items:center;
  gap:8px;
  min-height:26px;
  margin-bottom:12px
}
.assistant-mark{
  height:30px;
  width:30px;
  display:grid;
  place-items:center;
  border:1px solid color-mix(in srgb,var(--primary) 20%,var(--border));
  color:var(--on-primary);
  border-radius:10px;
  background:var(--primary);
  font-size:17px
}
.assistant-heading>strong{
  font-size:14px;
  font-weight:600
}
.message-turn{
  font-size:12px;
  color:var(--chat-muted);
  margin-left:2px
}
.message-status{
  margin-left:auto;
  font-size:12px;
  display:flex;
  gap:4px;
  align-items:center;
  color:var(--chat-muted)
}
.message-status.status-warning{
  color:var(--status-warn)
}
.assistant-content{
  margin-left:40px;
  min-width:0;
  padding:17px 20px 12px;
  background:var(--chat-answer);
  border:1px solid var(--chat-line);
  border-radius:5px 18px 18px 18px;
  box-shadow:0 2px 7px color-mix(in srgb,var(--text) 3%,transparent)
}
.historical-answer{
  font-size:15px;
  line-height:1.85;
  overflow-wrap:anywhere;
  color:var(--text)
}
.historical-answer :deep(p){
  margin:0 0 13px
}
.historical-answer :deep(p:last-child){
  margin-bottom:0
}
.historical-answer :deep(h1),.historical-answer :deep(h2),.historical-answer :deep(h3),.historical-answer :deep(h4){
  font-size:16px;
  line-height:1.65;
  font-weight:600;
  margin:20px 0 10px
}
.historical-answer :deep(:is(h1,h2,h3,h4):first-child){
  margin-top:0
}
.historical-answer :deep(ul),.historical-answer :deep(ol){
  padding-left:22px;
  margin:10px 0
}
.historical-answer :deep(li){
  margin:5px 0
}
.historical-answer :deep(blockquote){
  margin:14px 0;
  padding:1px 0 1px 14px;
  border-left:2px solid var(--chat-line);
  color:var(--chat-muted)
}
.historical-answer :deep(pre){
  overflow:auto;
  background:var(--chat-surface);
  border:1px solid var(--border);
  padding:12px;
  border-radius:8px;
  font-size:12px
}
.historical-answer :deep(a){
  color:var(--primary);
  text-underline-offset:3px
}
.historical-answer :deep(.result-table-scroll){
  max-width:100%;
  overflow:auto;
  margin:16px 0 18px;
  border:1px solid var(--chat-line);
  border-radius:9px;
  scrollbar-width:thin
}
.historical-answer :deep(table){
  border-collapse:collapse;
  width:100%;
  font-size:13px;
  line-height:1.7;
  text-align:left;
  margin:0
}
.historical-answer :deep(th){
  font-weight:600;
  color:var(--chat-muted);
  background:var(--chat-surface);
  white-space:nowrap
}
.historical-answer :deep(th),.historical-answer :deep(td){
  padding:10px 13px;
  border-bottom:1px solid var(--border);
  min-width:72px;
  vertical-align:top
}
.historical-answer :deep(tr:last-child td){
  border-bottom:0
}
.historical-answer :deep(.result-table-atomic){
  white-space:nowrap
}
.historical-answer :deep(.citation-button){
  display:inline-flex;
  align-items:center;
  justify-content:center;
  font:inherit;
  font-size:11px;
  line-height:1.35;
  border:0;
  border-radius:4px;
  background:color-mix(in srgb,var(--primary) 8%,var(--card-bg));
  color:var(--primary);
  padding:1px 5px;
  margin:0 2px;
  cursor:pointer;
  vertical-align:baseline
}
.historical-answer :deep(.citation-button:hover){
  background:color-mix(in srgb,var(--primary) 16%,var(--card-bg))
}
.answer-reading{
  position:relative;
  min-width:0
}
.answer-collapsed{
  max-height:320px;
  overflow:hidden
}
.answer-collapsed:after{
  content:'';
  position:absolute;
  inset:auto 0 0;
  height:46px;
  background:linear-gradient(transparent,var(--card-bg));
  pointer-events:none
}
.expand-answer{
  display:flex;
  align-items:center;
  gap:5px;
  background:none;
  border:0;
  padding:8px 0;
  color:var(--primary);
  font-size:12px!important
}
.expand-answer .el-icon{
  font-size:11px;
  transition:transform .15s
}
.rotated{
  transform:rotate(180deg)
}
.history-notice,.answer-notice{
  display:flex;
  align-items:flex-start;
  gap:7px;
  font-size:12px;
  line-height:1.7;
  color:var(--chat-muted);
  margin:0 0 14px
}
.history-notice .el-icon,.answer-notice .el-icon{
  flex-shrink:0;
  margin-top:3px
}
.answer-notice{
  color:var(--status-warn)
}
.answer-tools{
  display:flex;
  align-items:center;
  gap:6px;
  margin-top:16px;
  padding-top:10px;
  border-top:1px solid var(--border);
  flex-wrap:wrap;
  min-height:30px
}
.answer-tools .quiet-button{
  min-height:28px;
  font-size:12px!important;
  padding:4px 6px
}
.source-button{
  border:1px solid var(--chat-line);
  border-radius:6px;
  color:var(--chat-muted);
  background:transparent;
  display:inline-flex;
  align-items:center;
  gap:5px;
  padding:4px 9px;
  min-height:29px;
  font-size:12px!important;
  margin-right:3px
}
.source-button:hover{
  border-color:var(--primary);
  color:var(--primary)
}
.source-button .el-icon{
  font-size:12px
}
.icon-action{
  width:29px;
  height:29px;
  padding:4px
}
.icon-action .el-icon{
  font-size:14px
}
.empty-answer{
  font-size:13px;
  color:var(--chat-muted);
  display:flex;
  align-items:center;
  gap:10px;
  padding:5px 0
}
.empty-answer p{
  margin:0
}
.answer-failure{
  color:var(--danger-text)
}
.thinking-dots{
  display:inline-flex;
  gap:4px
}
.thinking-dots i{
  height:4px;
  width:4px;
  border-radius:50%;
  background:var(--chat-muted);
  animation:thinking 1.1s infinite
}
.thinking-dots i:nth-child(2){
  animation-delay:.15s
}
.thinking-dots i:nth-child(3){
  animation-delay:.3s
}
@keyframes thinking{
  50%{
    opacity:.25
  }
}
.composer-area{
  flex-shrink:0;
  background:var(--card-bg);
  padding:6px 26px 8px;
  position:relative
}
.composer-column{
  max-width:804px;
  margin:0 auto;
  min-width:0
}
.followup-form{
  background:var(--card-bg);
  border:1px solid var(--chat-line);
  border-radius:17px;
  padding:14px 16px 12px;
  box-shadow:0 4px 18px color-mix(in srgb,var(--text) 6%,transparent);
  transition:border-color .15s,box-shadow .15s
}
.followup-form:focus-within{
  border-color:color-mix(in srgb,var(--primary) 65%,var(--chat-line));
  box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 6%,transparent)
}
.followup-form textarea{
  display:block;
  width:100%;
  border:0;
  padding:0 2px 10px;
  resize:none;
  min-height:66px;
  max-height:140px;
  background:transparent;
  color:var(--text);
  font-size:15px;
  line-height:1.7;
  outline:none!important
}
.followup-form textarea::placeholder{
  color:var(--chat-muted);
  opacity:.8
}
.followup-form textarea:disabled{
  opacity:.6
}
.composer-toolbar,.composer-controls,.composer-submit{
  display:flex;
  align-items:center;
  min-width:0;
  gap:8px
}
.composer-toolbar{
  justify-content:space-between
}
.composer-controls{
  gap:6px;
  max-width:calc(100% - 46px)
}
.composer-select{
  display:flex;
  align-items:center;
  position:relative;
  border-radius:7px;
  gap:4px;
  padding:0 7px;
  background:var(--chat-surface);
  min-width:0;
  height:30px;
  color:var(--chat-muted);
  max-width:180px
}
.composer-select>.el-icon:first-child{
  font-size:13px;
  flex-shrink:0
}
.composer-select select{
  border:0;
  padding:5px 15px 5px 0;
  appearance:none;
  background:transparent;
  color:inherit;
  font-size:12px;
  cursor:pointer;
  min-width:0;
  max-width:100%
}
.composer-select .select-arrow{
  position:absolute;
  right:6px
}
.composer-select.selected-mode{
  color:var(--primary);
  background:color-mix(in srgb,var(--primary) 7%,var(--card-bg))
}
.composer-select:has(select:disabled){
  opacity:.5
}
.reference-select{
  background:transparent;
  max-width:180px
}
.composer-divider{
  width:1px;
  height:14px;
  background:var(--chat-line);
  flex-shrink:0
}
.send-button{
  display:grid;
  place-items:center;
  flex-shrink:0;
  width:34px;
  height:34px;
  border:0;
  border-radius:10px;
  color:var(--on-primary);
  background:var(--primary)
}
.send-button>.el-icon{
  font-size:20px
}
.send-button:disabled{
  background:var(--chat-surface);
  color:color-mix(in srgb,var(--text) 35%,var(--card-bg))
}
.send-button:not(:disabled):hover{
  filter:brightness(.92)
}
.character-count{
  font-size:10px;
  color:var(--chat-muted)
}
.composer-footnote{
  display:flex;
  justify-content:space-between;
  gap:12px;
  font-size:11px;
  color:var(--chat-muted);
  line-height:1.7;
  margin-top:7px;
  padding:0 3px
}
.keyboard-hint{
  white-space:nowrap
}
.reference-context{
  display:flex;
  align-items:center;
  gap:7px;
  max-width:100%;
  font-size:11px;
  line-height:1.6;
  color:var(--primary);
  padding:1px 0 8px;
  margin-bottom:7px;
  border-bottom:1px solid var(--border)
}
.reference-context>span{
  white-space:nowrap;
  text-overflow:ellipsis;
  overflow:hidden;
  flex:1
}
.reference-context>.el-icon{
  flex-shrink:0
}
.reference-context>.icon-action{
  width:24px;
  height:24px;
  min-height:24px;
  color:var(--chat-muted)
}
.pending-notice{
  display:flex;
  gap:9px;
  padding:10px 12px;
  margin-bottom:10px;
  background:color-mix(in srgb,var(--status-warn) 7%,var(--card-bg));
  border:1px solid color-mix(in srgb,var(--status-warn) 16%,var(--border));
  border-radius:9px;
  color:var(--status-warn);
  font-size:12px;
  line-height:1.65
}
.pending-notice>.el-icon{
  margin-top:3px;
  flex-shrink:0
}
.pending-notice strong{
  font-weight:500
}
.pending-notice p{
  margin:2px 0 0
}
.error{
  font-size:12px;
  line-height:1.65;
  color:var(--danger-text);
  margin:0 0 8px
}
.error .quiet-button{
  color:inherit;
  padding:0 4px
}
.source-conditions{
  background:var(--bg);
  padding:12px 14px;
  border-radius:7px;
  grid-template-columns:65px minmax(0,1fr)
}
.source-data-scroll{
  max-width:100%;
  overflow:auto;
  border:1px solid var(--border);
  border-radius:7px;
  margin-top:14px
}
.source-data-scroll table{
  border-collapse:collapse;
  font-size:11px;
  line-height:1.7;
  white-space:nowrap
}
.source-data-scroll th,.source-data-scroll td{
  padding:8px 10px;
  border-bottom:1px solid var(--border);
  text-align:left;
  font-weight:400
}
.source-data-scroll th{
  background:var(--bg);
  color:var(--text-secondary)
}
.source-data-scroll tr:last-child td{
  border-bottom:0
}
/* Secondary inspection shares the workbench drawer, leaving the reading column quiet. */
.inspector-intro{
  margin:0 0 24px;
  color:var(--chat-muted,var(--text-secondary));
  font-size:13px;
  line-height:1.8
}
.turn-source h3{
  font-size:14px;
  font-weight:600;
  margin:0;
  line-height:1.6
}
.condition-list{
  display:grid;
  grid-template-columns:86px minmax(0,1fr);
  gap:9px 14px;
  font-size:12px;
  line-height:1.7;
  margin:16px 0
}
.condition-list dt{
  color:var(--text-secondary)
}
.condition-list dd{
  margin:0;
  overflow-wrap:anywhere;
  color:var(--text)
}
.case-chips{
  display:flex;
  flex-wrap:wrap;
  gap:6px;
  margin-top:9px
}
.case-chips span{
  font-size:11px;
  padding:3px 6px;
  background:var(--bg);
  border:1px solid var(--border);
  border-radius:4px;
  font-variant-numeric:tabular-nums
}
.inspector-muted{
  color:var(--text-secondary)!important;
  font-size:12px;
  line-height:1.8
}
.inspector-warning{
  color:var(--status-warn);
  font-size:12px;
  line-height:1.75
}
.turn-source{
  border-top:1px solid var(--border);
  padding:22px 0
}
.turn-source>header{
  display:flex;
  align-items:baseline;
  gap:9px
}
.source-id{
  font-size:11px;
  color:var(--primary);
  background:var(--primary-soft);
  padding:2px 6px;
  border-radius:4px;
  flex-shrink:0
}
.source-highlight{
  border-top-color:var(--primary)
}
.source-path{
  font-size:11px;
  color:var(--text-secondary);
  overflow-wrap:anywhere;
  line-height:1.7
}
.turn-source blockquote{
  margin:15px 0;
  background:var(--bg);
  padding:13px 15px;
  border-radius:6px;
  font-size:12px;
  line-height:1.85;
  overflow-wrap:anywhere
}
.source-facts{
  display:grid;
  grid-template-columns:36px minmax(0,1fr);
  gap:8px;
  font-size:11px;
  line-height:1.7
}
.source-facts dt{
  color:var(--text-secondary)
}
.source-facts dd{
  margin:0;
  overflow-wrap:anywhere;
  color:var(--text)
}
.raw-record{
  margin-top:16px;
  font-size:11px;
  color:var(--text-secondary)
}
.raw-record summary{
  cursor:pointer
}
.raw-record pre{
  font-size:11px;
  line-height:1.6;
  white-space:pre-wrap;
  overflow-wrap:anywhere;
  background:var(--bg);
  padding:12px;
  color:var(--text)
}
.task-inspection>h3{
  font-size:13px;
  font-weight:600;
  margin:24px 0 9px
}
.task-inspection>p{
  font-size:13px;
  line-height:1.85;
  overflow-wrap:anywhere;
  margin:0 0 12px
}
.literal{
  white-space:pre-wrap
}
.task-inspection .condition-list{
  margin:8px 0 20px
}
@media(max-width:650px){
  .conversation-heading{
    padding:0 3px;
    min-height:54px
  }
  .conversation-title{
    gap:7px
  }
  .conversation-title h2{
    font-size:16px
  }
  .title-mark{
    font-size:17px
  }
  .turn-count{
    font-size:10px;
    padding-left:7px
  }
  .heading-actions{
    gap:1px
  }
  .heading-actions .quiet-button{
    padding:5px 6px;
    min-height:38px
  }
  .memory-button>span{
    display:none
  }
  .toolbar-divider{
    display:none
  }
  .conversation-navigation{
    min-height:33px;
    padding:0 4px
  }
  .sync-state{
    font-size:11px
  }
  .turn-jump>select{
    max-width:175px;
    font-size:11px
  }
  .message-column{
    padding:16px 6px 24px
  }
  .conversation-turn{
    margin-bottom:26px
  }
  .user-message{
    max-width:92%;
    margin-bottom:20px
  }
  .user-message>p{
    font-size:14px;
    padding:11px 13px;
    border-radius:13px 13px 3px 13px
  }
  .assistant-heading{
    gap:7px;
    margin-bottom:11px
  }
  .assistant-content{
    margin-left:0;
    padding:14px 13px 10px;
    border-radius:5px 14px 14px 14px
  }
  .historical-answer{
    font-size:14px;
    line-height:1.85
  }
  .historical-answer :deep(table){
    font-size:12px
  }
  .historical-answer :deep(th),.historical-answer :deep(td){
    padding:8px 10px
  }
  .answer-tools{
    gap:4px
  }
  .answer-collapsed{
    max-height:280px
  }
  .composer-area{
    padding:8px 0 1px
  }
  .followup-form{
    padding:10px 10px 9px;
    border-radius:13px
  }
  .followup-form textarea{
    font-size:14px;
    min-height:62px;
    padding-bottom:7px
  }
  .composer-select{
    height:30px;
    padding-left:5px;
    gap:3px;
    max-width:136px
  }
  .reference-select{
    max-width:120px
  }
  .composer-select select{
    font-size:11px;
    padding-right:12px
  }
  .composer-controls{
    gap:3px;
    max-width:calc(100% - 49px)
  }
  .composer-submit{
    gap:4px
  }
  .send-button{
    height:44px;
    width:44px;
    border-radius:11px
  }
  .keyboard-hint{
    display:none
  }
  .composer-footnote{
    font-size:10px;
    margin-top:5px
  }
  .character-count{
    display:none
  }
  .pending-notice{
    font-size:11px;
    padding:8px 10px
  }
  .reference-context{
    font-size:10px
  }
  .condition-list{
    grid-template-columns:74px minmax(0,1fr)
  }
}
@media(max-height:650px){
  .conversation-heading{
    min-height:48px
  }
  .conversation-navigation{
    min-height:30px
  }
  .composer-area{
    padding-top:4px
  }
  .followup-form textarea{
    min-height:42px;
    height:42px;
    padding-bottom:5px
  }
  .followup-form{
    padding-top:8px;
    padding-bottom:7px
  }
  .composer-footnote{
    font-size:10px;
    margin-top:4px
  }
  .pending-notice{
    max-height:64px;
    overflow:auto;
    padding:6px 9px;
    font-size:11px
  }
  .message-column{
    padding-top:10px
  }
  .conversation-turn{
    margin-bottom:26px
  }
}
@media(prefers-reduced-motion:reduce){
  .thinking-dots i{
    animation:none
  }
  .expand-answer .el-icon,.followup-form{
    transition:none
  }
}
</style>
