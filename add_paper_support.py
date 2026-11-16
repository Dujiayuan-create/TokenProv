"""One-time portable adapters for retained CPU experiment drivers."""
import ast
import hashlib
import json
from pathlib import Path

BASE=Path(__file__).resolve().parent
ROOT=BASE.parent
PUBLIC=BASE/'TokenProv'


def main():
    # The old plot bootstrap shared a contiguous block with these constants.
    for filename,original,names in [
        ('plot_b3_b4.py','plot_b3_scalability.py',{'PROTOCOL_RUN','CALLS','FACTORS','SYSTEMS','LABELS','MODELS'}),
        ('plot_f.py','plot_f_ablation.py',{'VARIANTS'})]:
        source=(ROOT/'artifacts/server_20261113'/original).read_text(encoding='utf-8')
        lines=source.splitlines(keepends=True)
        constants=''.join(''.join(lines[n.lineno-1:n.end_lineno]) for n in ast.parse(source).body
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets))
        path=PUBLIC/'paper/scripts'/filename
        text=path.read_text(encoding='utf-8')
        marker="OUT = output_dir('"+('b3_b4' if filename=='plot_b3_b4.py' else 'f')+"')\n"
        if constants not in text:
            path.write_text(text.replace(marker,marker+constants),encoding='utf-8')

    legacy=PUBLIC/'experiments/protocol_drivers'
    legacy.mkdir(exist_ok=True)
    for src,dst in [('run_protocol_scalability.py','storage.py'),('security_experiments.py','security.py'),('run_remaining_protocol.py','components.py')]:
        text=(ROOT/'experiments'/src).read_text(encoding='utf-8')
        text=text.replace('ROOT = Path(__file__).resolve().parents[1]', 'ROOT = None  # Set to a fresh output directory by experiments/paper_protocol.py')
        text=text.replace('range(100)', 'range(TRIALS)') if dst=='security.py' else text
        if dst=='security.py':
            text=text.replace('SEEDS = (42, 43, 44, 45, 46)','SEEDS = (42, 43, 44, 45, 46)\nTRIALS = 100')
            text=text.replace('security_experiments_20260813.jsonl','security_experiments.jsonl')
        if dst=='storage.py':
            # Retain only the original storage/call-count loop, not unrelated suites.
            start=text.index('    # C2 uses')
            text=text[:start]+'''    raw_path = ROOT / 'results/raw/storage_scaling.jsonl'
    raw_path.write_text(''.join(json.dumps(row)+'\\n' for row in raw),encoding='utf-8')
    print(json.dumps({'storage_rows':len(e1),'protocol_rows':len(c1)}))
'''
        if dst=='components.py':
            nodes=ast.parse(text).body
            keep=[]
            lines=text.splitlines(keepends=True)
            for node in nodes:
                if isinstance(node,ast.FunctionDef) and node.name not in {'write_csv','raw_row','run_g2'}:
                    continue
                if isinstance(node,ast.If):
                    continue
                keep.append(''.join(lines[node.lineno-1:node.end_lineno]))
            text='\n'.join(keep)+'\n'
            text=text.replace('"sampling": "100 warmups + 1000 measurements"', '"sampling": f"{WARMUPS} warmups + {SAMPLES} measurements"')
            text=text.replace('Run remaining CPU protocol microbenchmarks: C4, C5, D1 and G2.', 'Cumulative validation microbenchmark for Section F (historical G2).')
        text=text.replace('"storage": "ext4 fsync"', '"storage": "local filesystem fsync"')
        (legacy/dst).write_text(text,encoding='utf-8')
    print('Portable protocol drivers added; original drivers unchanged.')


if __name__=='__main__':main()
