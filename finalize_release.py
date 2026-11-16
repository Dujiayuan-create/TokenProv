"""Verify original preservation and package only the clean, standalone source tree."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile

BASE = Path(__file__).resolve().parent
ORIGINAL = BASE.parent
RELEASE = BASE / 'TokenProv'
CHECK = BASE / '.validation'
DIST = BASE / 'dist'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    # Use this addition task's baseline. The older snapshot is retained separately;
    # intervening user-authorized figure edits must not be mislabeled as this task.
    baseline = CHECK / 'paper_original_snapshot.json'
    snapshot = json.loads(baseline.read_text(encoding='utf-8'))
    changed = [name for name, digest in snapshot.items()
        if not (ORIGINAL / name).is_file() or sha256(ORIGINAL / name) != digest]
    if changed:
        raise ValueError('Original source preservation check failed: ' + repr(changed))

    generated = Path(tempfile.mkdtemp(prefix='worktree_generated_', dir=CHECK))
    moved = []
    def move_local(path):
        source = path.resolve()
        destination = (generated / path.relative_to(RELEASE)).resolve()
        if not source.is_relative_to(RELEASE.resolve()) or not destination.is_relative_to(CHECK.resolve()):
            raise ValueError('Generated-file move escaped checked release/validation directories')
        if path.is_symlink() or destination.exists():
            raise ValueError('Refusing symlink or existing destination during generated-file move')
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
        moved.append(path.relative_to(RELEASE).as_posix())
    for name in ['results', 'build', 'tokenprov.egg-info', '.pytest_cache', 'paper/generated']:
        path = RELEASE / name
        if path.exists():
            move_local(path)
    for path in sorted(RELEASE.rglob('__pycache__'), key=lambda value: len(value.parts), reverse=True):
        if path.is_dir():
            move_local(path)

    files = sorted(path for path in RELEASE.rglob('*') if path.is_file())
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(RELEASE.resolve()):
            raise ValueError('Invalid source path')
        relative = path.relative_to(RELEASE)
        if any(part in {'node_modules', '__pycache__', 'results', 'artifacts', 'models', 'build', '.git'} for part in relative.parts):
            raise ValueError('Generated directory in source release: ' + str(relative))
        if path.suffix in {'.pem', '.key', '.pfx', '.p12', '.safetensors', '.zkey', '.wtns', '.ptau', '.whl', '.exe', '.pyc'}:
            raise ValueError('Excluded binary/key artifact in source release: ' + str(relative))

    broken = []
    for path in RELEASE.rglob('*.md'):
        text = path.read_text(encoding='utf-8')
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
            target = target.strip('<>').split('#', 1)[0]
            if not target or '://' in target or target.startswith('mailto:'):
                continue
            if not (path.parent / target).resolve().exists():
                broken.append([path.relative_to(RELEASE).as_posix(), target])
    if broken:
        raise ValueError('Broken local document links: ' + repr(broken))

    forbidden = re.compile(r'192\.168\.89\.37|liuzijun|jyduTest|/home/ANTdd|/workspace/data|plot_runtime|plot_dependencies|-----BEGIN .*PRIVATE KEY')
    identifiers = []
    for path in files:
        if path.suffix in {'.py', '.md', '.json', '.jsonl', '.csv', '.tex', '.yaml', '.yml', '.toml', '.mjs', '.circom', '.in'}:
            if forbidden.search(path.read_text(encoding='utf-8-sig')):
                identifiers.append(path.relative_to(RELEASE).as_posix())
    if identifiers:
        raise ValueError('Machine-specific identifiers in public source: ' + repr(identifiers))

    DIST.mkdir(exist_ok=True)
    archive_path = DIST / 'TokenProv-0.2.0-source.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, 'TokenProv/' + path.relative_to(RELEASE).as_posix())
    digest = sha256(archive_path)
    archive_path.with_suffix('.zip.sha256').write_text(digest + '  ' + archive_path.name + '\n', encoding='ascii')
    manifest = {path.relative_to(RELEASE).as_posix(): sha256(path) for path in files}
    (DIST / 'TokenProv-0.2.0-source.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    report = {'original_source_files_checked': len(snapshot), 'original_sources_unchanged': True,
        'source_files': len(files), 'source_bytes': sum(path.stat().st_size for path in files),
        'archive': archive_path.name, 'archive_bytes': archive_path.stat().st_size, 'archive_sha256': digest,
        'license': 'MIT', 'broken_document_links': broken, 'machine_specific_identifiers': identifiers,
        'generated_outputs_moved_to_validation': moved,
        'cpu_regression_tests_passed': json.loads((CHECK / 'paper-validation.json').read_text(encoding='utf-8'))['cpu_regression_tests_passed'],
        'paper_artifacts': json.loads((CHECK / 'paper-validation.json').read_text(encoding='utf-8')),
        'python_distribution': json.loads((CHECK / 'python-build-validation.json').read_text(encoding='utf-8')),
        'javascript': json.loads((CHECK / 'javascript-validation.json').read_text(encoding='utf-8')),
        'new_gpu_or_tee_experiments_run': False}
    (BASE / 'VALIDATION_REPORT.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ['original_source_files_checked', 'original_sources_unchanged',
        'source_files', 'source_bytes', 'archive', 'archive_bytes', 'archive_sha256',
        'cpu_regression_tests_passed', 'broken_document_links', 'machine_specific_identifiers']}))


if __name__ == '__main__':
    main()
