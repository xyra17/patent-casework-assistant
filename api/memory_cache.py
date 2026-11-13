"""Bounded conversation working set backed by immutable SQLite turn snapshots.

Heat is derived from accepted accesses, not from prefetches. The inverted index
finds old turns without loading their answers; only selected records are read.
All token limits use cl100k_base estimates, including serialized JSON overhead.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
import re

from api import storage, task_sources
from harness.context import TokenCounter


@dataclass(frozen=True)
class Budget:
    hot: int = 2048
    warm: int = 1024
    cold: int = 2048
    total: int = 5632

    def __post_init__(self):
        if min(self.hot, self.warm, self.cold) < 128 or self.total < self.hot + self.warm + self.cold + 128:
            raise ValueError('记忆预算须每层至少128 Token，总量须覆盖三层及128 Token封装余量')

    @classmethod
    def from_env(cls):
        return cls(**{key:int(os.getenv('MEMORY_' + key.upper() + '_TOKENS', str(value)))
                      for key, value in vars(cls()).items()})


@lru_cache(maxsize=1)
def counter():
    return TokenCounter()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def tokens(value):
    return counter().text(dumps(value))


def clip(text, limit):
    encoding = counter().encoding
    ids = encoding.encode(str(text or ''), disallowed_special=())
    if len(ids) <= limit:
        return str(text or '')
    return encoding.decode(ids[:max(0, limit - 5)], errors='ignore') + '…[节选]'


_SOURCES = {'db_query':1, 'knowledge':2, 'web_search':4}
_STOP = {'这些','这个','那个','之前','以前','上次','现在','什么','一下','请问','案件','查询','负责','哪些','如何','是否','情况','继续','保持','条件','的案','案子','please','this','that','with','from','the','and'}


def terms(text):
    text = str(text or '').casefold()
    words = re.findall(r'[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]+', text)
    result = set()
    for word in words:
        if re.fullmatch(r'[\u4e00-\u9fff]+', word):
            result.update(word[i:i+2] for i in range(len(word)-1))
        else:
            result.add(word)
    return result - _STOP


def source_mask(sources):
    return sum(_SOURCES[s] for s in task_sources.normalize(sources))


def schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS conversation_memory (
          turn_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
          topic_id TEXT NOT NULL, source_mask INTEGER NOT NULL, summary TEXT NOT NULL,
          hits INTEGER NOT NULL DEFAULT 0, last_access INTEGER NOT NULL DEFAULT 0,
          tier TEXT NOT NULL DEFAULT 'warm'
        );
        CREATE INDEX IF NOT EXISTS idx_memory_thread_topic ON conversation_memory(thread_id,topic_id,ordinal);
        CREATE TABLE IF NOT EXISTS conversation_memory_terms (
          turn_id TEXT NOT NULL, thread_id TEXT NOT NULL, topic_id TEXT NOT NULL,
          term TEXT NOT NULL, PRIMARY KEY(turn_id,term)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_term ON conversation_memory_terms(thread_id,topic_id,term);
        CREATE TABLE IF NOT EXISTS conversation_memory_loads (
          turn_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, report TEXT NOT NULL
        );
    ''')


def topic(conn, thread_id, ordinal):
    row = conn.execute("SELECT turn_id FROM conversation_turns WHERE thread_id=? AND ordinal<=? AND kind='new_topic' ORDER BY ordinal DESC LIMIT 1",
                       (thread_id, ordinal)).fetchone()
    return row['turn_id'] if row else 'initial'


def index_turn(conn, turn):
    """Store a derived excerpt/index, never rewrite a frozen turn or its evidence."""
    snapshot = turn.get('snapshot')
    if not snapshot or conn.execute('SELECT 1 FROM conversation_memory WHERE turn_id=?', (turn['turn_id'],)).fetchone():
        return
    excerpt = {'user_text':clip(turn['user_text'], 96),
               'task':clip(turn['state'].get('goal') or turn['task'], 96),
               'answer_excerpt':clip(snapshot.get('result'), 176),
               'status':turn['status'], 'degraded':bool(snapshot.get('degraded')),
               'created_at':turn['created_at']}
    topic_id = topic(conn, turn['thread_id'], turn['ordinal'])
    conn.execute('INSERT INTO conversation_memory(turn_id,thread_id,ordinal,topic_id,source_mask,summary) VALUES(?,?,?,?,?,?)',
                 (turn['turn_id'],turn['thread_id'],turn['ordinal'],topic_id,source_mask(snapshot.get('allowed_sources')),dumps(excerpt)))
    # SQL limits candidate identifiers before any complete snapshot is hydrated.
    vocabulary = terms('\n'.join([turn['user_text'],turn['task'],snapshot.get('result') or '']))
    conn.executemany('INSERT OR IGNORE INTO conversation_memory_terms VALUES(?,?,?,?)',
                     [(turn['turn_id'],turn['thread_id'],topic_id,word) for word in sorted(vocabulary)])
    refresh_tiers(conn, turn['thread_id'], turn['ordinal'], allow_promotions=False)


def ensure_index(conn, thread_id):
    from api.conversation import _item
    rows = conn.execute('SELECT t.* FROM conversation_turns t LEFT JOIN conversation_memory m ON m.turn_id=t.turn_id '
                        'WHERE t.thread_id=? AND t.snapshot_json IS NOT NULL AND m.turn_id IS NULL ORDER BY t.ordinal', (thread_id,)).fetchall()
    for row in rows:
        index_turn(conn, _item(row))
    if rows:
        clock = conn.execute('SELECT MAX(ordinal) FROM conversation_turns WHERE thread_id=?', (thread_id,)).fetchone()[0]
        refresh_tiers(conn, thread_id, clock, allow_promotions=False)


def heat(hits, age, recent):
    if hits >= 2 and age <= 4:
        return 'hot'
    if recent <= 3 or (hits and age <= 8):
        return 'warm'
    return 'cold'


def refresh_tiers(conn, thread_id, clock, evicted=(), *, allow_promotions=True):
    rows = conn.execute('SELECT * FROM conversation_memory WHERE thread_id=? ORDER BY hits DESC,last_access DESC,ordinal DESC', (thread_id,)).fetchall()
    topic_id = topic(conn, thread_id, clock)
    start = conn.execute("SELECT MAX(ordinal) FROM conversation_turns WHERE thread_id=? AND ordinal<=? AND kind='new_topic'", (thread_id,clock)).fetchone()[0] or 0
    transitions, resident = [], 0
    for row in rows:
        tier = heat(row['hits'],clock-row['last_access'],clock-row['ordinal'])
        if row['topic_id'] != topic_id and row['last_access'] <= start:
            tier = 'cold'
        if tier == 'hot' and row['tier'] != 'hot' and not allow_promotions:
            tier = 'warm'
        if tier == 'hot':
            if resident >= 3 or row['turn_id'] in evicted:
                tier = 'warm'
            else:
                resident += 1
        if tier != row['tier']:
            conn.execute('UPDATE conversation_memory SET tier=? WHERE turn_id=?', (tier,row['turn_id']))
            transitions.append({'turn_id':row['turn_id'],'ordinal':row['ordinal'],'from':row['tier'],'to':tier})
    return transitions


def compact_record(conn, row, excerpt=None):
    raw = conn.execute('SELECT state_json FROM conversation_turns WHERE thread_id=? AND turn_id=?',
                       (row['thread_id'],row['turn_id'])).fetchone()
    state = json.loads(raw['state_json'])
    return {'turn_id':row['turn_id'], 'ordinal':row['ordinal'], 'historical':True,
            **json.loads(row['summary']), 'displayed_case_ids':state.get('displayed_case_ids') or [],
            'case_ids':state.get('case_ids'),
            **({'answer_excerpt':excerpt} if excerpt is not None else {})}


def relevant_excerpt(answer, query):
    query_terms = terms(query)
    folded = answer.casefold()
    positions = [folded.find(term) for term in query_terms if term in folded]
    start = max(0, min(positions) - 120) if positions else 0
    return clip(answer[start:start+6000], 400)


def build(thread_id, core, allowed_sources, *, reference_id=None, budget=None, fresh=False):
    """Plan a load, lazily indexing legacy turns; accepted runs alone record hits."""
    budget = budget or Budget.from_env()
    if tokens(core) > budget.hot:
        raise ValueError('当前目标、问题或案件范围超过热层 Token 预算，请缩小范围或新建会话；关键条件未被截断')
    with storage._LOCK, storage._connect() as conn:
        run = conn.execute('SELECT deleted_at FROM runs WHERE thread_id=?', (thread_id,)).fetchone()
        if run is None or run['deleted_at']:
            raise ValueError('会话不存在或已移入回收站')
        ensure_index(conn, thread_id)
        latest = conn.execute('SELECT ordinal FROM conversation_turns WHERE thread_id=? ORDER BY ordinal DESC LIMIT 1', (thread_id,)).fetchone()
        clock = latest['ordinal']
        topic_id = topic(conn, thread_id, clock)
        mask = source_mask(allowed_sources)
        # Fresh topics load no history. A later explicit historical question can
        # search older topics without inheriting their filters automatically.
        if reference_id:
            ref = conn.execute('SELECT topic_id FROM conversation_memory WHERE thread_id=? AND turn_id=?', (thread_id,reference_id)).fetchone()
            if ref:
                topic_id = ref['topic_id']
        if fresh:
            topic_id = '__new_topic__'
        params = (thread_id, topic_id, mask)
        recent = conn.execute('SELECT * FROM conversation_memory WHERE thread_id=? AND topic_id=? AND (source_mask & ?) = source_mask ORDER BY ordinal DESC LIMIT 3', params).fetchall()
        hot_topic = ' AND topic_id=?' if reference_id or fresh else ''
        hot_args = (thread_id,mask,clock,*((topic_id,) if hot_topic else ()))
        hot = conn.execute("SELECT * FROM conversation_memory WHERE thread_id=? AND (source_mask & ?) = source_mask AND tier='hot' AND ?-last_access<=4" + hot_topic + ' ORDER BY hits DESC,last_access DESC LIMIT 3', hot_args).fetchall()
        query_terms = sorted(terms(core['user_text']), key=lambda x:(-len(x),x))[:64]
        matches = []
        past_topics = not fresh and not reference_id and bool(re.search(r'之前|以前|上次|早先|还记得|先前',core['user_text']))
        if query_terms:
            marks = ','.join('?' for _ in query_terms)
            search_topic = '' if past_topics else ' AND x.topic_id=?'
            search_args = (thread_id,*(() if past_topics else (topic_id,)),*query_terms,mask)
            matches = conn.execute('SELECT m.*,COUNT(*) AS relevance FROM conversation_memory_terms x JOIN conversation_memory m ON m.turn_id=x.turn_id '
                f'WHERE x.thread_id=?{search_topic} AND x.term IN ({marks}) AND (m.source_mask & ?) = m.source_mask '
                'GROUP BY m.turn_id HAVING COUNT(*)>=2 OR MAX(length(x.term))>=4 ORDER BY relevance DESC,m.ordinal DESC LIMIT 6',
                search_args).fetchall()
        candidates = list(matches)
        if reference_id:
            row = conn.execute('SELECT * FROM conversation_memory WHERE thread_id=? AND turn_id=? AND (source_mask & ?) = source_mask',
                               (thread_id,reference_id,mask)).fetchone()
            if row:
                candidates.insert(0,row)
        payload = {**core, 'hot_turns':[], 'recent_turns':[], 'retrieved_turns':[]}
        loaded = set()
        layer_ids = {'hot':[], 'warm':[], 'cold':[]}
        def add(key, row, record, cap):
            if row['turn_id'] in loaded:
                return False
            trial = [*payload[key], record]
            size = tokens({**core,'hot_turns':trial}) if key == 'hot_turns' else tokens(trial)
            # A list/entry that cannot fit is omitted whole; no partial identifiers.
            candidate = {**payload,key:trial}
            if size > cap or tokens(candidate) > budget.total:
                return False
            payload[key] = trial
            loaded.add(row['turn_id'])
            layer_ids[{'hot_turns':'hot','recent_turns':'warm','retrieved_turns':'cold'}[key]].append(row['turn_id'])
            return True
        evicted_hot = []
        for row in hot:
            if not add('hot_turns', row, compact_record(conn,row), budget.hot):
                evicted_hot.append(row['turn_id'])
        for row in recent:
            add('recent_turns', row, compact_record(conn,row), budget.warm)
        recalled_cases = set()
        for row in candidates:
            if row['turn_id'] not in loaded:
                raw = conn.execute('SELECT snapshot_json FROM conversation_turns WHERE thread_id=? AND turn_id=?', (thread_id,row['turn_id'])).fetchone()
                answer = json.loads(raw['snapshot_json']).get('result') or ''
                add('retrieved_turns', row, compact_record(conn,row,relevant_excerpt(answer,core['user_text'])), budget.cold)
            if row['turn_id'] in loaded:
                # Only delivered identifiers in selected, permission-checked snapshots.
                raw = conn.execute('SELECT state_json FROM conversation_turns WHERE thread_id=? AND turn_id=?', (thread_id,row['turn_id'])).fetchone()
                recalled_cases.update(json.loads(raw['state_json']).get('displayed_case_ids') or [])
        usage = {'hot':tokens({**core,'hot_turns':payload['hot_turns']}),
                 'warm':tokens(payload['recent_turns']), 'cold':tokens(payload['retrieved_turns'])}
        if tokens(payload) > budget.total:
            raise ValueError('会话记忆超出总 Token 预算，请缩小范围；本轮未执行')
        accessed = sorted({row['turn_id'] for row in candidates if row['turn_id'] in loaded})
        report = {'tokenizer':counter().label, 'budget':vars(budget), 'tokens':usage, 'total_tokens':tokens(payload),
                  'loaded':layer_ids, 'accessed':accessed, 'topic_id':topic_id, 'evicted_hot':evicted_hot,
                  'retrieval':{'method':'indexed_terms_and_chinese_bigrams','matches':len(matches),'loaded':len(accessed),
                               'past_topics_requested':past_topics},
                  'recalled_case_ids':sorted(recalled_cases)}
        report['entries'] = [{'turn_id':item['turn_id'],'ordinal':item['ordinal'],'layer':layer,
                              'question':item['user_text']} for key,layer in (
                                  ('hot_turns','hot'),('recent_turns','warm'),('retrieved_turns','cold'))
                             for item in payload[key]]
        return payload, report


def accept(conn, turn_id, thread_id, clock, report):
    """Commit usage once, atomically with the new execution and parent validation."""
    if not report:
        return
    for ident in set(report['accessed']):
        conn.execute('UPDATE conversation_memory SET hits=hits+1,last_access=? WHERE thread_id=? AND turn_id=?', (clock,thread_id,ident))
    transitions = refresh_tiers(conn, thread_id, clock, report.get('evicted_hot', ()))
    conn.execute('INSERT INTO conversation_memory_loads VALUES(?,?,?)', (turn_id,thread_id,dumps({**report,'transitions':transitions})))


def inspect(thread_id, turn_id):
    with storage._LOCK, storage._connect() as conn:
        ensure_index(conn, thread_id)
        row = conn.execute('SELECT report FROM conversation_memory_loads WHERE thread_id=? AND turn_id=?', (thread_id,turn_id)).fetchone()
        counts = {tier:count for tier,count in conn.execute('SELECT tier,COUNT(*) FROM conversation_memory WHERE thread_id=? GROUP BY tier',(thread_id,))}
    return {'last_load':json.loads(row['report']) if row else None,
            'stored':{tier:counts.get(tier,0) for tier in ('hot','warm','cold')},
            'budget':vars(Budget.from_env()), 'scope':'conversation'}


def history_load(reference_id):
    budget = Budget.from_env()
    return {'tokenizer':counter().label, 'budget':vars(budget), 'tokens':{'hot':0,'warm':0,'cold':0},
            'total_tokens':0,'loaded':{'hot':[],'warm':[],'cold':[reference_id]},'accessed':[reference_id],
            'retrieval':{'method':'explicit_snapshot_no_model','matches':1,'loaded':1},'recalled_case_ids':[]}
