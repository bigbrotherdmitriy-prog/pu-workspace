"""Seed/check ONLY an explicitly identified isolated test instance."""
import argparse
import base64
import http.cookiejar
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import build_opener, HTTPCookieProcessor, Request


def environment(path):
    file = Path(path)
    if file.name not in {'.env.ci', '.env.staging'}:
        raise ValueError('Only dedicated test environment files are accepted')
    return dict(line.split('=', 1) for line in file.read_text().splitlines() if line and not line.startswith('#'))


def run(base, env, seed=False):
    parsed = urlparse(base)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.port != int(env['PU_TEST_PORT']) or parsed.port == 3000:
        raise ValueError('Smoke writes require the configured isolated loopback port')
    jar = http.cookiejar.CookieJar()
    client = build_opener(HTTPCookieProcessor(jar))

    def request(path, payload=None, extra=None):
        headers = {'Content-Type': 'application/json'}
        headers.update(extra or {})
        headers.update({'X-CSRF-Token': cookie.value for cookie in jar if cookie.name == 'pu_csrf'})
        body = json.dumps(payload).encode() if payload is not None else None
        with client.open(Request(base + path, data=body, headers=headers), timeout=30) as response:
            return json.load(response)

    def wait_for_job(job_id, timeout_seconds=90):
        deadline = time.monotonic() + timeout_seconds
        last_status = "queued"
        while time.monotonic() < deadline:
            jobs = request("/admin/jobs?limit=100").get("jobs", [])
            job = next((item for item in jobs if item.get("id") == job_id), None)
            if job:
                last_status = job.get("status", "unknown")
                if last_status == "completed":
                    return job
                if last_status in {"failed", "dead_letter", "cancelled"}:
                    raise AssertionError(
                        f"Upload job {job_id} ended with {last_status}: {job.get('error') or 'no error detail'}"
                    )
            time.sleep(1)
        raise AssertionError(f"Upload job {job_id} did not complete; last status={last_status}")

    last_failure = 'not ready'
    for attempt in range(60):
        try:
            ready = request('/api/readiness')
            if ready.get('ready'):
                break
        except (HTTPError, URLError, TimeoutError) as error:
            last_failure = type(error).__name__ + (f' status={error.code}' if isinstance(error, HTTPError) else '')
        time.sleep(2)
    else:
        raise RuntimeError(f'Readiness did not become healthy: {last_failure}')
    assert request('/health')['status'] == 'healthy'
    assert request('/api/status')['release'] == env['PU_RELEASE_REVISION'], 'Wrong deployed commit'
    # Check that application data cannot be read anonymously.
    try:
        request('/projects/')
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError('Anonymous project access is allowed')
    credentials = {'email': 'ci-admin@example.test', 'password': env['PU_SMOKE_PASSWORD']}
    if seed:
        request('/auth/bootstrap', {**credentials, 'name': 'CI administrator'}, {'X-Bootstrap-Token': env['BOOTSTRAP_TOKEN']})
    request('/auth/login', credentials)
    assert request('/auth/me')['email'] == credentials['email']
    if seed:
        organization = request('/organizations', {'name': 'Disposable CI organization'})
        project = request('/projects/', {'name': 'CI project A', 'organization_id': organization['id']})
        request('/projects/', {'name': 'CI project B', 'organization_id': organization['id']})
        result = request('/local-upload/analyze', {'project_id': project['id'], 'files': [{
            'path': 'nested/acceptance.txt', 'mime_type': 'text/plain',
            'content_base64': base64.b64encode('Просим подготовить акт выполненных работ до 30.12.2026. Ответственный: Иванов.'.encode()).decode(),
        }]})
        assert result.get('status') == 'queued' and len(result.get('jobs', [])) == 1, \
            'Upload was not admitted to the durable queue'
        wait_for_job(result['jobs'][0]['job_id'])
    projects = {item['name']: item['id'] for item in request('/projects/')['projects']}
    first, second = projects['CI project A'], projects['CI project B']
    documents = request(f'/projects/{first}/documents')['documents']
    assert any('acceptance.txt' in item['name'] for item in documents)
    assert not request(f'/projects/{second}/documents')['documents'], 'Project data leaked into another project'
    assert 'summary' in request(f'/dashboard/project?project_id={first}')
    assert 'adapters' in request(f'/integrations/project?project_id={first}')
    request('/auth/logout', {})
    print(json.dumps({'ready': True, 'release': env['PU_RELEASE_REVISION'], 'upload_and_project_isolation': True, 'seed': seed}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', default='.env.ci')
    parser.add_argument('--seed', action='store_true')
    args = parser.parse_args()
    values = environment(args.env_file)
    run(f"http://127.0.0.1:{values['PU_TEST_PORT']}", values, args.seed)
