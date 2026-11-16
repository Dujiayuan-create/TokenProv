"""Verify retained paper inputs or regenerate final figures/tables offline."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

PAPER = Path(__file__).resolve().parent
JOBS = {
    'b1': ['plot_b1.py'],
    'b2': ['plot_b2.py'],
    'b3_b4': ['plot_b3_b4.py'],
    'c': ['plot_c.py', 'write_c.py'],
    'd': ['plot_d.py'],
    'e': ['tables_e.py', 'compose_e.py'],
    'f': ['plot_f.py', 'tables_f.py'],
    'cases': ['plot_cases.py'],
}


def verify_inputs():
    manifest = json.loads((PAPER / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        path = (PAPER / name).resolve()
        if not path.is_relative_to(PAPER) or not path.is_file():
            raise ValueError('Missing or invalid bundled artifact: ' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Bundled artifact hash mismatch: ' + name)
    return len(manifest['files'])


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def same_value(left, right):
    if left == right:
        return True
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-8)
    except (TypeError, ValueError):
        return False


def verify_outputs(section, output):
    checks = []
    for expected in (PAPER / 'published' / section).glob('*.csv'):
        actual = output / section / expected.name
        if not actual.exists():
            continue  # Some snapshots are intermediate inputs, not generated exports.
        old, new = csv_rows(expected), csv_rows(actual)
        if len(old) != len(new):
            raise ValueError('CSV row count differs: ' + str(actual))
        for index, (a, b) in enumerate(zip(old, new)):
            if a.keys() != b.keys() or any(not same_value(a[k], b[k]) for k in a):
                raise ValueError(f'CSV differs from retained result: {actual.name}, row {index + 1}')
        checks.append(expected.name)
    required = {
        'b1': ['b1_five_model_latency_throughput.pdf', 'request_latency_data.csv'],
        'b2': ['b2_reference_cost_breakdown.pdf', 'b2_component_summary.csv', 'b2_appendix_component_table.tex'],
        'b3_b4': ['b3_b4_scalability_variability.pdf', 'b3_b4_appendix.tex', 'b4_model_range_summary.csv'],
        'c': ['c_deferred_proving_audit_rate.pdf', 'c_assurance_components_table.tex', 'c_complete.tex'],
        'd': ['d_evidence_settlement.pdf', 'd_current_plot_values.csv', 'd_appendix.tex'],
        'e': ['e_combined_results_table.tex', 'e_current_security_summary.csv', 'e_appendix_full.tex'],
        'f': ['f_component_waterfalls.pdf', 'f_main_rule_table.tex', 'f_cost_summary.csv'],
        'cases': ['token_provenance_case_boxes.pdf', 'case_figure_data.json'],
    }[section]
    for name in required:
        if not (output / section / name).is_file():
            raise ValueError('Expected output was not produced: ' + name)
    if section != 'cases' and not checks:
        raise ValueError('No regenerated numeric output was compared for ' + section)
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--section', choices=['all', *JOBS], default='all')
    parser.add_argument('--verify-only', action='store_true', help='Check bundled SHA-256 hashes; no plotting dependencies needed')
    parser.add_argument('--output', type=Path, default=PAPER / 'generated')
    parser.add_argument('--font-dir', type=Path, help='Directory containing legally installed Times New Roman; cases also need Microsoft YaHei')
    args = parser.parse_args(argv)
    checked = verify_inputs()
    if args.verify_only:
        print(json.dumps({'bundled_files_verified': checked, 'experiments_executed': False}))
        return 0
    output = args.output.resolve()
    for protected in (PAPER / 'data', PAPER / 'published', PAPER / 'scripts'):
        if output == PAPER or output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError('Choose an output directory separate from bundled inputs and scripts')
    env = os.environ.copy()
    env['TOKENPROV_PAPER_OUTPUT'] = str(output)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    if args.font_dir:
        if not args.font_dir.is_dir():
            raise ValueError('Font directory does not exist')
        env['TOKENPROV_PAPER_FONT_DIR'] = str(args.font_dir.resolve())
    sections = list(JOBS) if args.section == 'all' else [args.section]
    result = {'bundled_files_verified': checked, 'experiments_executed': False, 'sections': {}}
    for section in sections:
        destination = output / section
        destination.mkdir(parents=True, exist_ok=True)
        # Static manuscript prose and final layout templates are retained verbatim.
        # Numeric CSVs and plots must be regenerated, not copied from snapshots.
        for path in (PAPER / 'published' / section).glob('*.tex'):
            shutil.copyfile(path, destination / path.name)
        for script in JOBS[section]:
            print(f'Regenerating {section}: {script}', flush=True)
            subprocess.run([sys.executable, '-B', str(PAPER / 'scripts' / script)], env=env, check=True)
        result['sections'][section] = {'compared_csv_files': verify_outputs(section, output)}
    (output / 'reproduction_verification.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
