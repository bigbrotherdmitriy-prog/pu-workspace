"""Trusted dispatcher: local bounded request files -> one fixed GitHub workflow."""
import datetime, json, os, pathlib, re, stat, time, urllib.request, urllib.error

REPO = 'bigbrotherdmitriy-prog/pu-workspace'
ROOT = pathlib.Path('/home/codex-worker/workspace/autonomy/deepseek')
SAFE_PATH = re.compile(r'(?:backend/(?:app|tests)/|frontend/(?:src|tests)/|docs/)[A-Za-z0-9_./-]+\.(?:py|ts|tsx|css|md)\Z')

def validate(data):
    if not isinstance(data, dict) or set(data) != {'task', 'context_files'}:
        raise ValueError('Expected task and context_files only')
    task = data['task']
    if not isinstance(task, str) or not task.strip() or len(task) > 4900:
        raise ValueError('Task must contain 1-4900 characters')
    if not isinstance(data['context_files'], str):
        raise ValueError('context_files must be a string')
    paths = [p.strip() for p in data['context_files'].split(',')]
    if not 1 <= len(paths) <= 8 or len(set(paths)) != len(paths):
        raise ValueError('Use 1-8 unique source paths')
    for p in paths:
        if not SAFE_PATH.fullmatch(p) or any(x in ('', '.', '..') for x in p.split('/')):
            raise ValueError('Source path outside allowlist')
        if re.search(r'secret|credential|private|token|password', p, re.I):
            raise ValueError('Sensitive path blocked')
    return {'task': task, 'context_files': ','.join(paths)}

def api(path, method='GET', body=None):
    token = os.environ['GH_TOKEN']
    request = urllib.request.Request('https://api.github.com/repos/' + REPO + path,
        method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json', 'User-Agent': 'PU-DeepSeek-queue'})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raise RuntimeError('GitHub HTTP ' + str(error.code)) from None

def save(request_id, data):
    path = ROOT / 'results' / (request_id + '.json')
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.chmod(0o640)
    tmp.replace(path)

def load(path):
    if not re.fullmatch(r'[a-f0-9]{32}\.json', path.name):
        raise ValueError('Invalid request filename')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > 32000:
            raise ValueError('Expected bounded regular request file')
        return validate(json.loads(stream.read(32001)))

def process(path, deadline):
    request_id = path.stem
    result_path = ROOT / 'results' / (request_id + '.json')
    if result_path.exists():
        result = json.loads(result_path.read_text())
        if result.get('state') in ('completed', 'failed', 'rejected'):
            return
    else:
        try:
            inputs = load(path)
        except (ValueError, OSError, json.JSONDecodeError):
            save(request_id, {'state': 'rejected', 'reason': 'Invalid bounded request'})
            return
        # Do not replay a possibly accepted dispatch automatically after an ambiguous timeout.
        result = {'state': 'dispatching', 'created': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        save(request_id, result)
        inputs['request_id'] = request_id
        try:
            api('/actions/workflows/pu-deepseek-agent.yml/dispatches', 'POST', {'ref': 'main', 'inputs': inputs})
        except RuntimeError as error:
            result.update(state='failed', reason=str(error)); save(request_id, result); return
        result['state'] = 'waiting_for_run'; save(request_id, result)
    while time.time() < deadline:
        if not result.get('run_id'):
            runs = api('/actions/workflows/pu-deepseek-agent.yml/runs?event=workflow_dispatch&per_page=30')['workflow_runs']
            matches = [r for r in runs if r.get('display_title') == 'DeepSeek ' + request_id]
            if matches:
                result.update(run_id=matches[0]['id'], run_url=matches[0]['html_url'], state='running')
                save(request_id, result)
        if result.get('run_id'):
            run = api('/actions/runs/' + str(result['run_id']))
            if run['status'] == 'completed':
                result.update(state='completed' if run['conclusion'] == 'success' else 'failed', conclusion=run['conclusion'])
                pulls = api('/pulls?state=open&head=bigbrotherdmitriy-prog:agent/deepseek-' + str(run['id']) + '-' + str(run['run_attempt']))
                result['pull_requests'] = [p['html_url'] for p in pulls]
                save(request_id, result)
                return
        time.sleep(15)

def main():
    assert os.environ.get('GITHUB_REPOSITORY') == REPO
    assert os.environ.get('GITHUB_REF') == 'refs/heads/main'
    deadline = time.time() + int(os.environ.get('DISPATCH_SECONDS', '1200'))
    while time.time() < deadline:
        for path in sorted((ROOT / 'requests').glob('*.json')):
            process(path, deadline)
        time.sleep(10)

if __name__ == '__main__':
    main()
