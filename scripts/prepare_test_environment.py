"""Generate disposable credentials; never read production .env or overwrite a file."""
import argparse
import base64
import os
from pathlib import Path
import secrets
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='.env.ci')
    parser.add_argument('--port', type=int, default=3010)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.port == 3000:
        parser.error('choose a non-production port between 1024 and 65535')
    target = Path(args.output)
    if target.name not in {'.env.ci', '.env.staging'}:
        parser.error('output must be .env.ci or .env.staging')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    values = {
        'POSTGRES_PASSWORD': secrets.token_hex(24),
        'APP_SECRET_KEY': secrets.token_hex(32),
        'TOKEN_ENCRYPTION_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),
        'BOOTSTRAP_TOKEN': secrets.token_urlsafe(32),
        'PU_SMOKE_PASSWORD': secrets.token_urlsafe(24),
        'PU_RELEASE_REVISION': revision,
        'PU_TEST_PORT': str(args.port),
    }
    with target.open('x', encoding='utf-8') as output:
        output.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    if os.name != 'nt':
        target.chmod(0o600)
    print(f'Created {target.name}; credential values are not logged.')


if __name__ == '__main__':
    main()
