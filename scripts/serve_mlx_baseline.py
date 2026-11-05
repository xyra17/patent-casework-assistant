"""Local deterministic MLX base/adapter endpoint for a matched-runtime comparison."""
import argparse,json,time,uuid
from http.server import BaseHTTPRequestHandler,HTTPServer
import mlx.core as mx
from mlx_lm import load,generate
from mlx_lm.sample_utils import make_sampler
from mlx_lm.models.cache import make_prompt_cache,trim_prompt_cache
p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--adapter');p.add_argument('--port',type=int,default=15727);args=p.parse_args()
mx.random.seed(20261030)
model,tokenizer=load(args.model,adapter_path=args.adapter)
previous_tokens=[]
cache=make_prompt_cache(model)
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  global previous_tokens,cache
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  prompt=tokenizer.apply_chat_template(body['messages'],tokenize=False,add_generation_prompt=True)
  tokens=tokenizer.encode(prompt,add_special_tokens=tokenizer.bos_token is None or not prompt.startswith(tokenizer.bos_token))
  common=0
  for a,b in zip(tokens[:-1],previous_tokens):
   if a!=b:break
   common+=1
  if common:
   trim_prompt_cache(cache,cache[0].offset-common)
  else:cache=make_prompt_cache(model)
  max_tokens=max(1,min(int(body.get('max_tokens',1536)),1536))
  start=time.monotonic();output=generate(model,tokenizer,prompt=tokens[common:],prompt_cache=cache,max_tokens=max_tokens,sampler=make_sampler(temp=0),verbose=False)
  previous_tokens=tokens
  print('PREFIX_REUSED',common,flush=True)
  ni=len(tokenizer.encode(prompt));no=len(tokenizer.encode(output));print('GENERATED',no,time.monotonic()-start,flush=True)
  result={'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),'model':'Qwen2.5-1.5B-Instruct-mlx-'+('lora' if args.adapter else 'base'),'choices':[{'index':0,'message':{'role':'assistant','content':output},'finish_reason':'length' if no>=max_tokens else 'stop'}],'usage':{'prompt_tokens':ni,'completion_tokens':no,'total_tokens':ni+no}}
  data=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
HTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
