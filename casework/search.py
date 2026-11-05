"""Local vectors + BM25 retrieval, case grouping and source-backed experience tags."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from casework import parser, store
from tools.hybrid_retrieval import LexicalIndex, fuse_results,record_id,stable_rank_key
from utils.path_utils import PROJECT_ROOT

_LOCK=threading.RLock()
_MODEL=None
_MODEL_KEY=None
_CACHE={}
_LEXICAL_CACHE={}


def body_priority(text):
    """Prefer explanatory sections to bibliographic headers in exact-ID reads."""
    abstract = re.search(r'(?:^|\n)(?:\(57\))?摘要\s*\n|\babstract\b', text, re.I)
    if abstract and len(text[abstract.end():].strip()) >= 40:
        return 3
    if re.search(r'处理链依次|技术方案|具体实施方式|本(?:发明|申请).{0,10}(?:涉及|提供)|步骤\s*S?\d', text):
        return 2
    return 1 if len(text.strip()) > 100 and not re.search(r'材料信息|\(19\)国家知识产权局|AI生成的专利格式', text) else 0


def embedding_config():
    if os.getenv('CASEWORK_EMBEDDING','1')=='0':return None,'disabled'
    raw=os.getenv('CASEWORK_EMBEDDING_MODEL_PATH','').strip() or os.getenv('EMBEDDING_MODEL_PATH','').strip()
    if not raw:return None,'missing-local-model'
    path=Path(raw).expanduser();path=(PROJECT_ROOT/path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_dir():return None,'missing-local-model'
    markers=[(str(p.relative_to(path)),p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(path.rglob('*')) if p.is_file()]
    from utils.embedding_pool import QUERY_INSTRUCTION,PIPELINE_VERSION
    return path,store.digest([PIPELINE_VERSION,str(path),markers,os.getenv('RETRIEVAL_QUERY_INSTRUCTION',QUERY_INSTRUCTION)])


def get_embedding():
    global _MODEL,_MODEL_KEY
    path,key=embedding_config()
    if not path:return None,key
    with _LOCK:
        if _MODEL_KEY!=key:
            from utils.embedding_pool import get_embedding as shared_embedding
            _MODEL=shared_embedding(str(path),local_only=True)
            _MODEL_KEY=key
        return _MODEL,key


def embed_chunks(texts):
    if not texts:return [],None,'没有变化的正文，无需重新嵌入'
    try:
        model,key=get_embedding()
        if model is None:return [],None,'未启用或未配置本地模型；使用BM25和已登记技术同义词，不调用外部嵌入'
        values=[]
        from casework.chunking import count, config as chunk_config
        if chunk_config()['strategy']!='legacy_chars' and any(count(text)+2>512 for text in texts):
            raise ValueError('Embedding输入超过512 Token，拒绝静默截断')
        with _LOCK:
            for at in range(0,len(texts),64):values.extend(model.embed_documents(texts[at:at+64]))
        return values,key,'本地向量与BM25；向量未发往外部'
    except Exception as exc:
        return [],None,'本地嵌入不可用，已降级BM25：'+type(exc).__name__


def expanded_query(query):
    matched=parser.query_tags(query)
    return query+' '+' '.join(tag+' '+' '.join(parser.TAG_RULES[tag][:3]) for tag in matched)


def matched_terms(text, tags):
    """Literal highlights only; a matched label is not necessarily a source quote."""
    candidates = [term for tag in sorted(tags) for term in [tag, *parser.TAG_RULES.get(tag, [])]]
    return sorted({term for term in candidates if term.casefold() in text.casefold()}, key=lambda s: (-len(s), s))[:8]


@store.consistent_read
def search(query,*,limit=8,root_id=None,domain=None,owner=None,kind=None,allow_vector=True,query_scope='mechanism',_profile='production'):
    started=time.perf_counter();timings={}
    # Offline ablation only; HTTP/tool schemas never expose this selector.
    if _profile not in ('production','vector','bm25','hybrid'):
        raise ValueError('未知检索实验配置')
    if query_scope not in ('mention','topic','mechanism','ambiguous'):raise ValueError('未知案件查询口径')
    broad_scope=query_scope in ('mention','ambiguous')
    if not query.strip():raise ValueError('请输入技术点或自然语言描述')
    if not 1<=limit<=30:raise ValueError('检索数量须在1–30之间')
    exact_ids={m.group().upper() for m in parser.CASE_RE.finditer(query)}
    cases={c['case_key']:c for c in store.list_cases(root_id) if not exact_ids or c['case_id'] in exact_ids}
    from casework.domains import canonical
    cases={k:c for k,c in cases.items() if (not domain or canonical(c.get('technical_domain'))==canonical(domain)) and (not owner or c.get('owner_name')==owner)}
    with store.db() as conn:
        drawing_rows=conn.execute('''SELECT f.*,EXISTS(SELECT 1 FROM chunks c WHERE c.file_id=f.id
            AND c.revision=f.revision AND c.parser_key=f.parser_key) AS text_indexed
            FROM files f JOIN roots r ON r.id=f.root_id WHERE f.active=1 AND r.enabled=1
            AND f.kind='drawing' AND f.state IN ('parsed','inventory')''').fetchall()
        drawings={}
        for file in drawing_rows:
            if not file['case_id']:continue
            key=file['root_id']+':'+file['case_id']
            drawings.setdefault(key,[]).append({
                'file_id':file['id'],'revision':file['revision'],'parser_key':file['parser_key'],
                'source':file['relative_path'],'kind':'drawing','state':file['state'],
                'text_indexed':bool(file['text_indexed']),
                'association':'按所编号关联，附件登记不作为技术正文依据',
                'scope':'仅提取图中文字，未识别图形或连线关系' if file['text_indexed'] else '未提取到图中文字，请下载原件核对'})
        roots=store.roots(enabled=True)
        signature=store.digest([(r['id'],r['generation'],r['enabled']) for r in roots]+[str(store.database_path())])
        with _LOCK:
            cached=_CACHE.get(signature)
            if cached is None:
                rows=conn.execute('''SELECT c.*,f.root_id,f.case_id,f.relative_path,f.kind FROM chunks c
                    JOIN files f ON c.file_id=f.id AND c.revision=f.revision AND c.parser_key=f.parser_key
                    JOIN roots r ON r.id=f.root_id WHERE f.active=1 AND f.state='parsed' AND r.enabled=1''').fetchall()
                payloads={r['file_id']:json.loads(r['payload']) for r in conn.execute('''SELECT v.file_id,v.payload
                    FROM revisions v JOIN files f ON v.file_id=f.id AND v.revision=f.revision AND v.parser_key=f.parser_key
                    JOIN roots r ON r.id=f.root_id WHERE f.active=1 AND f.state='parsed' AND r.enabled=1''')}
                records=[{'text':r['text'],'metadata':{'source':r['relative_path'],'chunk_id':r['id'],'file_id':r['file_id'],
                          'revision':r['revision'],'parser_key':r['parser_key'],'root_id':r['root_id'],'case_id':r['case_id'],
                          'kind':r['kind'],'location':r['location'],'tags':json.loads(r['tags']),
                          'case_focus':(payloads.get(r['file_id'],{}).get('retrieval_focus') or {}).get('text'),
                          'retrieval_role':payloads.get(r['file_id'],{}).get('chunks',[{}]* (r['ordinal']+1))[r['ordinal']].get('retrieval_role')},
                          'vector':json.loads(r['vector']) if r['vector'] else None,'embedding_key':r['embedding_key']} for r in rows]
                if len(_CACHE)>=2:_CACHE.clear()
                _CACHE[signature]=records;cached=records
    records=[r for r in cached if r['metadata']['root_id']+':'+r['metadata']['case_id'] in cases and (not kind or r['metadata']['kind']==kind)]
    if not exact_ids and not kind:
        records=[r for r in records if r['metadata']['kind'] in ('disclosure','application','meeting','other','drawing')]
    subject_check={'status':'not_required','checked_cases':0,'retained_cases':0}
    subject_checks={}
    if _profile=='production' and not exact_ids:
        from casework.subject_support import assess,VERSION
        # Read only current published text, keyed by root as well as case ID.
        # Filtering precedes every retrieval/fallback path and uses the original
        # question, never catalog expansion, generated summaries or filenames.
        bodies={}
        for record in records:
            meta=record['metadata'];key=meta['root_id']+':'+meta['case_id']
            bodies.setdefault(key,[]).append(record['text'])
        subject_checks={key:assess(query,'\n'.join(parts)) for key,parts in bodies.items()}
        allowed={key for key,review in subject_checks.items() if review['supported']}
        records=[r for r in records if r['metadata']['root_id']+':'+r['metadata']['case_id'] in allowed]
        subject_check={'status':'checked','version':VERSION,'checked_cases':len(bodies),
            'retained_cases':len(allowed),'rejected_cases':len(bodies)-len(allowed),
            'terms':list(next(iter(subject_checks.values()),{}).get('terms',[])),
            'scope':'原问题对象的词面支持预筛；不证明采用、不证明全库不存在，同义转述仍需原文核对'}
    literal_tags=parser.query_tags(query)
    expansion={'concepts':[],'status':'not_required'}
    if _profile=='production':
        from casework.local_reranker import technical_passage
        if not exact_ids and not broad_scope:
            records=[r for r in records if technical_passage(r['text'])]
        if records and not literal_tags and not exact_ids and allow_vector and os.getenv('CASEWORK_EMBEDDING','1')!='0':
            from casework.concept_router import propose
            expansion=propose(query)
    expanded=expanded_query(query) if _profile=='production' else query
    if expansion['concepts']:
        expanded+=' '+' '.join(tag+' '+' '.join(parser.TAG_RULES[tag]) for tag in expansion['concepts'])
    timings['preparation_seconds']=time.perf_counter()-started
    tick=time.perf_counter()
    lexical_key=(signature,tuple(r['metadata']['chunk_id'] for r in records))
    with _LOCK:
        index=_LEXICAL_CACHE.get(lexical_key)
        if index is None:
            index=LexicalIndex(records)
            if len(_LEXICAL_CACHE)>=8:_LEXICAL_CACHE.clear()
            _LEXICAL_CACHE[lexical_key]=index
    lexical=index.search(expanded,max(80,limit*8))
    timings['lexical_seconds']=time.perf_counter()-tick
    tick=time.perf_counter()
    vector=[];raw_vector=[];mode='BM25＋技术同义词';note='未使用外部服务'
    try:
        model,key=get_embedding() if records and allow_vector and _profile!='bm25' else (None,'disabled-by-ablation')
        eligible=[r for r in records if r['vector'] and r['embedding_key']==key]
        if model and eligible:
            import numpy as np
            with _LOCK:embedding=np.asarray(model.embed_query(query),dtype='float32')
            matrix=np.asarray([r['vector'] for r in eligible],dtype='float32')
            scores=matrix@embedding
            indices=sorted(range(len(eligible)),key=lambda i:(-float(scores[i]),stable_rank_key(eligible[i])))[:max(80,limit*8)]
            raw_vector=[{**eligible[int(i)],'vector_distance':max(0.,2-2*float(scores[i]))} for i in indices]
            vector=[{**eligible[int(i)],'vector_distance':max(0.,2-2*float(scores[i]))} for i in indices if float(scores[i])>=.52]
            mode='本地向量＋BM25＋技术同义词'
        elif model and records:note='当前发布版本缺少匹配模型的向量，请重新同步；本次使用词法检索'
    except Exception as exc:note='本地向量查询不可用，本次使用词法检索：'+type(exc).__name__
    timings['vector_seconds']=time.perf_counter()-tick
    tick=time.perf_counter()
    known_tags=bool(parser.query_tags(query))
    fused=fuse_results(expanded,vector,lexical,k=max(80,limit*8),
                       min_overlap=.025 if known_tags else .12,max_distance=.96 if known_tags else .8)
    candidate_fallback=False
    if _profile=='production' and not exact_ids and (not known_tags or broad_scope):
        # Long paraphrases dilute token overlap. Preserve bounded lexical
        # candidates for reading; do not promote them to verified mechanisms.
        pool={};rank_scores={};channels={}
        for channel,hits in [('bm25',lexical),('vector',raw_vector)]:
            for rank,hit in enumerate(hits,1):
                ident=record_id(hit);pool[ident]=hit
                rank_scores[ident]=rank_scores.get(ident,0)+1/(60+rank)
                channels.setdefault(ident,[]).append(channel)
        candidate_limit=int(os.getenv('CASEWORK_RERANKER_CANDIDATES','80'))
        if not 1<=candidate_limit<=80:raise ValueError('重排候选数须在1–80之间')
        candidates=[{**pool[i],'score':s,'channels':channels[i]} for i,s in sorted(rank_scores.items(),
            key=lambda pair:(-pair[1],stable_rank_key(pool[pair[0]])))][:candidate_limit]
        reranked=None
        if allow_vector:
            try:
                from casework.local_reranker import rank as rerank
                reranked=rerank(expanded,candidates)
            except Exception as exc:note+='；本地重排不可用：'+type(exc).__name__
        if reranked is not None:
            threshold=float(os.getenv('CASEWORK_RERANKER_MIN_LOGIT','-3'))
            # Broad mention/browse queries still need relevance ordering. Keep
            # their negative/background mentions for semantic classification;
            # a mechanism-only cutoff would silently change the user's scope.
            fused=sorted([{**hit,'score':hit['rerank_logit']} for hit in reranked
                if broad_scope or hit['rerank_logit']>=threshold],key=lambda hit:(-hit['score'],stable_rank_key(hit)))
            mode+='＋本地交叉编码重排'
            candidate_fallback=True
        elif broad_scope or not fused:
            fused=[hit for hit in candidates if 'bm25' in hit['channels'] or hit.get('vector_distance',2)<=.96]
            candidate_fallback=True
        if candidate_fallback:note+='；保留词法和向量候选供原文核对，不表示技术机制已确认'
    if _profile!='production':
        # Native channel ranking / plain RRF, without expansion, boosts or tag filtering.
        pool={};scores={};channels={}
        selected=([('vector',vector)] if _profile=='vector' else
                  [('bm25',lexical)] if _profile=='bm25' else [('vector',vector),('bm25',lexical)])
        for channel,hits in selected:
            for rank,hit in enumerate(hits,1):
                ident=record_id(hit);pool[ident]=hit
                scores[ident]=scores.get(ident,0.)+1/(60+rank)
                channels.setdefault(ident,[]).append(channel)
        fused=[{**pool[ident],'score':score,'channels':channels[ident]} for ident,score in
               sorted(scores.items(),key=lambda pair:(-pair[1],stable_rank_key(pool[pair[0]])))]
        mode='ablation:'+_profile
    if _profile=='production' and exact_ids:
        # Exact case lookup should expose technical body, not only header chunks that repeat the ID.
        fused=[{**r,'score':(.03 if r['metadata']['kind'] in ('application','disclosure') else .01)+(.01 if r['metadata']['tags'] else 0),'channels':['exact_case']} for r in records]
        fused.sort(key=lambda hit:(hit['metadata']['kind'] not in ('application','disclosure'),
                                  -body_priority(hit['text']), -hit['score'], stable_rank_key(hit)))
    focus_check={'status':'not_required','checked_cases':0}
    if _profile=='production' and not exact_ids and not broad_scope and allow_vector:
        # A shared template paragraph must not establish an unrelated case's subject.
        focus_records={hit['metadata']['root_id']+':'+hit['metadata']['case_id']:{'text':hit['metadata']['case_focus'],
            'metadata':{'case_key':hit['metadata']['root_id']+':'+hit['metadata']['case_id']}} for hit in fused if hit['metadata'].get('case_focus')}
        if focus_records:
            from casework.local_reranker import rank as rerank
            try:
                checked=rerank(query,list(focus_records.values()))
            except Exception:
                checked=None
            if checked is not None:
                allowed={hit['metadata']['case_key'] for hit in checked if hit['rerank_logit']>=float(os.getenv('CASEWORK_RERANKER_MIN_LOGIT','-3'))}
                fused=[hit for hit in fused if hit['metadata']['root_id']+':'+hit['metadata']['case_id'] not in focus_records or hit['metadata']['root_id']+':'+hit['metadata']['case_id'] in allowed]
                focus_check={'status':'checked','checked_cases':len(checked),'retained_cases':len(allowed),
                    'scope':'仅核对生成材料的个案摘要是否相关，不证明已采用、实施或全库不存在'}
            else:
                focus_check={'status':'unavailable','checked_cases':0}
                note+='；个案主题核对不可用，召回候选尚未确认'
        fused.sort(key=lambda hit:(hit['metadata'].get('retrieval_role')=='context',-hit['score'],stable_rank_key(hit)))
    timings['fusion_and_ranking_seconds']=time.perf_counter()-tick
    groups={}
    def group_for(key,score=0.):
        if key not in groups:
            case=cases[key]
            groups[key]={'case_key':key,'case_id':case['case_id'],'title':case.get('title'),
                'owner_name':case.get('owner_name'),'technical_domain':case.get('technical_domain'),'stage':case.get('stage'),
                'client':case.get('client_alias'),'score':score,'sources':[],'matched_tags':[],
                'related_drawings':sorted(drawings.get(key,[]),key=lambda p:p['source']),
                'subject_support':subject_checks.get(key,{'status':'not_required'})}
        return groups[key]
    query_tags=set(parser.query_tags(query)) if _profile=='production' else set()
    semantic_candidates=set()
    if query_tags and not broad_scope and allow_vector and os.getenv('CASEWORK_EMBEDDING','1')!='0':
        # Alias absence is not a semantic negative. Only broaden the catalog
        # gate with a bounded local reranker; keep lexical-only behavior intact.
        try:
            from casework.local_reranker import rank as rerank
            untagged=[hit for hit in fused if not query_tags.intersection(hit['metadata']['tags'])]
            checked=rerank(query,untagged)
            semantic_candidates={record_id(hit) for hit in (checked or []) if hit['rerank_logit']>=float(os.getenv('CASEWORK_RERANKER_MIN_LOGIT','-3'))}
        except Exception as exc:note+='；目录外转述候选核对不可用：'+type(exc).__name__
    for hit in fused:
        meta=hit['metadata'];key=meta['root_id']+':'+meta['case_id'];case=cases[key]
        tag_matches=query_tags&set(meta['tags'])
        # Catalog tags boost explicit evidence, but cannot be a hard recall gate:
        # an affirmative paraphrase need not contain a catalog alias. Untagged
        # passages remain candidates requiring original-text relevance checking.
        if query_tags and not exact_ids and not broad_scope and not tag_matches and record_id(hit) not in semantic_candidates:continue
        score=hit['score']+(.015*len(tag_matches)+(.004 if meta['kind'] in ('disclosure','application') else 0)
                            if _profile=='production' else 0)
        proof={k:v for k,v in meta.items() if k not in ('root_id','case_id','case_focus','retrieval_role')}
        proof.update(excerpt=hit['text'],channels=hit['channels'],matched_tags=sorted(tag_matches),
                     matched_terms=matched_terms(hit['text'], tag_matches),score=round(score,6))
        if 'rerank_logit' in hit:
            proof.update(rerank_logit=hit['rerank_logit'],rerank_coverage=hit.get('rerank_coverage'),
                         rerank_window=hit.get('rerank_window'))
        group=group_for(key,score);group['score']=max(group['score'],score)
        if len(group['sources'])<3 and not any(p['revision']==proof['revision'] and p['excerpt']==proof['excerpt'] for p in group['sources']):group['sources'].append(proof)
        group['matched_tags']=sorted(set(group['matched_tags'])|tag_matches)
    # Filename association can satisfy a case-ID lookup, never an unrelated mechanism query.
    if exact_ids and not query_tags and kind in (None,'drawing'):
        for key in cases:
            if drawings.get(key):group_for(key)
    ranked=sorted(groups.values(),key=lambda g:(-g['score'],g['case_id']))
    for group in ranked:
        group['sources'].sort(key=lambda p: (p['kind'] not in ('disclosure','application'),
            -body_priority(p['excerpt']) if exact_ids and _profile=='production' else 0, -p['score']))
        group['match_basis']=('candidate_body' if candidate_fallback else 'body') if group['sources'] else 'case_id_attachment'
        group['candidate_only']=candidate_fallback or not bool(exact_ids or group['matched_tags'])
    timings['total_seconds']=time.perf_counter()-started
    from api.telemetry import record
    record({'event':'stage_timing','stage':'retrieval','timings':timings,'elapsed_seconds':timings['total_seconds']})
    return {'query':query,'query_scope':query_scope,'exhaustive':False,'timings':timings,'focus_check':focus_check,
            'filters':{'domain':domain,'owner':owner,'root_id':root_id,'kind':kind,'allow_vector':allow_vector},
            'results':ranked[:limit],'matched_cases_in_retrieval_pool':len(ranked),
            'has_more':len(ranked)>limit,'strategy':mode,'note':note,'coverage':store.coverage(),
            'concept_expansion':expansion,'subject_check':subject_check,
            'meaning':'排名分数不代表置信度、技术等同或撰写质量；正文依据来自当前发布的解析文本，关联绘图仅按所编号登记，不证明图形或连线关系。'}
