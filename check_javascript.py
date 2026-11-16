"""Check worker syntax using a verified, isolated Node binary; install nothing globally."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import zipfile

BASE = Path(__file__).resolve().parent
CHECK = BASE / '.validation'
NAME = 'node-v22.14.0-win-x64.zip'
URL = 'https://nodejs.org/dist/v22.14.0/'


def main():
    CHECK.mkdir(exist_ok=True)
    with urllib.request.urlopen(URL + 'SHASUMS256.txt', timeout=45) as response:
        sums = response.read().decode('ascii').splitlines()
    expected = next(line.split()[0] for line in sums if line.split()[-1] == NAME)
    archive = CHECK / NAME
    if not archive.exists():
        with urllib.request.urlopen(URL + NAME, timeout=45) as response:
            with archive.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('Official Node SHA-256 check failed')
    with zipfile.ZipFile(archive) as entries:
        for entry in entries.infolist():
            if not (CHECK / entry.filename).resolve().is_relative_to(CHECK.resolve()):
                raise ValueError('Node archive member escaped validation directory')
        entries.extractall(CHECK)
    node = CHECK / 'node-v22.14.0-win-x64/node.exe'
    scripts = sorted((BASE / 'TokenProv/tools/zk').glob('*.mjs'))
    for script in scripts:
        subprocess.run([str(node), '--check', str(script)], check=True, capture_output=True, text=True, timeout=30)
    result = {'node': '22.14.0', 'node_archive_sha256': expected,
        'syntax_checked': [str(script.relative_to(BASE / 'TokenProv')).replace('\\', '/') for script in scripts]}
    (CHECK / 'javascript-validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
