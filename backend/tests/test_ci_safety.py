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
