"""Bounded local cross-encoder ranking; never downloads weights or calls an API."""
from __future__ import annotations
import os,re,threading,json
from pathlib import Path
from utils.path_utils import RESOURCE_ROOT
from utils.local_inference import inference_guard

_LOCK=threading.RLock()
_STATE={}


def technical_passage(text):
    """Keep technical prose; an explicit denial of a following block is not proof."""
    body=re.split(r'##\s*技术正文\s*\n',text,maxsplit=1)[-1]
    paragraphs=re.split(r'\n\s*\n',body)
    return '\n\n'.join(p for p in paragraphs if not re.search(
        r'(?:不采用|不涉及|未采用|未实现|没有)(?:下述|以下|如下|上述)(?:技术)?机制[：:]',p)).strip()


def pair_windows(tokenizer,query,text,*,maximum=384):
    """Explicit bounded windows; report unprocessed tails and query shortening."""
    backend=tokenizer.backend_tokenizer
    backend.no_truncation();backend.no_padding()
    query_encoding=backend.encode(query,add_special_tokens=False)
    query_cut=len(query_encoding.ids)>96
    if query_cut:query_encoding.truncate(96)
    encoded=backend.encode(text,add_special_tokens=False)
    budget=maximum-len(query_encoding.ids)-tokenizer.num_special_tokens_to_add(pair=True)
    if budget<=48:raise ValueError('重排输入预算不足')
    encoded.truncate(budget,stride=48)
    passages=[encoded,*encoded.overflowing]
    windows=[]
    for passage in passages[:4]:
        pair=backend.post_process(query_encoding,passage,add_special_tokens=True)
        values={'input_ids':pair.ids,'attention_mask':pair.attention_mask}
        if 'token_type_ids' in tokenizer.model_input_names:values['token_type_ids']=pair.type_ids
        if len(values['input_ids'])>maximum:raise ValueError('重排输入超过Token预算')
        windows.append((values,(passage.offsets[0][0],passage.offsets[-1][1]) if passage.ids else (0,0)))
    covered=len(passages)<=4
    return windows,{'query_shortened':query_cut,'complete_passage':covered,'windows':len(windows)}


def qwen_pair_windows(tokenizer, query, text, *, maximum=512):
    """Official yes/no prompt, retaining original passage offsets and coverage."""
    from utils.embedding_pool import QUERY_INSTRUCTION
    instruction = os.getenv('RETRIEVAL_QUERY_INSTRUCTION', QUERY_INSTRUCTION)
    prefix = tokenizer.encode('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
                              + f'<Instruct>: {instruction}\n<Query>: ', add_special_tokens=False)
    separator = tokenizer.encode('\n<Document>: ', add_special_tokens=False)
    suffix = tokenizer.encode('<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n', add_special_tokens=False)
    backend = tokenizer.backend_tokenizer
    backend.no_truncation(); backend.no_padding()
    question = backend.encode(query, add_special_tokens=False)
    shortened = len(question.ids) > 96
    if shortened:
        question.truncate(96)
    start = prefix + question.ids + separator
    budget = maximum - len(start) - len(suffix)
    if budget <= 48:
        raise ValueError('重排输入预算不足')
    encoded = backend.encode(text, add_special_tokens=False)
    encoded.truncate(budget, stride=48)
    passages = [encoded, *encoded.overflowing]
    windows = []
    for passage in passages[:4]:
        ids = start + passage.ids + suffix
        windows.append(({'input_ids': ids, 'attention_mask': [1] * len(ids)},
                        (passage.offsets[0][0], passage.offsets[-1][1]) if passage.ids else (0, 0)))
    return windows, {'query_shortened': shortened, 'complete_passage': len(passages) <= 4,
                     'windows': len(windows)}


def rank(query,records,limit=80):
    if os.getenv('CASEWORK_RERANKER','1')=='0' or os.getenv('CASEWORK_EMBEDDING','1')=='0':return None
    path=Path(os.getenv('CASEWORK_RERANKER_MODEL_PATH',str(RESOURCE_ROOT/'models/bge-reranker-base'))).expanduser()
    if not path.is_absolute():path=RESOURCE_ROOT/path
    if not path.is_dir():return None
    checkpoint=os.getenv('CASEWORK_RERANKER_ADAPTER','')
    device=os.getenv('CASEWORK_RERANKER_DEVICE','cpu')
    if device not in ('cpu','mps','cuda'):raise ValueError('重排设备须为cpu/mps/cuda')
    key=(str(path.resolve()),checkpoint,device)
    with _LOCK, inference_guard(device):
        import torch
        if _STATE.get('key')!=key:
            from transformers import AutoTokenizer,AutoModelForSequenceClassification,AutoModelForCausalLM
            qwen=json.loads((path/'config.json').read_text()).get('model_type')=='qwen3'
            tokenizer=AutoTokenizer.from_pretrained(str(path),local_files_only=True,padding_side='left' if qwen else 'right')
            factory=AutoModelForCausalLM if qwen else AutoModelForSequenceClassification
            model=factory.from_pretrained(str(path),local_files_only=True,attn_implementation='sdpa',torch_dtype=torch.float32)
            if checkpoint:
                delta=torch.load(checkpoint,map_location='cpu',weights_only=True)
                unexpected=model.load_state_dict(delta,strict=False).unexpected_keys
                if unexpected:raise ValueError('重排参数与基础模型不兼容')
            model.to(device).eval();torch.set_num_threads(min(torch.get_num_threads(),4))
            _STATE.update(key=key,tokenizer=tokenizer,model=model,qwen=qwen)
        selected=records[:limit];pairs=[];owners=[];bounds=[];details=[]
        tokenizer=_STATE['tokenizer']
        for index,record in enumerate(selected):
            text=technical_passage(record.get('rerank_text',record['text']))
            window_fn=qwen_pair_windows if _STATE['qwen'] else pair_windows
            windows,detail=window_fn(tokenizer,query,text,maximum=int(os.getenv('CASEWORK_RERANKER_MAX_TOKENS','512' if _STATE['qwen'] else '384')))
            details.append(detail)
            for values,span in windows:
                pairs.append(values);owners.append(index);bounds.append(span)
        scores=[float('-inf')]*len(selected);chosen=[None]*len(selected)
        from api.telemetry import span
        with span('reranking'),torch.inference_mode():
            batch_size=int(os.getenv('CASEWORK_RERANKER_BATCH_SIZE','4'))
            if not 1<=batch_size<=32:raise ValueError('重排批量须在1–32之间')
            for at in range(0,len(pairs),batch_size):
                values={k:v.to(device) for k,v in tokenizer.pad(pairs[at:at+batch_size],padding=True,return_tensors='pt').items()}
                if _STATE['qwen']:
                    output=_STATE['model'](**values,logits_to_keep=1).logits[:,-1,:]
                    logits=(output[:,tokenizer.convert_tokens_to_ids('yes')]-output[:,tokenizer.convert_tokens_to_ids('no')]).cpu().tolist()
                else:logits=_STATE['model'](**values).logits.reshape(-1).cpu().tolist()
                for offset,score in enumerate(logits,at):
                    owner=owners[offset]
                    if score>scores[owner]:scores[owner]=score;chosen[owner]=bounds[offset]
    return [{**record,'rerank_logit':float(score),'rerank_coverage':detail,'rerank_window':window}
            for record,score,detail,window in zip(selected,scores,details,chosen)]
