"""Fresh CPU storage, historical security, and cumulative validation experiments.

These drivers reproduce the measurement methods, not historical hardware timing.
Use paper/reproduce.py to regenerate paper graphics from retained observations.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',choices=['storage','security','ablation'],required=True)
    parser.add_argument('--output',type=Path,required=True,help='New directory; existing paths are rejected')
    parser.add_argument('--seeds',default='42,43,44,45,46')
    parser.add_argument('--trials',type=int,default=100,help='Security repetitions per fixture')
    parser.add_argument('--samples',type=int,default=1000,help='Ablation samples per variant per seed')
    parser.add_argument('--warmups',type=int,default=100)
    parser.add_argument('--calls',default='1,2,4,8,16,32,64',help='Storage call-count sweep')
    args=parser.parse_args(argv)
    seeds=tuple(int(x) for x in args.seeds.split(','))
    calls=tuple(int(x) for x in args.calls.split(','))
    if not seeds or len(set(seeds))!=len(seeds) or not calls or min(calls)<1:
        parser.error('Seeds must be unique and call counts positive')
    if args.trials<1 or args.samples<1 or args.warmups<0:
        parser.error('Trials/samples must be positive; warmups must be nonnegative')
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    for name in ['processed','raw','runtime']:
        (output/'results'/name).mkdir(parents=True)
    module_name={'ablation':'components'}.get(args.experiment,args.experiment)
    path=Path(__file__).resolve().parent/'protocol_drivers'/f'{module_name}.py'
    spec=importlib.util.spec_from_file_location('paper_protocol_'+module_name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Historical storage layouts are deeply nested; support Windows long paths.
    module.ROOT=Path('\\\\?\\'+str(output)) if sys.platform=='win32' else output
    module.SEEDS=seeds
    (output/'configuration.json').write_text(json.dumps({
        'experiment':args.experiment,'seeds':seeds,'calls':calls,'trials':args.trials,
        'samples':args.samples,'warmups':args.warmups,'measurement_type':'PROTOCOL_MICROBENCHMARK',
        'historical_numeric_reproduction':False},indent=2)+'\n',encoding='utf-8')
    if args.experiment=='ablation':
        module.SAMPLES=args.samples
        module.WARMUPS=args.warmups
        raw=[]
        rows=module.run_g2(raw)
        module.write_csv('G2_performance_ablation.csv',rows)
        (output/'results/raw/performance_ablation.jsonl').write_text(
            ''.join(json.dumps(r)+'\n' for r in raw),encoding='utf-8')
        print(json.dumps({'observations':len(raw),'round_rows':len(rows),'output':str(output)}))
    else:
        module.TRIALS=args.trials
        module.CALLS=calls
        module.main()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
