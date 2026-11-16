"""Verify saved CNG signatures and recompute a receipt-signing component report."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

DOMAIN = b'TOKENPROV\x00vbs-receipt-authentication-component/v1\x00'
MODES = ('software_ksp', 'require_vbs')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def describe(values):
    return {'n': len(values), 'mean': statistics.fmean(values),
            'sample_sd': statistics.stdev(values) if len(values) > 1 else None}


def verify_example(path):
    """Verify the saved CNG ECCPUBLICBLOB/raw P-256 signature on any platform."""
    example = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    digest = hashlib.sha256(DOMAIN + canonical(example['envelope'])).digest()
    require(digest.hex() == example['digest_hex'], 'saved receipt digest mismatch')
    public = bytes.fromhex(example['public_key_hex'])
    signature = bytes.fromhex(example['signature_hex'])
    require(len(public) == 72 and struct.unpack('<4sI', public[:8]) == (b'ECS1', 32),
            'unsupported CNG public-key format')
    require(len(signature) == 64, 'unsupported CNG signature format')
    key = ec.EllipticCurvePublicNumbers(int.from_bytes(public[8:40], 'big'),
                                       int.from_bytes(public[40:72], 'big'), ec.SECP256R1()).public_key()
    encoded = utils.encode_dss_signature(int.from_bytes(signature[:32], 'big'), int.from_bytes(signature[32:], 'big'))
    key.verify(encoded, digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    return {'file': Path(path).name, 'verified': True, 'vbs_isolated': example['vbs_isolated'],
            'request_id': example['envelope']['receipt']['request_id'],
            'call_id': example['envelope']['receipt']['call_id']}


def validate(run):
    run = Path(run).resolve()
    summary = json.loads((run / 'summary.json').read_text(encoding='utf-8-sig'))
    raw = [json.loads(line) for line in (run / 'measurements.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    configurations = [row for row in raw if row['type'] == 'configuration']
    require(len(configurations) == 1, 'configuration count mismatch')
    config = configurations[0]
    rounds, receipts = config['rounds'], config['receipts_per_session']
    require(rounds > 0 and receipts > 0, 'invalid run dimensions')
    require(summary['status'] == 'completed' and summary['correctness_failures'] == 0, 'benchmark did not complete')
    require(len([row for row in raw if row['type'] == 'completion' and row['success']]) == 1, 'missing successful completion')
    require(not any(row['type'] == 'fatal_error' for row in raw), 'raw records contain fatal errors')
    tests = [row for row in raw if row['type'] == 'correctness']
    require(len(tests) == 20 * rounds and all(row['passed'] for row in tests), 'correctness checks failed or missing')
    measured = [row for row in raw if row['type'] == 'operation' and row['phase'] == 'measured']
    require(len(measured) == 2 * rounds * receipts and all(row['passed'] and row['verify_status'] == '0x00000000' for row in measured),
            'measured signing count or status mismatch')
    sessions = [row for row in raw if row['type'] == 'session']
    require(len(sessions) == 2 * rounds, 'session count mismatch')
    fixtures = json.loads((run / 'replayed_receipts.json').read_text(encoding='utf-8-sig'))
    require(len(fixtures) == receipts and hashlib.sha256(canonical(fixtures)).hexdigest() == config['cohort_sha256'],
            'receipt cohort mismatch')
    fields = ('sign_api_ns', 'receipt_authentication_ns', 'external_verify_ns')
    round_stats = []
    for mode in MODES:
        for round_number in range(1, rounds + 1):
            session_group = [row for row in sessions if row['mode'] == mode and row['round'] == round_number]
            require(len(session_group) == 1, 'duplicate or missing session')
            session = session_group[0]
            require(session['properties']['Virtual Iso']['dword'] == (1 if mode == 'require_vbs' else 0),
                    'reported isolation property mismatch')
            require(session['properties']['Export Policy']['dword'] == 0, 'reported export policy mismatch')
            group = [row for row in measured if row['mode'] == mode and row['round'] == round_number]
            require(len(group) == receipts and {row['index'] for row in group} == set(range(receipts)),
                    'session receipt indices mismatch')
            cases = [row for row in tests if row['mode'] == mode and row['round'] == round_number]
            names = Counter(row['case'] for row in cases)
            require(len(names) == 10 and all(value == 1 for value in names.values()), 'missing or duplicate correctness case')
            require(names['private_export_denied'] == 1 and names['scope_control_host_modified_charge_can_be_freshly_signed'] == 1,
                    'required scope/export control missing')
            require(sum('rejected' in name for name in names) == 8, 'mutation control count mismatch')
            saved = [row for row in summary['round_statistics'] if row['mode'] == mode and row['round'] == round_number]
            require(len(saved) == 1, 'saved session statistics missing')
            item = {'mode': mode, 'round': round_number}
            for field in fields:
                values = [row[field] / 1e6 for row in group]
                require(all(value >= 0 for value in values), 'negative timing')
                item[field + '_ms'] = statistics.fmean(values)
                require(abs(item[field + '_ms'] - saved[0][field + '_ms']['mean']) < 1e-12,
                        'saved session statistics differ from raw records')
            item['key_setup_ms'] = session['key_setup_ns'] / 1e6
            round_stats.append(item)
    examples = [verify_example(path) for path in sorted(run.glob('example-*.json'))]
    require(len(examples) == 2 * rounds, 'saved example count mismatch')
    for mode in MODES:
        for round_number in range(1, rounds + 1):
            example_path = run / f'example-{mode}-round{round_number}.json'
            example = json.loads(example_path.read_text(encoding='utf-8-sig'))
            session = next(row for row in sessions if row['mode'] == mode and row['round'] == round_number)
            require(example['public_key_hex'] == session['public_key_hex'], 'example key differs from session key')
            require(example['vbs_isolated'] == (mode == 'require_vbs'), 'example isolation label mismatch')
    return config, tests, measured, round_stats, examples


def main(run, output=None, plots=False):
    run = Path(run).resolve()
    config, tests, measured, round_stats, examples = validate(run)
    metrics = [('Signing API', 'sign_api_ns_ms', 'ms/receipt'),
               ('Receipt authentication', 'receipt_authentication_ns_ms', 'ms/receipt'),
               ('External verification', 'external_verify_ns_ms', 'ms/receipt'),
               ('Session key setup', 'key_setup_ms', 'ms/session')]
    report = []
    for name, field, unit in metrics:
        values = {mode: [row[field] for row in round_stats if row['mode'] == mode] for mode in MODES}
        report.append({'metric': name, 'unit': unit, 'software': describe(values['software_ksp']),
                       'vbs': describe(values['require_vbs']),
                       'paired_increment': describe([vbs - software for software, vbs in zip(values['software_ksp'], values['require_vbs'])])})
    output = Path(output).resolve() if output else run / 'report'
    output.mkdir(parents=True, exist_ok=False)
    validation = {'passed': True, 'rounds': config['rounds'], 'receipts_per_session': config['receipts_per_session'],
                  'measured_operations': len(measured), 'raw_statistics_recomputed': True,
                  'saved_receipt_examples_verified': examples,
                  'tampered_receipt_or_signature_rejections': sum('rejected' in row['case'] for row in tests),
                  'private_export_denials': sum(row['case'] == 'private_export_denied' for row in tests),
                  'accepted_scope_controls': sum(row['case'].startswith('scope_control_') for row in tests),
                  'metrics': report, 'raw_jsonl_sha256': hashlib.sha256((run / 'measurements.jsonl').read_bytes()).hexdigest(),
                  'scope': 'Component key isolation; accounting and receipt preparation remain host-side; no inference or remote attestation'}
    (output / 'verification.json').write_text(json.dumps(validation, indent=2) + '\n', encoding='utf-8')
    table = [r'\begin{tabular}{lrr}', r'\toprule', r'Metric & Software KSP & VBS-isolated \\', r'\midrule']
    for row in report:
        values = [f"{row[mode]['mean']:.3f} $\\pm$ {(row[mode]['sample_sd'] or 0):.3f}" for mode in ('software', 'vbs')]
        table.append(f"{row['metric']} ({row['unit']}) & " + ' & '.join(values) + r' \\')
    table += [r'\bottomrule', r'\end{tabular}']
    (output / 'receipt_signing_table.tex').write_text('\n'.join(table) + '\n', encoding='utf-8')
    (output / 'METHODOLOGY.md').write_text(
        '# VBS-isolated receipt signing\n\n'
        f"{config['rounds']} independently keyed sessions per mode; {config['receipts_per_session']} source receipts "
        f"and {config['warmups_per_session']} warmups per session. First operations are recorded separately. "
        'The report recomputes session means and sample SD across those means. One round has no estimable sample SD.\n\n'
        'ECDSA-P256/SHA256 signatures use the same Windows CNG API in both modes. '
        'The protected mode requires VBS key isolation and rejects software fallback; keys are ephemeral and nonexportable. '
        'Saved public examples are independently verified with cryptography on any platform. '
        'The raw log records verification status for all measured operations; only the saved examples can be reverified from this report.\n\n'
        'Changed-message checks reject stale signatures. Accepted scope controls demonstrate that host-modified accounting '
        'can be freshly signed. Accounting, token origin and receipt preparation remain outside the isolated key operation. '
        'Reported local key properties are not remote attestation; this experiment does not measure protected inference.\n',
        encoding='utf-8')
    if plots:
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib import pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.1))
        for ax, row in zip(axes, report[:2]):
            means = [row[mode]['mean'] for mode in ('software', 'vbs')]
            deviations = [row[mode]['sample_sd'] or 0 for mode in ('software', 'vbs')]
            ax.bar([0, 1], means, yerr=deviations, capsize=4, color=['#245783', '#B12F39'])
            for index, mode in enumerate(MODES):
                field = 'sign_api_ns_ms' if row['metric'] == 'Signing API' else 'receipt_authentication_ns_ms'
                samples = [item[field] for item in round_stats if item['mode'] == mode]
                offsets = [0] if len(samples) == 1 else [0.16 * (i / (len(samples) - 1) - .5) for i in range(len(samples))]
                ax.scatter([index + shift for shift in offsets], samples, color='black', s=18)
            ax.set_xticks([0, 1], ['Software KSP', 'VBS-isolated'])
            ax.set_ylabel('Time (ms/receipt)')
            ax.set_title(row['metric'])
            ax.set_ylim(bottom=0)
        fig.tight_layout()
        for extension in ('pdf', 'svg', 'png'):
            fig.savefig(output / ('receipt_signing.' + extension), dpi=240)
        plt.close(fig)
    print(json.dumps({'validated': True, 'operations': len(measured), 'verified_saved_examples': len(examples), 'output': str(output)}))
    return validation


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path, help='new report directory; default RUN/report')
    parser.add_argument('--plots', action='store_true', help='also generate PDF/SVG/PNG plots; requires matplotlib')
    args = parser.parse_args()
    main(args.run, args.output, args.plots)

