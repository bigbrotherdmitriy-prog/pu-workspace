import importlib.util
from pathlib import Path

import pytest


def script(name):
    path = Path(__file__).resolve().parents[2] / 'scripts' / f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('base,port', [
    ('http://localhost:3000', '3000'),
    ('https://pu-workspace.duckdns.org', '443'),
    ('http://127.0.0.1:3011', '3010'),
])
def test_smoke_rejects_production_or_wrong_target_before_network(base, port):
    with pytest.raises(ValueError):
        script('check_ci_smoke').run(base, {'PU_TEST_PORT': port}, seed=True)


def test_log_artifact_redacts_credentials_and_personal_identifiers():
    result = script('redact_ci_logs').redact(
        'failed token=sensitive-test-value Bearer abcdefghi user@example.test https://private.test/file',
        ['sensitive-test-value'],
    )
    for value in ['sensitive-test-value', 'abcdefghi', 'user@example.test', 'private.test']:
        assert value not in result


def test_test_environment_cannot_read_production_env(tmp_path):
    path = tmp_path / '.env'
    path.write_text('APP_SECRET_KEY=must-not-be-read')
    with pytest.raises(ValueError):
        script('check_ci_smoke').environment(path)


def audit_payload(high=0, critical=0):
    return '{"metadata":{"vulnerabilities":{"info":0,"low":0,"moderate":0,"high":%d,"critical":%d,"total":%d}}}' % (
        high,
        critical,
        high + critical,
    )


def test_frontend_audit_retries_transient_registry_failures(tmp_path, monkeypatch):
    module = script('run_frontend_audit')
    results = iter([(1, '{"error":{"code":"ETIMEDOUT"}}', 'timeout'), (0, audit_payload(), '')])
    monkeypatch.setattr(module, 'audit_once', lambda *args: next(results))
    sleeps = []
    assert module.run(tmp_path / 'audit.json', 3, 60, sleep=sleeps.append) == 0
    assert sleeps == [5]


def test_frontend_audit_does_not_retry_real_high_findings(tmp_path, monkeypatch):
    module = script('run_frontend_audit')
    calls = []

    def vulnerable(*args):
        calls.append(args)
        return 1, audit_payload(high=1), ''

    monkeypatch.setattr(module, 'audit_once', vulnerable)
    assert module.run(tmp_path / 'audit.json', 3, 60, sleep=lambda _: None) == 1
    assert len(calls) == 1


def test_frontend_audit_fails_closed_after_bounded_retries(tmp_path, monkeypatch):
    module = script('run_frontend_audit')
    monkeypatch.setattr(module, 'audit_once', lambda *args: (124, '', 'timeout'))
    output = tmp_path / 'audit.json'
    assert module.run(output, 2, 60, sleep=lambda _: None) == 2
    assert 'unavailable' in output.read_text(encoding='utf-8')
