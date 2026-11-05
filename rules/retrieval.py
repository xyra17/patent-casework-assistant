"""Versioned derived vectors in app SQLite; authoritative rules remain in long_term_rules."""
import asyncio
import hashlib
import json
import math
import os
import threading
from pathlib import Path
from api import storage

_LOCK=threading.Lock()


def _model():
    from tools.knowledge_base_tool import _get_embedding
    return _get_embedding()


def _fingerprint():
    name=os.getenv('EMBEDDING_MODEL_PATH') or os.getenv('EMBEDDING_MODEL','BAAI/bge-base-zh-v1.5')
    root=Path(name).expanduser()
    stamps=[(str(p),p.stat().st_size,p.stat().st_mtime_ns) for pattern in ('config.json','modules.json','model*.safetensors','pytorch_model*.bin') for p in root.glob(pattern)] if root.is_dir() else []
    return hashlib.sha256(json.dumps(['rule-retrieval-v2',name,stamps]).encode()).hexdigest()


def _unit(values):
    values=[float(x) for x in values]
    norm=math.sqrt(sum(x*x for x in values))
    if not norm or not all(math.isfinite(x) for x in values): raise ValueError('无效的语义向量')
    return [x/norm for x in values]


def _search(query,rules):
    with _LOCK:
        fingerprint=_fingerprint()
        with storage._LOCK,storage._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS rule_vectors (rule_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,fingerprint TEXT NOT NULL,vector TEXT NOT NULL)')
            cached={r['rule_id']:r for r in conn.execute('SELECT * FROM rule_vectors')}
        missing=[r for r in rules if r['id'] not in cached or cached[r['id']]['revision']!=r['revision'] or cached[r['id']]['fingerprint']!=fingerprint]
        model=_model()
        if missing:
            texts=['\n'.join([r['title'],r['subject'],r['content'],r['scope_note'],*r['match_terms'],*r.get('aliases',[])]) for r in missing]
            vectors=model.embed_documents(texts)
            if len(vectors)!=len(missing): raise ValueError('语义索引不完整')
            with storage._LOCK,storage._connect() as conn:
                for rule,values in zip(missing,vectors):
                    vector=json.dumps(_unit(values));cached[rule['id']]={'vector':vector}
                    # A concurrent edit/delete must not resurrect a stale derived index.
                    row=conn.execute('SELECT revision,active FROM long_term_rules WHERE id=?',(rule['id'],)).fetchone()
                    if row and row['revision']==rule['revision'] and row['active']:
                        conn.execute('INSERT OR REPLACE INTO rule_vectors VALUES(?,?,?,?)',(rule['id'],rule['revision'],fingerprint,vector))
                conn.execute('DELETE FROM rule_vectors WHERE rule_id NOT IN (SELECT id FROM long_term_rules WHERE active=1)')
        q=_unit(model.embed_query(query))
        scored=[]
        for rule in rules:
            v=json.loads(cached[rule['id']]['vector'])
            if len(q)!=len(v):
                with storage._LOCK,storage._connect() as conn:conn.execute('DELETE FROM rule_vectors')
                raise ValueError('语义模型维度变化，下次重建索引')
            score=sum(a*b for a,b in zip(q,v))
            if score>=0.48: scored.append((score,rule['id']))
        return sorted(scored,reverse=True)[:8]


async def retrieve(query,rules,matcher):
    lexical={r['id'] for r in rules if r.get('match_mode')=='all_tasks' or any(matcher(query,t) for t in [*r['match_terms'],*r.get('aliases',[])])}
    if not rules:return [],{'mode':'empty','semantic':'not_needed'}
    # No expensive vector operation when every rule is already in scope.
    remaining=[r for r in rules if r['id'] not in lexical]
    scores={};status='not_needed'
    if remaining:
        try:
            scores={ident:round(score,4) for score,ident in await asyncio.wait_for(asyncio.to_thread(_search,query,remaining),20)}
            status='ready'
        except Exception:
            # No silent keyword-only downgrade: review all unresolved candidates, or block at the existing bound.
            scores={r['id']:None for r in remaining};status='unavailable_review_all'
    candidates=[]
    for rule in rules:
        if rule['id'] in lexical or rule['id'] in scores:
            candidates.append({**rule,'recall':{'channel':'keyword_or_alias' if rule['id'] in lexical else 'semantic' if status=='ready' else 'fallback_review','score':scores.get(rule['id'])}})
    return candidates,{'mode':'hybrid','semantic':status,'candidate_count':len(candidates),'active_count':len(rules)}
