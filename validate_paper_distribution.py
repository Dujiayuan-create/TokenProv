"""Validate the completed source distribution, retained data, and original files."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

BASE=Path(__file__).resolve().parent
CHECK=BASE/'.validation'
ORIGINAL=BASE.parent
PUBLIC=BASE/'TokenProv'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    snapshot=json.loads((CHECK/'paper_original_snapshot.json').read_text(encoding='utf-8'))
    assert all(sha(ORIGINAL/name)==value for name,value in snapshot.items())
    provenance=json.loads((PUBLIC/'paper/SOURCE_PROVENANCE.json').read_text(encoding='utf-8'))
    source_checks=0
    for item in provenance.values():
        if 'source' in item:
            assert sha(ORIGINAL/item['source'])==item['source_sha256']
            source_checks+=1
        for name,value in item.get('source_files_sha256',{}).items():
            assert sha(ORIGINAL/name)==value
            source_checks+=1
    folder=Path(tempfile.mkdtemp(prefix='sdist_',dir=CHECK)).resolve()
    assert folder.is_relative_to(CHECK.resolve())
    with tarfile.open(BASE/'dist/tokenprov-0.2.0.tar.gz') as archive:
        for entry in archive.getmembers():
            assert (folder/entry.name).resolve().is_relative_to(folder)
            assert not entry.issym() and not entry.islnk()
        archive.extractall(folder,filter='data')
    source=folder/'tokenprov-0.2.0'
    command=('import sys;from pathlib import Path;'
        'root=Path(sys.argv[1]);sys.path.insert(0,str(root));import tokenprov;'
        'assert Path(tokenprov.__file__).resolve().is_relative_to(root);'
        'import pytest;raise SystemExit(pytest.main([str(root/"tests"),"-p","no:cacheprovider"]))')
    env=os.environ.copy()
    env['PYTHONDONTWRITEBYTECODE']='1'
    result=subprocess.run([sys.executable,'-B','-c',command,str(source)],cwd=CHECK,
                          env=env,capture_output=True,text=True,timeout=180)
    (CHECK/'sdist-tests.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Tests from unpacked source distribution failed: '+result.stdout+result.stderr)
    match=re.search(r'(\d+) passed',result.stdout)
    if not match:raise RuntimeError('No passing-test count in pytest output')
    report=json.loads((CHECK/'paper-validation.json').read_text(encoding='utf-8'))
    assert all(report['key_tex_identical'].values())
    assert all(v['png_pixels_equal'] for v in report['png_pixel_comparison'].values())
    assert report['regenerated_csv_count']==25
    report.update(cpu_regression_tests_passed=int(match[1]),
                  tests_executed_from_unpacked_sdist=True,
                  original_source_files_checked=len(snapshot),
                  retained_original_inputs_checked=source_checks,
                  original_sources_unchanged=True)
    (CHECK/'paper-validation.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed_tests':int(match[1]),'original_source_files_checked':len(snapshot),
        'retained_original_inputs_checked':source_checks,'csv_comparisons':25,
        'pixel_identical_figures':len(report['png_pixel_comparison'])}))


if __name__=='__main__':main()
