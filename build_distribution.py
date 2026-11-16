"""Build the curated release in a disposable directory, without changing old environments."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

BASE = Path(__file__).resolve().parent
RELEASE = BASE / 'TokenProv'
CHECK = BASE / '.validation'
DEPS = CHECK / 'build_dependencies'
DIST = BASE / 'dist'


def extract_zip(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for entry in archive.infolist():
        if not (destination / entry.filename).resolve().is_relative_to(destination.resolve()):
            raise ValueError('Archive member escaped destination')
    archive.extractall(destination)


def main():
    sys.dont_write_bytecode = True
    DEPS.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(exist_ok=True)
    wheels = []
    previous = CHECK / 'python-build-validation.json'
    cached = {item['file']: item['sha256'] for item in json.loads(previous.read_text(encoding='utf-8'))['build_dependencies']} if previous.is_file() else {}
    for package, version in [('setuptools', '75.8.2'), ('wheel', '0.45.1'), ('packaging', '24.2')]:
        known = next((name for name in cached if name.startswith(f'{package}-{version}-')), None)
        if known and (CHECK / known).is_file():
            entry = {'filename': known, 'digests': {'sha256': cached[known]}}
        else:
            metadata = json.load(urllib.request.urlopen(f'https://pypi.org/pypi/{package}/{version}/json', timeout=45))
            candidates = [entry for entry in metadata['urls'] if entry['filename'].endswith('none-any.whl')]
            if len(candidates) != 1:
                raise ValueError('Expected one platform-independent build wheel')
            entry = candidates[0]
        target = CHECK / entry['filename']
        if not target.exists():
            with urllib.request.urlopen(entry['url'], timeout=45) as response:
                target.write_bytes(response.read())
        if hashlib.sha256(target.read_bytes()).hexdigest() != entry['digests']['sha256']:
            raise ValueError('Build dependency SHA-256 mismatch')
        with zipfile.ZipFile(target) as archive:
            extract_zip(archive, DEPS)
        wheels.append({'file': target.name, 'sha256': entry['digests']['sha256']})
    sys.path.insert(0, str(DEPS))
    os.chdir(RELEASE)
    from setuptools import build_meta
    with (CHECK / 'python-build.log').open('w', encoding='utf-8') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            wheel = DIST / build_meta.build_wheel(str(DIST))
            source = DIST / build_meta.build_sdist(str(DIST))
    install = CHECK / 'installed_wheel'
    with zipfile.ZipFile(wheel) as archive:
        entries = archive.namelist()
        extract_zip(archive, install)
    assert 'tokenprov/server_eval/__main__.py' in entries
    assert any(entry.endswith('/LICENSE') for entry in entries)
    with tarfile.open(source) as archive:
        source_files = archive.getnames()
    for suffix in ['README.md', 'LICENSE', 'configs/smoke.yaml', 'scripts/serve_transformers.py',
            'experiments/README.md', 'tools/zk/setup.py', 'tools/zk/circuits/tokenprov_accounting.circom',
            'paper/README.md', 'paper/reproduce.py', 'paper/manifest.json',
            'paper/data/b1/request_latency_data.csv', 'paper/published/c/c_deferred_proving_audit_rate.pdf',
            'paper/published/e/e_combined_results_table.tex', 'experiments/paper_protocol.py',
            'experiments/protocol_drivers/components.py']:
        assert any(name.endswith('/' + suffix) for name in source_files), suffix
    assert not any('/node_modules/' in name or '/results/' in name or '/artifacts/' in name for name in source_files)
    assert not any('/paper/generated/' in name for name in source_files)
    command = ('import sys; from pathlib import Path; '
        'sys.path.insert(0,sys.argv[1]); import tokenprov; '
        'assert Path(tokenprov.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()); '
        'assert tokenprov.__version__ == "0.2.0"; '
        'from tokenprov.server_eval.__main__ import main; '
        'raise SystemExit(main(["smoke","--config",sys.argv[2]]))')
    result = subprocess.run([sys.executable, '-B', '-c', command, str(install),
        str(RELEASE / 'configs/smoke.yaml')], cwd=CHECK, text=True, capture_output=True, timeout=60)
    (CHECK / 'installed-wheel-smoke.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Installed-wheel smoke failed: ' + result.stderr)
    data = {'build_dependencies': wheels, 'wheel': wheel.name, 'sdist': source.name,
        'installed_wheel_smoke_passed': True, 'wheel_files': len(entries), 'sdist_files': len(source_files)}
    (CHECK / 'python-build-validation.json').write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(data))


if __name__ == '__main__':
    main()
