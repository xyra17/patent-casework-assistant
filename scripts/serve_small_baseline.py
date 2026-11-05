"""Loopback-only OpenAI-compatible deterministic small-model eval server.
Does not implement constrained decoding: invalid JSON remains an observed failure.
"""
import argparse,time,uuid
import torch
from fastapi import FastAPI
from transformers import AutoTokenizer,AutoModelForCausalLM
import uvicorn
p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--port',type=int,default=15726);a=p.parse_args()
torch.set_num_threads(4);torch.manual_seed(20261030)
tokenizer=AutoTokenizer.from_pretrained(a.model)
model=AutoModelForCausalLM.from_pretrained(a.model,torch_dtype=torch.float16).to('mps').eval()
app=FastAPI()
@app.post('/v1/chat/completions')
def completion(body:dict):
    text=tokenizer.apply_chat_template(body['messages'],tokenize=False,add_generation_prompt=True)
    tokens=tokenizer(text,return_tensors='pt').to('mps');start=time.monotonic()
    with torch.inference_mode():
        generated=model.generate(**tokens,max_new_tokens=min(body.get('max_tokens') or 1536,1536),do_sample=False,pad_token_id=tokenizer.eos_token_id)
    answer=tokenizer.decode(generated[0,tokens.input_ids.shape[-1]:],skip_special_tokens=True)
    print('GENERATED',len(generated[0])-tokens.input_ids.shape[-1],time.monotonic()-start,flush=True)
    return {'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),'model':'Qwen2.5-1.5B-Instruct-fp16','choices':[{'index':0,'message':{'role':'assistant','content':answer},'finish_reason':'length' if len(generated[0])-tokens.input_ids.shape[-1]>=1536 else 'stop'}],'usage':{'prompt_tokens':tokens.input_ids.shape[-1],'completion_tokens':len(generated[0])-tokens.input_ids.shape[-1],'total_tokens':len(generated[0])}}
uvicorn.run(app,host='127.0.0.1',port=a.port)
