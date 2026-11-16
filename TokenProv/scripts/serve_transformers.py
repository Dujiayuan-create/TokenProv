"""Local, bounded Transformers microbatch service for controlled experiments."""
import argparse,concurrent.futures,hashlib,json,os,queue,signal,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir',required=True)
    p.add_argument('--model-name',required=True)
    p.add_argument('--port',type=int,default=8000)
    p.add_argument('--max-batch',type=int,default=8)
    p.add_argument('--batch-window-ms',type=float,default=2)
    p.add_argument('--max-context',type=int,default=4096)
    p.add_argument('--threads',type=int,default=4)
    p.add_argument('--memory-fraction',type=float,default=.10)
    a=p.parse_args(argv)
    if not 1 <= a.port <= 65535:p.error('--port must be between 1 and 65535')
    if a.max_batch < 1 or a.max_context < 1 or a.threads < 1:
        p.error('--max-batch, --max-context, and --threads must be positive')
    if not 0 <= a.batch_window_ms <= 1000:p.error('--batch-window-ms must be between 0 and 1000')
    if not 0 < a.memory_fraction <= 1:p.error('--memory-fraction must be in (0, 1]')
    return a


def encode_request(payload, tokenizer, model_name, max_context):
    """Validate the supported nonstreaming profile before enqueueing GPU work."""
    if not isinstance(payload,dict):raise ValueError('request must be an object')
    if payload.get('model')!=model_name:raise ValueError('wrong model')
    if payload.get('temperature',0)!=0:raise ValueError('temperature must be zero')
    if payload.get('stream',False) is not False:raise ValueError('streaming is unsupported')
    if payload.get('chat_template_kwargs',{'enable_thinking':False})!={'enable_thinking':False}:
        raise ValueError('thinking is disabled in this profile')
    messages=payload.get('messages')
    if not isinstance(messages,list) or not messages or any(
            not isinstance(message,dict) or not isinstance(message.get('role'),str)
            or not isinstance(message.get('content'),str) for message in messages):
        raise ValueError('messages must contain text role/content objects')
    limit=payload.get('max_tokens',256)
    if type(limit) is not int or not 1<=limit<=1024:raise ValueError('invalid max_tokens')
    ids=tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,enable_thinking=False)
    if hasattr(ids,'keys'):ids=ids['input_ids']
    if not isinstance(ids,list) or any(type(value) is not int for value in ids):
        raise ValueError('tokenizer must return a list of token IDs')
    if len(ids)+limit>max_context:raise ValueError('context limit exceeded')
    return ids,limit


def main(argv=None):
    a=parse_args(argv)
    model_directory=Path(a.model_dir).expanduser().resolve()
    if not model_directory.is_dir():raise SystemExit('--model-dir must name an existing local directory')
    import torch,transformers
    from transformers import AutoModelForCausalLM,AutoTokenizer
    if not torch.cuda.is_available():raise SystemExit('A compatible CUDA GPU and PyTorch build are required')
    torch.set_num_threads(a.threads)
    torch.cuda.set_per_process_memory_fraction(a.memory_fraction,0)
    tokenizer=AutoTokenizer.from_pretrained(model_directory,local_files_only=True,trust_remote_code=False)
    tokenizer.padding_side='left'
    if tokenizer.pad_token_id is None:tokenizer.pad_token=tokenizer.eos_token
    model=AutoModelForCausalLM.from_pretrained(model_directory,local_files_only=True,trust_remote_code=False,
        use_safetensors=True,torch_dtype=torch.bfloat16,attn_implementation='sdpa').to('cuda:0').eval()
    eos=model.generation_config.eos_token_id
    eos_ids=set(eos if isinstance(eos,list) else [eos] if eos is not None else [])
    jobs=queue.Queue(maxsize=128)
    stop=threading.Event()
    metadata={'backend':'transformers-sdpa-microbatch','model':a.model_name,'model_path':str(model_directory),
        'torch':torch.__version__,'transformers':transformers.__version__,'cuda':torch.version.cuda,
        'gpu':torch.cuda.get_device_name(0),'gpu_visible':os.environ.get('CUDA_VISIBLE_DEVICES'),
        'dtype':'bfloat16','prefix_cache':False,'hidden_reasoning':False,'max_context':a.max_context,
        'max_batch':a.max_batch,'batch_window_ms':a.batch_window_ms,'memory_fraction':a.memory_fraction,
        'server_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    def encode(payload):
        return encode_request(payload,tokenizer,a.model_name,a.max_context)
    def worker():
        while not stop.is_set():
            try:first=jobs.get(timeout=.2)
            except queue.Empty:continue
            batch=[first]
            deadline=time.perf_counter()+a.batch_window_ms/1000
            while len(batch)<a.max_batch:
                remaining=deadline-time.perf_counter()
                if remaining<=0:break
                try:batch.append(jobs.get(timeout=remaining))
                except queue.Empty:break
            try:
                tokenized=tokenizer.pad([{'input_ids':j[1]} for j in batch],padding=True,return_tensors='pt')
                tokenized={k:v.to('cuda:0') for k,v in tokenized.items()}
                width=tokenized['input_ids'].shape[1]
                cap=max(j[2] for j in batch)
                begun=time.perf_counter_ns()
                with torch.inference_mode():
                    outputs=model.generate(**tokenized,max_new_tokens=cap,do_sample=False,use_cache=True,
                        pad_token_id=tokenizer.pad_token_id)
                torch.cuda.synchronize()
                gpu_ns=time.perf_counter_ns()-begun
                for i,(payload,ids,limit,future) in enumerate(batch):
                    generated=outputs[i,width:].tolist()[:limit]
                    for n,t in enumerate(generated):
                        if t in eos_ids:
                            generated=generated[:n+1];break
                    text=tokenizer.decode(generated,skip_special_tokens=True)
                    response={'id':'chatcmpl-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),
                        'model':a.model_name,'choices':[{'index':0,'message':{'role':'assistant','content':text},
                        'finish_reason':'stop' if generated and generated[-1] in eos_ids else 'length'}],
                        'usage':{'prompt_tokens':len(ids),'completion_tokens':len(generated),'total_tokens':len(ids)+len(generated)},
                        'experiment_backend':{'batch_size':len(batch),'batch_generate_ns':gpu_ns}}
                    if payload.get('return_token_ids') is True:
                        response['token_ids']={'prompt':ids,'completion':generated}
                    future.set_result(response)
                del tokenized,outputs
            except Exception as exc:
                print(json.dumps({'event':'batch_failed','error_type':type(exc).__name__,'error':str(exc)}),flush=True)
                for *_,future in batch:
                    if not future.done():future.set_exception(exc)
                if isinstance(exc,torch.cuda.OutOfMemoryError):torch.cuda.empty_cache()
            finally:
                for _ in batch:jobs.task_done()
    thread=threading.Thread(target=worker,name='gpu-batch-worker',daemon=True)
    thread.start()
    # A real warmup is completed before readiness is advertised.
    future=concurrent.futures.Future()
    warm={'model':a.model_name,'messages':[{'role':'user','content':'Reply OK.'}],'max_tokens':8,'temperature':0}
    ids,limit=encode(warm);jobs.put((warm,ids,limit,future));future.result(timeout=180)
    print(json.dumps({'event':'ready','metadata':metadata}),flush=True)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value):
            body=json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):
            if self.path=='/health':self.reply(200,{'ready':True,'metadata':metadata})
            elif self.path=='/v1/models':self.reply(200,{'data':[{'id':a.model_name,'object':'model'}]})
            else:self.reply(404,{'error':'not found'})
        def do_POST(self):
            if self.path!='/v1/chat/completions':self.reply(404,{'error':'not found'});return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2*1024**2:raise ValueError('invalid body size')
                payload=json.loads(self.rfile.read(length))
                ids,limit=encode(payload)
                future=concurrent.futures.Future()
                jobs.put((payload,ids,limit,future),timeout=30)
                self.reply(200,future.result(timeout=180))
            except (ValueError,KeyError,TypeError) as e:self.reply(400,{'error':type(e).__name__})
            except Exception as e:self.reply(503,{'error':type(e).__name__})
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    def shutdown(*_):
        stop.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    try:server.serve_forever()
    finally:stop.set();server.server_close();thread.join(timeout=5)

if __name__=='__main__':main()
