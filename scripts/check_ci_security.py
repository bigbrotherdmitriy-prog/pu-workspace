"""High-confidence tracked-secret gate; print locations, never matched secrets."""
import json
from pathlib import Path
import re
import subprocess


PATTERNS = {
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'Google OAuth secret': re.compile(rb'GOCSPX-[A-Za-z0-9_-]{20,}'),
    'GitHub token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})'),
    'Google API key': re.compile(rb'AIza[0-9A-Za-z_-]{35}'),
}


def scan():
    problems = []
    files = subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')
    for filename in filter(None, files):
        path = Path(filename)
        if path.name == '.env' or (path.name.startswith('.env.') and path.name != '.env.example') or path.suffix in {'.jks', '.keystore', '.p12'}:
            problems.append({'file': filename, 'rule': 'tracked secret file'})
        if not path.is_file():
            continue
        data = path.read_bytes()
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(data):
                problems.append({'file': filename, 'line': data[:match.start()].count(b'\n') + 1, 'rule': label})
    return problems


if __name__ == '__main__':
    findings = scan()
    print(json.dumps({'passed': not findings, 'findings': findings}, indent=2))
    raise SystemExit(bool(findings))
