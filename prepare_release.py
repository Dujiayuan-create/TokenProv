"""Create a separate source-only release; never rewrite the original tree."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'opensource' / 'TokenProv'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise SystemExit('Release directory already exists; refusing to overwrite it.')
    sources = set()
    for name in ('tokenprov', 'tests', 'docs', 'configs', 'scripts', 'experiments', 'tools/zk'):
        for path in (ROOT / name).rglob('*'):
            if (path.is_file() and not path.is_symlink()
                    and not any(part in {'__pycache__', 'node_modules', 'build', 'bin', 'obj'} for part in path.parts)
                    and path.suffix in {'.py', '.md', '.mjs', '.circom', '.json', '.yaml', '.yml', '.ps1', '.sh', '.toml', '.txt', '.c', '.h', '.cpp', '.def'}):
                sources.add(path)
    sources.update(path for path in ROOT.iterdir() if path.is_file())
    snapshot = {str(path.relative_to(ROOT)).replace('\\', '/'): digest(path) for path in sorted(sources)}
    (ROOT / 'opensource' / 'ORIGINAL_SOURCE_SNAPSHOT.json').write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    selected = []
    for name in ('tokenprov', 'tests'):
        selected.extend((path, path.relative_to(ROOT)) for path in (ROOT / name).rglob('*.py')
            if '__pycache__' not in path.parts)
    for name in ('pyproject.toml', 'configs/server-eval.yaml', 'configs/tee/runtime.yaml',
            'configs/tee/policy.yaml', 'configs/tee/nvidia.rego',
            'configs/workloads/example_traces.jsonl', 'tools/zk/proof_worker.mjs',
            'tools/zk/package.json', 'tools/zk/package-lock.json',
            'tools/zk/circuits/tokenprov_accounting.circom',
            'experiments/run_c_assurance_server.py', 'experiments/report_c_assurance.py'):
        selected.append((ROOT / name, Path(name)))
    selected.extend((ROOT / old, Path(new)) for old, new in [
        ('experiments/c_assurance_worker.mjs', 'tools/zk/c_assurance_worker.mjs'),
        ('experiments/tee_vbs_component/vbs_receipt_signing.py', 'experiments/tee_signing/benchmark.py'),
        ('experiments/tee_vbs_component/report_vbs_signing.py', 'experiments/tee_signing/report.py'),
        ('artifacts/server_20261113/reproduction_supplement/brand_extension_qwen14_repeats_20261113/experiment_hf_server.py',
         'scripts/serve_transformers.py')])
    for source, relative in selected:
        if source.is_symlink():
            raise ValueError('Symlink source rejected')
        destination = OUT / relative
        if not destination.resolve().is_relative_to(OUT.resolve()):
            raise ValueError('Output escaped release directory')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    print(json.dumps({'release': str(OUT), 'copied_files': len(selected),
        'original_sources_snapshotted': len(snapshot)}, ensure_ascii=True))


if __name__ == '__main__':
    main()
