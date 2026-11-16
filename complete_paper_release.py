"""Curate retained paper artifacts without modifying the original project.

This maintenance script is not part of the public Python package. It imports no
original experiment module and never executes inference or proving.
"""
from pathlib import Path
import ast
import csv
import hashlib
import json
import shutil

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
PUBLIC = BASE / 'TokenProv'
PAPER = PUBLIC / 'paper'
OLD = ROOT / 'artifacts/server_20261113'
OLD_C = ROOT / 'artifacts/server_20261116'
SOURCES = {}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source, destination):
    source = source.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    SOURCES[destination.relative_to(PAPER).as_posix()] = {
        'source': source.relative_to(ROOT).as_posix(), 'source_sha256': sha(source)}


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def replace_function(source, name, replacement):
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + replacement.rstrip() + '\n\n' + ''.join(lines[node.end_lineno:])


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def main():
    if PAPER.exists():
        raise FileExistsError('Refusing to overwrite an existing curated paper tree')
    # Snapshot only the files read by this task, plus all existing original code.
    originals = list((ROOT / 'tokenprov').rglob('*.py')) + list((ROOT / 'experiments').rglob('*.py'))
    originals += list(OLD.glob('*.py')) + list(OLD_C.glob('*.py'))
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in originals}
    put(BASE / '.validation/paper_original_snapshot.json', json.dumps(before, indent=2) + '\n')
    for folder in ['data', 'scripts', 'published']:
        (PAPER / folder).mkdir(parents=True, exist_ok=True)

    sections = {
        'b1': (OLD / 'b1_request_traces', 'b1_five_model_latency_throughput'),
        'b2': (OLD / 'b2_cost_breakdown', 'b2_reference_cost_breakdown'),
        'b3_b4': (OLD / 'b3_scalability', 'b3_b4_scalability_variability'),
        'c': (OLD_C / 'c_assurance', 'c_deferred_proving_audit_rate'),
        'd': (OLD / 'd_evidence_settlement', 'd_evidence_settlement'),
        'e': (OLD / 'e_security_accountability', None),
        'f': (OLD / 'f_ablation', 'f_component_waterfalls'),
        'cases': (OLD / 'appendix_provenance_cases', 'token_provenance_case_boxes'),
    }
    # Only the selected final figure, CSVs and non-preview LaTeX; no deprecated E figure.
    for section, (directory, stem) in sections.items():
        for path in directory.iterdir():
            if not path.is_file():
                continue
            if path.suffix == '.csv' or (path.suffix == '.tex' and 'preview' not in path.name):
                copy(path, PAPER / 'published' / section / path.name)
            elif stem and path.stem == stem and path.suffix in {'.pdf', '.png', '.svg'}:
                copy(path, PAPER / 'published' / section / path.name)
    # Unchanged input tables, including every retained round.
    for name in ['five_model_three_round_metrics.csv', 'five_model_three_round_variability.csv']:
        copy(OLD / name, PAPER / 'data' / name)
    input_map = {
        'b1/request_latency_data.csv': OLD / 'b1_request_traces/request_latency_data.csv',
        'b2/component_round_means.csv': OLD / 'b2_cost_breakdown/b2_component_round_means.csv',
        'b3_b4/protocol_scaling.csv': OLD / 'b3_scalability/b3_protocol_scaling.csv',
        'b3_b4/concurrency.csv': OLD / 'b3_scalability/b3_concurrency_original_round.csv',
        'd/current_round_data.csv': OLD / 'd_evidence_settlement/current_round_data.csv',
        'd/current_model_summary.csv': OLD / 'd_evidence_settlement/current_model_summary.csv',
        'd/E1_storage_vs_calls.csv': ROOT / 'results/processed/E1_storage_vs_calls.csv',
        'e/security.jsonl': OLD / 'final_evidence/runs/server_runs/20261113T072445Z_610546852f92/security.jsonl',
        'cases/case_figure_data.json': OLD / 'appendix_provenance_cases/case_figure_data.json',
    }
    for name in ['F1_attack_detection_summary.csv', 'F2_cross_call_splicing.csv',
                 'F3_fault_localization_summary.csv', 'F4_collusion.csv']:
        input_map['e/' + name] = ROOT / 'results/processed' / name
    for name in ['G1_security_ablation.csv', 'G2_performance_ablation.csv']:
        input_map['f/' + name] = ROOT / 'results/processed' / name
    c_run = OLD_C / 'c_assurance/c_assurance_20261116_3round'
    for name in ['round_metrics.csv', 'verification.json', 'plan.json', 'measurements.jsonl']:
        input_map['c/' + name] = c_run / name
    signing = ROOT / 'artifacts/local_tee_20261114/vbs_key_signing/20261114T080139302306Z'
    for path in signing.iterdir():
        if path.name in {'summary.json', 'verification.json', 'replayed_receipts.json', 'measurements.jsonl'} or path.name.startswith('example-'):
            input_map['c/signing/' + path.name] = path
    for target, source in input_map.items():
        copy(source, PAPER / 'data' / target)

    # Extract only unsigned request measurement fields. No messages, service
    # addresses, keys, evidence bundles or signed payloads are modified/exported.
    metrics = csv_rows(OLD / 'five_model_three_round_metrics.csv')
    cached = {}
    observations = []
    raw_hashes = {}
    for metric in metrics:
        identity = metric['source'], metric['run_id']
        if identity not in cached:
            parent = OLD / metric['source'] / 'runs'
            candidates = [parent / metric['run_id'], parent / 'server_runs' / metric['run_id']]
            candidates += [p / 'server_runs' / metric['run_id'] for p in parent.iterdir() if p.is_dir()]
            run = next(p for p in candidates if (p / 'requests.jsonl').is_file())
            source = run / 'requests.jsonl'
            cached[identity] = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line]
            raw_hashes[source.relative_to(ROOT).as_posix()] = sha(source)
        selected = [r for r in cached[identity] if not r['warmup'] and r['system'] == metric['system']
                    and r['repetition'] == int(metric['within_run_repetition'])]
        assert len(selected) == 128 and all(r['success'] for r in selected)
        for row in selected:
            observations.append({
                'model': metric['model'], 'system': metric['system'], 'round': int(metric['round']),
                **{k: row[k] for k in ['request_id', 'actual_calls', 'success', 'request_pipeline_ns', 'timings_ns']}})
    assert len(observations) == 7680
    target = PAPER / 'data/b2/request_measurements.jsonl'
    put(target, ''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in observations))
    SOURCES[target.relative_to(PAPER).as_posix()] = {
        'transformation': 'Selection of unsigned timing/count fields; all 7680 measured requests retained',
        'source_files_sha256': raw_hashes}

    scripts = {
        'plot_b1.py': OLD / 'plot_b1_request_traces.py',
        'plot_b2.py': OLD / 'plot_b2_cost_breakdown.py',
        'plot_b3_b4.py': OLD / 'plot_b3_scalability.py',
        'plot_c.py': OLD_C / 'plot_c_assurance.py',
        'write_c.py': OLD_C / 'write_c_manuscript.py',
        'plot_d.py': OLD / 'plot_d_evidence_settlement.py',
        'tables_e.py': OLD / 'e_security_accountability/prepare_e_tables.py',
        'plot_f.py': OLD / 'plot_f_ablation.py',
        'tables_f.py': OLD / 'f_ablation/prepare_f_tables.py',
        'plot_cases.py': OLD / 'plot_appendix_provenance_cases.py',
    }
    header = 'from paper_paths import DATA, PAPER, output_dir, read_csv\n'
    for name, path in scripts.items():
        text = path.read_text(encoding='utf-8')
        section = {'plot_b1.py':'b1','plot_b2.py':'b2','plot_b3_b4.py':'b3_b4',
                   'plot_c.py':'c','write_c.py':'c','plot_d.py':'d','tables_e.py':'e',
                   'plot_f.py':'f','tables_f.py':'f','plot_cases.py':'cases'}[name]
        # Move imports after __future__ where needed, then replace local bootstrap.
        lines = text.splitlines(keepends=True)
        start = next(i for i,l in enumerate(lines) if l.startswith('HERE =') or l.startswith('ROOT ='))
        end = start
        if name in {'tables_e.py','tables_f.py'}:
            end = next(i for i in range(start,len(lines)) if lines[i].startswith('SYSTEMS =') or lines[i].startswith('VARIANTS ='))
        elif name == 'write_c.py':
            end = next(i for i in range(start,len(lines)) if lines[i].startswith('def read_csv'))
        else:
            while end < len(lines) and (lines[end].strip() or end == start):
                end += 1
        init = header + f"HERE = DATA\nROOT = PAPER\nOUT = output_dir('{section}')\n"
        if name in {'tables_e.py','tables_f.py'}:
            init += 'HERE = OUT\n'
            source_names = {
                'tables_e.py': {'current_security':'security.jsonl','historical_attacks':'F1_attack_detection_summary.csv',
                   'historical_splicing':'F2_cross_call_splicing.csv','historical_localization':'F3_fault_localization_summary.csv',
                   'historical_collusion':'F4_collusion.csv'},
                'tables_f.py': {'rule_gating':'G1_security_ablation.csv','cumulative_cost':'G2_performance_ablation.csv'}}[name]
            init += 'SOURCES = {\n' + ''.join(f"    {k!r}: DATA / {section!r} / {v!r},\n" for k,v in source_names.items()) + '}\n'
        if name in {'plot_c.py','write_c.py'}:
            init += "SOURCE = DATA / 'c'\nSIGNING = DATA / 'c/signing'\nVBS = SIGNING / 'summary.json'\n"
        text = ''.join(lines[:start]) + init + '\n' + ''.join(lines[end:])
        if name == 'plot_b1.py':
            a = text.index('    runs = {}')
            b = text.index('    assert len(exported) == 7680', a)
            text = text[:a] + '''    exported = read_csv(DATA / 'b1/request_latency_data.csv')
    traces, batches = {}, {}
    for metric in metrics:
        model, system, rnd = metric['model'], metric['system'], int(metric['round'])
        selected = sorted([r for r in exported if r['model'] == model and r['system'] == system
                           and int(r['round']) == rnd], key=lambda r:int(r['request_index_within_round']))
        assert len(selected) == 128
        latencies = [float(r['latency_pipeline_s']) for r in selected]
        rates = {float(r['batch_throughput_requests_s']) for r in selected}
        assert len(rates) == 1
        throughput = rates.pop()
        assert math.isclose(throughput, float(metric['throughput_requests_s']), rel_tol=1e-10)
        assert math.isclose(statistics.mean(latencies)*1000,float(metric['mean_pipeline_ms']),rel_tol=1e-10)
        traces[(model,system,rnd)] = latencies
        batches[(model,system,rnd)] = throughput

''' + text[b:]
            text = text.replace("out = HERE / 'b1_request_traces'", 'out = OUT')
        elif name == 'plot_b2.py':
            a = text.index('    cached, per_round, identities = {}, [], set()')
            b = text.index('    assert len(identities) == 7680',a)
            text = text[:a] + '''    raw = [json.loads(line) for line in (DATA / 'b2/request_measurements.jsonl').read_text().splitlines()]
    per_round, identities = [], set()
    max_closure_error = 0
    for metric in metrics:
        selected = [r for r in raw if r['model'] == metric['model'] and r['system'] == metric['system']
                    and r['round'] == int(metric['round'])]
        assert len(selected) == 128 and all(r['success'] and r['actual_calls']==8 for r in selected)
        for row in selected:
            identity = row['model'],row['system'],row['round'],row['request_id']
            assert identity not in identities
            identities.add(identity)
        costs = [request_cost(row) for row in selected]
        means = {key:statistics.mean(cost[key] for cost in costs) for key in ALL_KEYS}
        assert math.isclose(means['online_non_llm'],float(metric['mean_online_non_llm_ms']),rel_tol=1e-10)
        max_closure_error = max(max_closure_error,abs(means['pipeline_non_llm']-means['online_non_llm']-means['remaining_processing']))
        per_round.append({'model':metric['model'],'system':metric['system'],'round':int(metric['round']),
            'requests':128,'source':metric['source'],'run_id':metric['run_id'],
            **{key+'_ms':value for key,value in means.items()}})
''' + text[b:]
            text = replace_function(text, 'find_run', '''def find_run(*args):
    raise RuntimeError('Public reproduction uses bundled measurement extracts, not private run archives.')''')
        elif name == 'plot_b3_b4.py':
            text = replace_function(text, 'protocol_data', '''def protocol_data():
    rows = read_csv(DATA / 'b3_b4/protocol_scaling.csv', numeric=True)
    assert len(rows)==10
    write_csv(OUT / 'b3_protocol_scaling.csv', rows)
    return rows''')
            text = replace_function(text, 'concurrency_data', '''def concurrency_data():
    rows = read_csv(DATA / 'b3_b4/concurrency.csv', numeric=True)
    assert len(rows)==12 and sum(r['measured_requests'] for r in rows)==1088
    write_csv(OUT / 'b3_concurrency_original_round.csv',rows)
    return rows''')
            text = text.replace("'raw_concurrency_metrics_crosschecked': True", "'archived_concurrency_summary_loaded': True")
            text = text.replace('All values are recomputed from archived requests.jsonl/calls.jsonl and checked\nagainst normalized_metrics.csv.', 'Protocol and concurrency curves use the bundled retained observations.\nCross-model statistics are recomputed from all round-level measurements.')
        elif name == 'plot_d.py':
            text = text.replace("ROOT / 'results' / 'processed' / 'E1_storage_vs_calls.csv'", "DATA / 'd/E1_storage_vs_calls.csv'")
            text = text.replace("OUT / 'current_round_data.csv'", "DATA / 'd/current_round_data.csv'")
            text = text.replace("OUT / 'current_model_summary.csv'", "DATA / 'd/current_model_summary.csv'")
        elif name == 'plot_f.py':
            text = text.replace("ROOT / 'results' / 'processed' / 'G2_performance_ablation.csv'", "DATA / 'f/G2_performance_ablation.csv'")
        elif name == 'plot_cases.py':
            text = replace_function(text, 'read_cases', '''def read_cases():
    # Display extracts preserve receipt/settlement fields and recorded audit status.
    # The full private source archive is not bundled; no cryptographic re-audit is claimed.
    cases = json.loads((DATA / 'cases/case_figure_data.json').read_text(encoding='utf-8'))
    assert len(cases)==3
    for case in cases:
        receipt,settlement=case['receipt'],case['settlement']
        assert case['recorded_offline_audit_accepted']
        assert len(case['prompt_token_ids'])==case['input_tokens']
        assert receipt['usage']['n_pf']==case['input_tokens']
        assert receipt['usage']['n_out']==case['output_tokens']
        assert receipt['charge_microunits']==case['input_tokens']+2*case['output_tokens']
        assert settlement['bill_microunits']==receipt['charge_microunits']
        assert all(0<=t['position']<case['output_tokens'] for t in case['selected_output_tokens'])
    return cases''')
            text = text.replace('The generator checks source SHA-256 values, token IDs/counts, receipt',
                                'The generator checks bundled token counts, selected token positions, receipt')
        target = PAPER / 'scripts' / name
        put(target,text)
        SOURCES[target.relative_to(PAPER).as_posix()] = {
            'source': path.relative_to(ROOT).as_posix(), 'source_sha256': sha(path),
            'transformation': 'Portable input/output paths; plotting geometry retained; archive readers replaced by retained metric inputs where necessary'}

    # Keep final prose/table layouts as templates; generators replace the tables
    # they own and the runner checks regenerated numeric outputs against snapshots.
    put(PAPER / 'SOURCE_PROVENANCE.json', json.dumps(SOURCES,indent=2,ensure_ascii=False)+'\n')
    changed = [n for n,h in before.items() if sha(ROOT/n)!=h]
    if changed:
        raise RuntimeError('Original files changed: '+repr(changed))
    print(json.dumps({'paper_files':sum(p.is_file() for p in PAPER.rglob('*')),
                      'request_observations':len(observations),'original_files_unchanged':len(before)}))


if __name__=='__main__':
    main()
