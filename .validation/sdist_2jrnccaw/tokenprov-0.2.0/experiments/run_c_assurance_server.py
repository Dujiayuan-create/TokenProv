"""Measure scoped Groth16 costs using retained TokenProv serving witnesses.

This CPU benchmark performs actual proofs; it starts no model or GPU service.
Foreground/audit phase replay does not measure concurrent inference latency.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import random
import shutil
import statistics
import subprocess
import threading
import time
import traceback


def canonical(value):
    return json.dumps(value, ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def digest(domain,value):
    return hashlib.sha256(b'TOKENPROV\0'+domain.encode()+b'\0'+canonical(value)).hexdigest()


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def write_csv(path,rows):
    columns=list(dict.fromkeys(k for row in rows for k in row if not isinstance(row[k],(dict,list))))
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)


class Worker:
    def __init__(self,index,node,script,build,out):
        self.index=index
        self.stderr=(out/f'worker-{index}.stderr.log').open('a',encoding='utf-8')
        self.process=subprocess.Popen([str(node),str(script),str(build)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=self.stderr,text=True,bufsize=1,cwd=out)
        self.responses=queue.Queue()
        def receive():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)
        threading.Thread(target=receive,daemon=True).start()
        try:
            self.ready=self.read()
            if not self.ready.get('ready'): raise RuntimeError(self.ready)
            if self.ready.get('curve_workers')!=2: raise RuntimeError('worker thread limit mismatch')
        except BaseException:
            self.process.terminate();self.process.wait(timeout=10);self.stderr.close()
            raise

    def read(self):
        try: line=self.responses.get(timeout=120)
        except queue.Empty as exc: raise TimeoutError('worker response timeout') from exc
        if not line: raise RuntimeError(f'worker {self.index} exited {self.process.poll()}')
        return json.loads(line)

    def call(self,action,**data):
        start=time.perf_counter_ns()
        self.process.stdin.write(json.dumps({'action':action,**data},ensure_ascii=False,separators=(',',':'))+'\n')
        self.process.stdin.flush()
        result=self.read()
        if result.get('ok') is not True: raise RuntimeError(result)
        result['rpc_wall_ms']=(time.perf_counter_ns()-start)/1e6
        return result

    def close(self):
        try:
            if self.process.poll() is None: self.call('shutdown');self.process.wait(timeout=10)
        finally:
            if self.process.poll() is None: self.process.terminate();self.process.wait(timeout=10)
            self.stderr.close()


def source_cohort(source):
    source=Path(source).resolve()
    requests=[json.loads(line) for line in (source/'requests.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    eligible={v['request_id'] for v in requests if v['system']=='TokenProv-Full' and not v['warmup'] and v['success'] and v.get('measurement_type')=='REAL_MODEL_SERVING'}
    references=[json.loads(line) for line in (source/'witnesses.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    references=[v for v in references if v['system']=='TokenProv-Full' and not v['warmup'] and v['request_id'] in eligible]
    groups=defaultdict(list);bundles={}; manifest=[];seen=set()
    for ref in references:
        path=(source/ref['file']).resolve()
        if not path.is_relative_to(source.resolve()): raise ValueError('witness escaped source root')
        if path not in bundles:
            raw=path.read_bytes();bundle=json.loads(raw)
            expected=bundle.pop('content_hash')
            if expected != ref['bundle_content_hash'] or digest('evidence-object/v1',bundle)!=expected:
                raise ValueError('bundle content hash mismatch')
            bundles[path]=(bundle,expected)
            manifest.append({'path':str(path.relative_to(source)),'sha256':hashlib.sha256(raw).hexdigest(),
                'content_hash':expected})
        bundle,expected=bundles[path]
        if ref['bundle_content_hash']!=expected: raise ValueError('inconsistent cached bundle hash')
        payload=bundle['payload']['provider_witnesses'][ref['witness_index']]
        if (payload['request_id'],payload['call_id'],payload['commitment']) != (ref['request_id'],ref['call_id'],ref['commitment']):
            raise ValueError('reference mismatch')
        if digest('commitment/provider-witness/v1',{'value':payload['witness'],'opening':payload['opening']})!=payload['commitment']:
            raise ValueError('witness commitment mismatch')
        identity=(ref['request_id'],ref['call_id'])
        if identity in seen: raise ValueError('duplicate witness reference')
        seen.add(identity)
        groups[ref['request_id']].append({'payload':payload,'binding':digest('deferred-proof-job/v1',payload)})
    for values in groups.values():
        values.sort(key=lambda v:v['payload']['call_id'])
        if len(values)!=8: raise ValueError('not eight calls per request')
    if set(groups)!=eligible: raise ValueError('successful request is missing witnesses')
    if len(groups)<100: raise ValueError('at least 100 successful eight-call TokenProv-Full requests required')
    return dict(sorted(groups.items())),manifest


def summarize(rows,keys):
    groups=defaultdict(list)
    for row in rows: groups[tuple(row[k] for k in keys)].append(row)
    output=[]
    for group,items in groups.items():
        result=dict(zip(keys,group));result['rounds']=len(items)
        for field in items[0]:
            if field in keys or field in ('round','seed') or not all(isinstance(v.get(field),(int,float)) and not isinstance(v.get(field),bool) for v in items): continue
            values=[v[field] for v in items]
            result[field+'_mean']=statistics.fmean(values)
            result[field+'_sample_sd']=statistics.stdev(values) if len(values)>1 else 0
            result[field+'_min']=min(values);result[field+'_max']=max(values)
        output.append(result)
    return output


def build_plan(args,source_requests):
    rounds=1 if args.smoke else args.rounds
    plan={'rounds':rounds,'seeds':list(range(args.seed,args.seed+rounds)),'source_requests':source_requests,'calls_per_source_request':8,
        'c1_warm_calls_per_round':32,'c1_legacy_commands_per_round':8,
        'c2_calls':[1,2,4,8],'c2_request_groups_per_configuration':4,
        'c3_requests':100,'c3_rates_percent':[1,5,10,25,50,100],
        'proof_workers':2,'curve_workers_per_process':2,'warmup_proofs_per_worker':3,
        'fixed_before_measurement':True,'retain_all_observations':True,
        'scope':'Retained serving-witness replay; scoped commitment/token-accounting circuit; shared-field checks on host',
        'c2_interpretation':'Phase timing of subsets of the original eight-call requests; no new inference and no live GPU interference measurement',
        'c3_sampling':'Exactly selected request counts out of 100; all eight calls of each selected request; deterministic nested random rankings per round',
        'c3_repetition':'Each rate generates and verifies its own actual proofs; no cost extrapolation',
        'timing':'Monotonic wall clocks; summed job wall durations are work, not CPU time; audit makespan separately measured',
        'persistence':'Separate private witness copies written and fsynced before challenge selection; originals unchanged'}
    if args.smoke:
        plan.update(c1_warm_calls_per_round=2,c1_legacy_commands_per_round=1,
            c2_calls=[1],c2_request_groups_per_configuration=1,c3_rates_percent=[1],formal=False)
    else:
        plan['formal']=True
    if args.skip_legacy: plan['c1_legacy_commands_per_round']=0
    return plan


def load_average():
    return list(os.getloadavg()) if hasattr(os,'getloadavg') else []


def run(args):
    source=Path(args.source_run).resolve()
    out=Path(args.output).resolve()
    if out.is_relative_to(source): raise ValueError('output must be outside the immutable source run')
    if out.exists(): raise FileExistsError(f'output directory already exists: {out}')
    build=Path(args.build_dir).resolve()
    assets=Path(__file__).resolve().parents[1]/'tools/zk'
    worker_script=assets/'c_assurance_worker.mjs'
    legacy_worker=Path(args.legacy_worker).resolve() if args.legacy_worker else assets/'proof_worker.mjs'
    node=shutil.which(str(args.node))
    if not node: raise ValueError('Node executable not found; install Node or pass --node')
    node=str(Path(node).resolve())
    cohort,source_manifest=source_cohort(source)
    requests=list(cohort)
    asset_files=[build/'tokenprov_accounting_js/tokenprov_accounting.wasm',build/'tokenprov_accounting_final.zkey',build/'verification_key.json',worker_script]
    if not args.skip_legacy: asset_files.append(legacy_worker)
    for path in asset_files:
        if not path.is_file(): raise ValueError(f'missing proof asset: {path}')
    package=assets/'node_modules/snarkjs/package.json'
    if not package.is_file(): raise ValueError('missing npm dependencies; run npm ci --prefix tools/zk')
    plan=build_plan(args,len(cohort))
    plan.update(source_run=str(source),build_dir=str(build),
        software={'node':subprocess.check_output([node,'--version'],text=True).strip(),
            'snarkjs':json.loads(package.read_text(encoding='utf-8'))['version']},
        hardware={'platform':platform.platform(),'logical_cpus':os.cpu_count(),
            'available_cpus':len(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else os.cpu_count(),
            'cpu_model':platform.processor(),'load_at_start':load_average(),
            'proof_execution':'CPU; no model or GPU service started by this benchmark'},
        assets=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in asset_files],
        started_utc=datetime.now(timezone.utc).isoformat())
    os.umask(0o077)
    out.mkdir(parents=True,exist_ok=False)
    for folder in ('proofs','retained','source'): (out/folder).mkdir()
    (out/'source/run_c_assurance_server.py').write_bytes(Path(__file__).read_bytes())
    (out/'source/c_assurance_worker.mjs').write_bytes(worker_script.read_bytes())
    save(out/'source_manifest.json',source_manifest)
    save(out/'cohort.json',[{'request_id':rid,'calls':cohort[rid]} for rid in requests])
    save(out/'plan.json',plan)
    raw=(out/'measurements.jsonl').open('x',encoding='utf-8',buffering=1)
    proofs=(out/'proofs/artifacts.jsonl').open('x',encoding='utf-8',buffering=1)
    aggregates=[];stats={'successful_proofs':0,'failed_proofs':0,'negative_controls':0,'negative_controls_rejected':0}
    total_configurations=len(plan['seeds'])*(1+bool(plan['c1_legacy_commands_per_round'])+2*len(plan['c2_calls'])+len(plan['c3_rates_percent']))
    status={'state':'running','completed_configurations':0,'total_configurations':total_configurations,'round':0,'stage':'initializing'}
    def progress(**values):
        status.update(values);status.update(stats);status['updated_utc']=datetime.now(timezone.utc).isoformat()
        status['load_average']=load_average()
        save(out/'progress.json',status);print(json.dumps(status),flush=True)
    def record(stage,round_index,record,**meta):
        artifact=record.pop('artifact',None)
        if artifact is not None:
            proofs.write(json.dumps({'stage':stage,'round':round_index,**meta,'artifact':artifact},separators=(',',':'))+'\n')
        raw.write(json.dumps({'stage':stage,'round':round_index,**meta,**record},separators=(',',':'))+'\n')
    def retain(items,path):
        started=time.perf_counter_ns()
        with path.open('xb') as f:
            for item in items: f.write(canonical(item)+b'\n')
            f.flush();os.fsync(f.fileno())
        return (time.perf_counter_ns()-started)/1e6
    workers=[]
    try:
        progress()
        for ri,seed in enumerate(plan['seeds'],1):
            progress(round=ri,stage='warmup')
            for i in range(plan['proof_workers']): workers.append(Worker(i,node,worker_script,build,out))
            for worker in workers:
                for wi in range(plan['warmup_proofs_per_worker']):
                    value=worker.call('full',**cohort[requests[wi]][0]);record('warmup',ri,value,worker=worker.index)
                    stats['successful_proofs']+=1
            rng=random.Random(seed); ranking=rng.sample(requests,len(requests))
            # Warm persistent-worker primitive baseline, measured independently.
            progress(stage='C1-warm')
            values=[]
            for ci in range(plan['c1_warm_calls_per_round']):
                item=cohort[ranking[ci//8]][ci%8]
                value=workers[0].call('full',**item);values.append(dict(value));record('C1-warm',ri,value,call=ci)
                stats['successful_proofs']+=1
            row={'experiment':'C1','mode':'persistent-warm','round':ri,'seed':seed,'proof_count':len(values)}
            for key in ['host_check_ms','circuit_input_ms','prepare_ms','circuit_witness_ms','groth16_prove_ms','full_prove_ms','verify_ms','rpc_wall_ms','proof_bytes','artifact_bytes']:
                data=[v[key] for v in values];row[key]=statistics.fmean(data)
                row[key+'_p95']=sorted(data)[int(.95*(len(data)-1))]
            aggregates.append(row);status['completed_configurations']+=1
            # Existing process-per-call adapter is retained as a separate baseline.
            progress(stage='C1-legacy-command')
            legacy=[]
            for ci in range(plan['c1_legacy_commands_per_round']):
                item=cohort[ranking[4]][ci];request={'schema_version':1,**item}
                t=time.perf_counter_ns()
                result=subprocess.run([str(node),str(legacy_worker),'prove',str(build)],input=json.dumps(request),text=True,capture_output=True,timeout=120,check=True,cwd=out)
                prover_ms=(time.perf_counter_ns()-t)/1e6;artifact=json.loads(result.stdout)
                t=time.perf_counter_ns()
                result=subprocess.run([str(node),str(legacy_worker),'verify',str(build)],input=json.dumps({**request,'artifact':artifact}),text=True,capture_output=True,timeout=120,check=True,cwd=out)
                verifier_ms=(time.perf_counter_ns()-t)/1e6
                if not json.loads(result.stdout)['verified']:raise RuntimeError('legacy verification failed')
                workers[0].call('prepare',**item)
                if not workers[0].call('verify',binding=item['binding'],artifact=artifact)['verified']:
                    raise RuntimeError('new worker rejected original adapter proof')
                value={'legacy_prover_command_ms':prover_ms,'legacy_verifier_command_ms':verifier_ms,
                    'native_full_prove_ms':artifact['native_prove_ms'],'proof_bytes':len(json.dumps(artifact['proof'],separators=(',',':')).encode()),'artifact':artifact}
                legacy.append(dict(value));record('C1-legacy-command',ri,value,call=ci);stats['successful_proofs']+=1
            if legacy:
                row={'experiment':'C1','mode':'legacy-command','round':ri,'seed':seed,'proof_count':len(legacy)}
                for key in ['legacy_prover_command_ms','legacy_verifier_command_ms','native_full_prove_ms','proof_bytes']:row[key]=statistics.fmean(v[key] for v in legacy)
                aggregates.append(row);status['completed_configurations']+=1
            # Independent wrong-charge, proof, commitment, and handoff controls.
            item=cohort[ranking[5]][0];good=workers[0].call('full',**item);stats['successful_proofs']+=1
            for name in ['public_signal','proof_point','commitment','shared_field']:
                bad=json.loads(json.dumps(good['artifact']));rejected=False
                if name in ('public_signal','proof_point'):
                    if name=='public_signal':bad['publicSignals'][0]=str(int(bad['publicSignals'][0])+1)
                    else:bad['proof']['pi_a'][0]=str(int(bad['proof']['pi_a'][0])+1)
                    try:rejected=workers[0].call('verify',binding=item['binding'],artifact=bad)['verified'] is False
                    except RuntimeError:rejected=True
                else:
                    payload=json.loads(json.dumps(item['payload']))
                    if name=='commitment':payload['opening']+='x'
                    else:
                        payload['witness']['shared']['call_id']='c-tampered'
                        payload['commitment']=digest('commitment/provider-witness/v1',{'value':payload['witness'],'opening':payload['opening']})
                        payload['receipt']['witness_commitment']=payload['commitment']
                    try:workers[0].call('prepare',payload=payload,binding=digest('deferred-proof-job/v1',payload))
                    except RuntimeError:rejected=True
                stats['negative_controls']+=1;stats['negative_controls_rejected']+=int(rejected)
                record('negative-control',ri,{'control':name,'rejected':rejected})
                if not rejected:raise RuntimeError('negative control accepted')
            # Replay the foreground and audit phases in their actual order.
            configs=[(n,mode) for n in plan['c2_calls'] for mode in ('Immediate','Deferred')]
            rng.shuffle(configs)
            for calls,mode in configs:
                progress(stage='C2',calls=calls,mode=mode)
                request_rows=[]
                for request_index,rid in enumerate(ranking[8:8+plan['c2_request_groups_per_configuration']]):
                    items=cohort[rid][:calls];pvalues=[];generated=[]
                    t=time.perf_counter_ns()
                    for item in items:pvalues.append(workers[0].call('prepare',**item))
                    persistence_ms=retain(items,out/f'retained/c2-r{ri}-k{calls}-{mode}-{request_index}.jsonl')
                    if mode=='Immediate':
                        for item in items:generated.append(workers[0].call('prove',binding=item['binding']))
                    foreground_ms=(time.perf_counter_ns()-t)/1e6
                    t=time.perf_counter_ns();verify_values=[]
                    if mode=='Deferred':
                        for item in items:
                            value=workers[0].call('prove',binding=item['binding']);generated.append(value)
                            verify_values.append(workers[0].call('verify',binding=item['binding'],artifact=value['artifact']))
                    else:
                        for item,value in zip(items,generated):verify_values.append(workers[0].call('verify',binding=item['binding'],artifact=value['artifact']))
                    audit_ms=(time.perf_counter_ns()-t)/1e6
                    if not all(v['verified'] for v in verify_values):raise RuntimeError('C2 proof rejected')
                    for item,value,verified in zip(items,generated,verify_values):
                        record('C2-call',ri,{**value,'verify_ms':verified['verify_ms']},calls=calls,mode=mode,request_id=rid,call_id=item['payload']['call_id'])
                        stats['successful_proofs']+=1
                    row={'experiment':'C2','round':ri,'seed':seed,'calls':calls,'mode':mode,'request_id':rid,
                        'foreground_ms':foreground_ms,'prepare_rpc_ms':sum(v['rpc_wall_ms'] for v in pvalues),
                        'prepare_native_ms':sum(v['prepare_ms'] for v in pvalues),'witness_persistence_ms':persistence_ms,
                        'full_prove_ms':sum(v['full_prove_ms'] for v in generated),
                        'online_full_prove_ms':sum(v['full_prove_ms'] for v in generated) if mode=='Immediate' else 0,
                        'audit_full_prove_ms':sum(v['full_prove_ms'] for v in generated) if mode=='Deferred' else 0,
                        'verify_ms':sum(v['verify_ms'] for v in verify_values),'audit_wall_ms':audit_ms,
                        'proof_bytes':sum(v['proof_bytes'] for v in generated),'proof_count':calls}
                    request_rows.append(row);record('C2-request',ri,dict(row))
                row={'experiment':'C2','round':ri,'seed':seed,'calls':calls,'mode':mode,'measured_requests':len(request_rows)}
                for key in request_rows[0]:
                    if key not in row and isinstance(request_rows[0][key],(int,float)):row[key]=statistics.fmean(v[key] for v in request_rows)
                aggregates.append(row);status['completed_configurations']+=1
            # Fix and persist all 100 requests before choosing any challenged set.
            progress(stage='C3-retention')
            selected_ids=ranking[:100];retained_items=[item for rid in selected_ids for item in cohort[rid]]
            prep_values=[]
            for item in retained_items:prep_values.append(workers[0].call('prepare',**item))
            persist_ms=retain(retained_items,out/f'retained/c3-r{ri}-all100.jsonl')
            save(out/f'c3-ranking-r{ri}.json',{'seed':seed,'request_ids':selected_ids,'rates':plan['c3_rates_percent']})
            rates=plan['c3_rates_percent'][:];rng.shuffle(rates)
            for rate in rates:
                progress(stage='C3',rate_percent=rate,proof_jobs=rate*8)
                items=[item for rid in selected_ids[:rate] for item in cohort[rid]]
                leases=queue.Queue()
                for worker in workers:leases.put(worker)
                batch_start=time.perf_counter_ns()
                def task(item):
                    worker=leases.get();started=time.perf_counter_ns()
                    try:
                        value=worker.call('full',**item)
                        return {'worker':worker.index,'queue_wait_ms':(started-batch_start)/1e6,
                            'service_wall_ms':(time.perf_counter_ns()-started)/1e6,**value,
                            'request_id':item['payload']['request_id'],'call_id':item['payload']['call_id']}
                    finally:leases.put(worker)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results=[]
                    for index,value in enumerate(pool.map(task,items),1):
                        results.append(dict(value));record('C3-job',ri,value,rate_percent=rate)
                        stats['successful_proofs']+=1
                        if index%100==0:progress(jobs_completed=index)
                wall_ms=(time.perf_counter_ns()-batch_start)/1e6
                row={'experiment':'C3','round':ri,'seed':seed,'audit_rate_percent':rate,'requests':100,
                    'challenged_requests':rate,'proof_count':len(results),'audit_makespan_ms':wall_ms,
                    'proofs_per_second':len(results)/(wall_ms/1000),'proof_bytes':sum(v['proof_bytes'] for v in results),
                    'sum_full_prove_ms':sum(v['full_prove_ms'] for v in results),
                    'sum_verify_ms':sum(v['verify_ms'] for v in results),
                    'sum_job_service_wall_ms':sum(v['service_wall_ms'] for v in results),
                    'amortized_full_prove_ms_per_request':sum(v['full_prove_ms'] for v in results)/100,
                    'amortized_prove_verify_ms_per_request':sum(v['full_prove_ms']+v['verify_ms'] for v in results)/100,
                    'amortized_audit_wall_ms_per_request':wall_ms/100,
                    'online_prepare_native_ms_per_request':sum(v['prepare_ms'] for v in prep_values)/100,
                    'online_prepare_rpc_ms_per_request':sum(v['rpc_wall_ms'] for v in prep_values)/100,
                    'online_witness_persistence_ms_per_request':persist_ms/100,'online_proving_ms':0,
                    'mean_job_queue_wait_ms':statistics.fmean(v['queue_wait_ms'] for v in results),
                    'max_job_queue_wait_ms':max(v['queue_wait_ms'] for v in results)}
                aggregates.append(row);status['completed_configurations']+=1
                write_csv(out/'round_metrics.csv',aggregates)
            for worker in workers:worker.close()
            workers=[]
        write_csv(out/'round_metrics.csv',aggregates)
        for experiment,keys in [('C1',['experiment','mode']),('C2',['experiment','calls','mode']),('C3',['experiment','audit_rate_percent'])]:
            rows=[v for v in aggregates if v['experiment']==experiment]
            write_csv(out/(experiment.lower()+'_summary.csv'),summarize(rows,keys))
        raw.close();proofs.close()
        if stats['negative_controls']!=stats['negative_controls_rejected']:raise RuntimeError('invalid negative controls')
        verification={'source_bundle_hashes_verified':len(source_manifest),
            'source_witness_commitments_verified':sum(map(len,cohort.values())),
            'source_files_unchanged':all(hashlib.sha256((source/v['path']).read_bytes()).hexdigest()==v['sha256'] for v in source_manifest),
            'aggregate_rows':len(aggregates),'all_configurations_expected_rounds':all(v['rounds']==plan['rounds'] for e,keys in [('C1',['experiment','mode']),('C2',['experiment','calls','mode']),('C3',['experiment','audit_rate_percent'])] for v in summarize([r for r in aggregates if r['experiment']==e],keys)),**stats}
        if not verification['source_files_unchanged'] or not verification['all_configurations_expected_rounds']:
            raise RuntimeError('final verification failed')
        save(out/'verification.json',verification)
        progress(state='complete',stage='complete',completed_configurations=total_configurations,total_configurations=total_configurations)
        save(out/'output_manifest.json',[{'file':str(p.relative_to(out)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(out.rglob('*')) if p.is_file() and p.name not in ('output_manifest.json','progress.json')])
    except BaseException as exc:
        stats['failed_proofs']+=1
        progress(state='failed',error=repr(exc));(out/'error.txt').write_text(traceback.format_exc())
        raise
    finally:
        for worker in workers:worker.close()
        if not raw.closed:raw.close()
        if not proofs.closed:proofs.close()


def make_parser():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',required=True,type=Path,help='completed serving run with requests.jsonl, witnesses.jsonl and evidence bundles')
    parser.add_argument('--build-dir',required=True,type=Path,help='matching circuit WASM, zkey and verification_key.json directory')
    parser.add_argument('--output','--out',required=True,type=Path,help='new directory; existing paths are rejected')
    parser.add_argument('--node',default='node',help='Node executable, resolved from PATH by default')
    parser.add_argument('--legacy-worker',type=Path,help='override the process-per-call worker; default tools/zk/proof_worker.mjs')
    parser.add_argument('--skip-legacy',action='store_true',help='omit the process-per-call C1 comparison')
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--smoke',action='store_true',help='one small round of actual proofs; still needs 100 eight-call source requests')
    return parser


if __name__=='__main__':
    parser=make_parser();args=parser.parse_args()
    if args.rounds<1: parser.error('--rounds must be positive')
    run(args)
