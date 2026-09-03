"""Require verified Actions checks on main, preserving existing protection.

Uses Git's configured credential helper for github.com. Never prints credentials.
Default mode is read-only. --apply requires successful checks for --sha first.
"""
import argparse
import json
import os
import re
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

REPO = 'bigbrotherdmitriy-prog/pu-workspace'
CHECKS = {'test-and-build', 'docker-smoke', 'package-and-secrets', 'python-dependencies', 'frontend-dependencies'}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.apply and not re.fullmatch(r'[a-f0-9]{40}', args.sha or ''):
        parser.error('--apply requires an exact tested --sha')
    result = subprocess.run(
        ['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
        capture_output=True, text=True, check=True, timeout=30,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'},
    )
    credential = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    token = credential.get('password')
    if not token:
        raise RuntimeError('GitHub credential helper has no usable credential')
    client = build_opener(NoRedirect())

    def api(path, body=None, method=None):
        request = Request('https://api.github.com/repos/' + REPO + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          method=method,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'application/vnd.github+json',
                                   'Content-Type': 'application/json',
                                   'X-GitHub-Api-Version': '2022-11-28'})
        with client.open(request, timeout=30) as response:
            return json.load(response)

    path = '/branches/main/protection'
    try:
        protection = api(path)
    except HTTPError as error:
        if error.code != 404:
            raise RuntimeError(f'GitHub protection access failed: HTTP {error.code}') from None
        protection = None
    if args.apply:
        runs = api(f'/commits/{args.sha}/check-runs?per_page=100')['check_runs']
        verified = {}
        for run in runs:
            if run['name'] in CHECKS and run['name'] not in verified:
                if run['status'] != 'completed' or run['conclusion'] != 'success':
                    raise RuntimeError('Required check not successful: ' + run['name'])
                if run['app']['slug'] != 'github-actions':
                    raise RuntimeError('Required check is not supplied by GitHub Actions')
                verified[run['name']] = run['app']['id']
        if set(verified) != CHECKS:
            raise RuntimeError('Missing required checks: ' + ', '.join(sorted(CHECKS - set(verified))))
        old = (protection or {}).get('required_status_checks') or {}
        checks = {item['context']: item for item in old.get('checks', [])}
        for context in old.get('contexts', []):
            checks.setdefault(context, {'context': context})
        checks.update({name: {'context': name, 'app_id': app_id} for name, app_id in verified.items()})
        status = {'strict': True, 'checks': list(checks.values())}
        if protection:
            api(path + '/required_status_checks', status, 'PATCH')
        else:
            api(path, {'required_status_checks': status, 'enforce_admins': True,
                       'required_pull_request_reviews': None, 'restrictions': None}, 'PUT')
        protection = api(path)
        actual = set(protection['required_status_checks']['contexts'])
        if not CHECKS <= actual:
            raise RuntimeError('Protection verification failed')
    print(json.dumps({'repository': REPO, 'branch': 'main', 'applied': args.apply,
                      'protected': protection is not None,
                      'required_checks': ((protection or {}).get('required_status_checks') or {}).get('contexts', [])}))


if __name__ == '__main__':
    main()
