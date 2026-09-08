import json
from types import SimpleNamespace

import pytest

from test_mvp_runtime_coverage import runner


def probe():
    return dict(probe="authority_concurrency", status="FAIL", phase="revoke_change", error_code="ValueError")


@pytest.mark.parametrize("mutation", [
    {"phase": "secret"}, {"error_code": "secret"}, {"status": "PASS"},
    {"probe": "external"}, {"payload": "secret"}, {"phase": []},
    {"error_code": {}}, {"phase": "x" * 300},
])
def test_rejects_untrusted_probe(mutation):
    assert runner().authority_failure_probe(json.dumps({**probe(), **mutation})) is None


def test_accepts_only_fixed_probe():
    module = runner()
    assert module.authority_failure_probe("raw secret\n" + json.dumps(probe())) == probe()
    for invalid in ("null", "[]", "invalid", '"text"'):
        assert module.authority_failure_probe(invalid) is None


@pytest.mark.parametrize("phase,code,expected", [
    ("postgres_authority_runtime", 1, True),
    ("unrelated", 1, False),
    ("unrelated", 0, False),
])
def test_failed_authority_phase_attaches_safe_probe(monkeypatch, phase, code, expected):
    module = runner()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=code, stdout=json.dumps(probe()), stderr="secret"))
    if code:
        with pytest.raises(RuntimeError):
            module.run_phase(phase, ["synthetic"])
    else:
        module.run_phase(phase, ["synthetic"])
    assert ("authority_failure" in module.PHASES[-1]) is expected
    assert "secret" not in json.dumps(module.PHASES)
