"""Validate a scoped C1--C3 replay run and export plan-driven statistics."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics


def require(condition, message):
    if not condition:
        raise ValueError(message)


def expected_counts(plan):
    rounds = plan['rounds']
    require(rounds > 0 and len(plan['seeds']) == rounds, 'invalid round plan')
    require(plan['c3_requests'] == 100 and plan['calls_per_source_request'] == 8,
            'unsupported cohort layout')
    return {
        'C1-warm': rounds * plan['c1_warm_calls_per_round'],
        'C1-legacy-command': rounds * plan['c1_legacy_commands_per_round'],
        'C2-call': rounds * 2 * plan['c2_request_groups_per_configuration'] * sum(plan['c2_calls']),
        'C2-request': rounds * 2 * plan['c2_request_groups_per_configuration'] * len(plan['c2_calls']),
        'C3-job': rounds * 8 * sum(plan['c3_rates_percent']),
        'warmup': rounds * plan['proof_workers'] * plan['warmup_proofs_per_worker'],
        'negative-control': rounds * 4,
    }


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def numeric(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def summarize(rows, keys):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    result = []
    for values, members in groups.items():
        item = dict(zip(keys, values))
        item['rounds'] = len(members)
        for field in members[0]:
            if field in keys or field in ('round', 'seed'):
                continue
            samples = [row[field] for row in members]
            if all(type(value) in (int, float) for value in samples):
                item[field] = {'mean': statistics.fmean(samples),
                               'sample_sd': statistics.stdev(samples) if len(samples) > 1 else 0}
        result.append(item)
    return result


def validate(path):
    path = Path(path).resolve()
    plan = json.loads((path / 'plan.json').read_text(encoding='utf-8-sig'))
    verification = json.loads((path / 'verification.json').read_text(encoding='utf-8-sig'))
    progress = json.loads((path / 'progress.json').read_text(encoding='utf-8-sig'))
    require(progress['state'] == 'complete', 'run is incomplete')
    require(verification['source_files_unchanged'] and verification['all_configurations_expected_rounds'],
            'run verification failed')
    require(verification['failed_proofs'] == 0, 'run contains proof failures')
    expected = expected_counts(plan)
    require(verification['negative_controls'] == verification['negative_controls_rejected'] == expected['negative-control'],
            'negative-control count mismatch')
    for entry in json.loads((path / 'output_manifest.json').read_text(encoding='utf-8-sig')):
        candidate = (path / entry['file']).resolve()
        require(candidate.is_relative_to(path), 'manifest path escaped run directory')
        require(candidate.stat().st_size == entry['bytes'], 'manifest size mismatch: ' + entry['file'])
        require(hashlib.sha256(candidate.read_bytes()).hexdigest() == entry['sha256'],
                'manifest hash mismatch: ' + entry['file'])
    measurements = read_jsonl(path / 'measurements.jsonl')
    counts = Counter(row['stage'] for row in measurements)
    require(all(counts[stage] == count for stage, count in expected.items()), 'raw stage counts differ from plan')
    require(set(counts).issubset(expected), 'unknown measurement stage')
    require(all(row['rejected'] for row in measurements if row['stage'] == 'negative-control'), 'negative control accepted')
    require(all(row['verified'] for row in measurements if row['stage'] in ('C1-warm', 'C3-job', 'warmup')),
            'honest proof rejected')
    artifacts = read_jsonl(path / 'proofs/artifacts.jsonl')
    proof_stages = ('C1-warm', 'C1-legacy-command', 'C2-call', 'C3-job', 'warmup')
    require(Counter(row['stage'] for row in artifacts) == Counter({k: expected[k] for k in proof_stages if expected[k]}),
            'saved proof artifact counts differ from plan')
    with (path / 'round_metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = [{key: numeric(value) for key, value in row.items() if value != ''}
                for row in csv.DictReader(stream)]
    configurations = 1 + bool(plan['c1_legacy_commands_per_round']) + 2 * len(plan['c2_calls']) + len(plan['c3_rates_percent'])
    require(len(rows) == plan['rounds'] * configurations, 'aggregate row count mismatch')
    for row in rows:
        if row['experiment'] == 'C3':
            require(row['proof_count'] == 8 * row['challenged_requests'], 'C3 proof count mismatch')
            require(row['requests'] == 100 and row['challenged_requests'] == row['audit_rate_percent'], 'C3 sample mismatch')
            require(row['online_proving_ms'] == 0, 'C3 online proving must be zero')
            require(abs(row['amortized_full_prove_ms_per_request'] - row['sum_full_prove_ms'] / 100) < 1e-6,
                    'C3 amortization mismatch')
    return plan, rows, expected


def main(path, output=None):
    path = Path(path).resolve()
    plan, rows, counts = validate(path)
    summaries = {}
    for experiment, keys in [('C1', ['mode']), ('C2', ['calls', 'mode']), ('C3', ['audit_rate_percent'])]:
        summaries[experiment] = summarize([row for row in rows if row['experiment'] == experiment], keys)
        require(all(row['rounds'] == plan['rounds'] for row in summaries[experiment]), 'missing aggregate round')
    derived = Path(output).resolve() if output else path / 'paper_exports'
    derived.mkdir(parents=True, exist_ok=False)
    results = {'formal': plan['formal'], 'rounds': plan['rounds'], 'seeds': plan['seeds'],
               'measured_proofs_by_experiment': {'C1': counts['C1-warm'] + counts['C1-legacy-command'],
                                               'C2': counts['C2-call'], 'C3': counts['C3-job']},
               'warmup_proofs': counts['warmup'], 'additional_honest_control_proofs': plan['rounds'],
               'negative_controls': counts['negative-control'], 'summaries': summaries,
               'scope': plan['scope'], 'hardware': plan['hardware'], 'software': plan['software'],
               'validation_scope': 'Saved-file hashes, counts and aggregate consistency; no new cryptographic proof verification'}
    (derived / 'key_results.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    for experiment, groups in summaries.items():
        table = [r'\begin{tabular}{llrr}', r'\toprule', r'Configuration & Metric & Mean & Sample SD \\', r'\midrule']
        keys = {'C1': ['mode'], 'C2': ['calls', 'mode'], 'C3': ['audit_rate_percent']}[experiment]
        for row in groups:
            label = ', '.join(str(row[key]) for key in keys).replace('_', r'\_')
            for field, value in row.items():
                if isinstance(value, dict):
                    field = field.replace('_', r'\_')
                    table.append(f"{label} & {field} & {value['mean']:.6g} & {value['sample_sd']:.6g} " + r'\\')
        table += [r'\bottomrule', r'\end{tabular}']
        (derived / f'{experiment.lower()}_summary.tex').write_text('\n'.join(table) + '\n', encoding='utf-8')
    (derived / 'METHODOLOGY.md').write_text(
        '# Scoped assurance replay\n\n'
        f"Run type: {'formal' if plan['formal'] else 'small smoke run with actual proofs'}. "
        f"{plan['rounds']} independently seeded rounds; seeds {plan['seeds']}. "
        f"Source cohort: {plan['source_requests']} requests, {plan['calls_per_source_request']} calls per request.\n\n"
        f"Recorded hardware: {json.dumps(plan['hardware'], ensure_ascii=False)}.\n"
        f"Recorded software: {json.dumps(plan['software'])}.\n\n"
        'Means and sample SD are across round means; one round has zero sample SD. '
        'The scoped circuit checks Poseidon accounting commitments, integer token charges and a request-hash equality. '
        'Receipt-bound SHA-256 witness commitments and shared call fields are checked on the host. '
        'These CPU proof measurements include no model execution or inference attestation.\n\n'
        'C1 separates persistent warm workers from process-per-call command costs. '
        'C2 revalidates inputs and fsyncs retained witnesses: Immediate proves before the foreground timer ends; '
        'Deferred proves and verifies afterward. These replay phases are not live LLM latency measurements.\n\n'
        'C3 persists 100 requests before selecting challenges. Each selected request contributes eight actual proofs; '
        'each rate executes independently. Summed job wall durations measure work, not CPU time; '
        'audit makespan includes result I/O. The preparation baseline uses one cohort-level fsync per round.\n\n'
        'This report checks saved-file hashes and structural consistency; it does not independently rerun Groth16 verification.\n',
        encoding='utf-8')
    print(json.dumps({'validated': True, 'rounds': plan['rounds'], 'output': str(derived)}))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path, help='new report directory; default RUN/paper_exports')
    args = parser.parse_args()
    main(args.run, args.output)

