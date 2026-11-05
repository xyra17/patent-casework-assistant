"""Optional local model proposes catalog terms; its output is never case evidence."""
from __future__ import annotations
import ipaddress,json,os,threading,time,urllib.parse,urllib.request
from collections import OrderedDict
from pathlib import Path
from casework import parser

_LOCK=threading.Lock()
_CACHE=OrderedDict()


def messages(query):
    return [{'role':'system','content':
        '为专利技术检索选择登记概念。只选择用户正向描述实际涉及的机制，不把排除项当要求；无法匹配则为空。'
        '概念仅用于扩展候选，不能证明原文采用。只输出JSON对象：{"concepts":["目录中的概念名"]}，最多2个。目录：'
        +json.dumps(list(parser.TAG_RULES),ensure_ascii=False)}, {'role':'user','content':query}]


def propose(query):
    url=os.getenv('CASEWORK_CONCEPT_ROUTER_URL','').rstrip('/')
    if not url:
        if os.getenv('CASEWORK_CATALOG_EXPANSION','1')=='0' or os.getenv('CASEWORK_EMBEDDING','1')=='0':
            return {'concepts':[],'status':'disabled'}
        try:
            from casework.local_reranker import rank
            catalog=json.loads(Path(__file__).with_name('concept_catalog.json').read_text())
            ranked=rank(query,[{'text':k+'：'+v,'metadata':{'case_id':k}} for k,v in catalog.items() if k in parser.TAG_RULES])
            ranked=sorted(ranked or [],key=lambda r:(-r['rerank_logit'],r['metadata']['case_id']))
            proposed=[ranked[0]['metadata']['case_id']] if ranked and ranked[0]['rerank_logit']>=-4 else []
            return {'concepts':proposed,'status':'local_catalog_candidates','ranking':[
                {'concept':r['metadata']['case_id'],'logit':r['rerank_logit']} for r in ranked[:3]],
                'scope':'目录描述的语义近邻，仅扩展候选；低分也不证明采用或完整相关'}
        except Exception as exc:return {'concepts':[],'status':'unavailable','error_type':type(exc).__name__}
    parsed=urllib.parse.urlsplit(url)
    try:local=ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:local=parsed.hostname=='localhost'
    if parsed.scheme!='http' or not local or parsed.username or parsed.password:
        return {'concepts':[],'status':'invalid_local_endpoint'}
    model=os.getenv('CASEWORK_CONCEPT_ROUTER_MODEL','Qwen2.5-1.5B-Instruct')
    key=(url,model,json.dumps(parser.TAG_RULES,sort_keys=True),query)
    with _LOCK:
        cached=_CACHE.get(key)
        if cached and time.monotonic()-cached[0]<300:return {**cached[1],'cached':True}
    body={'model':model,'messages':messages(query),'temperature':0,'max_tokens':128}
    try:
        request=urllib.request.Request(url+'/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=15) as response:value=json.loads(response.read())
        content=value['choices'][0]['message']['content'];obj=json.loads(content)
        concepts=obj['concepts']
        if set(obj)!={'concepts'} or not isinstance(concepts,list) or len(concepts)>2 or any(c not in parser.TAG_RULES for c in concepts):
            raise ValueError('invalid concept contract')
        result={'concepts':list(dict.fromkeys(concepts)),'status':'proposed','model':model,'usage':value.get('usage'),
                'scope':'仅用于查询扩展，非案件事实或已核实相关性'}
        with _LOCK:
            _CACHE[key]=(time.monotonic(),result)
            while len(_CACHE)>128:_CACHE.popitem(last=False)
        return result
    except Exception as exc:return {'concepts':[],'status':'unavailable','error_type':type(exc).__name__}
