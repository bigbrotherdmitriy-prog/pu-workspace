import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_public_smoke.py"
SPEC = importlib.util.spec_from_file_location("check_public_smoke", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_public_smoke_checks_readiness_and_current_spa(monkeypatch):
    responses = {
        "https://example.test/api/readiness": (200, json.dumps({"ready": True})),
        "https://example.test/api/status": (200, json.dumps({"status": "ok", "release": "current"})),
        "https://example.test/new/": (200, '<div id="root"></div><script src="./assets/current.js"></script>'),
        "https://example.test/new/assets/current.js": (200, " ".join(MODULE.REQUIRED_UI_MARKERS)),
    }
    monkeypatch.setattr(MODULE, "_get", lambda url, timeout=20: responses[url])

    result = MODULE.check_public("https://example.test")

    assert result["ready"] is True
    assert result["markers"] == 4


def test_public_smoke_rejects_stale_frontend(monkeypatch):
    responses = {
        "https://example.test/api/readiness": (200, json.dumps({"ready": True})),
        "https://example.test/api/status": (200, json.dumps({"status": "ok", "release": "current"})),
        "https://example.test/new/": (200, '<div id="root"></div><script src="/new/assets/old.js"></script>'),
        "https://example.test/new/assets/old.js": (200, "Запуск проекта"),
    }
    monkeypatch.setattr(MODULE, "_get", lambda url, timeout=20: responses[url])

    try:
        MODULE.check_public("https://example.test/")
    except RuntimeError as exc:
        assert "incomplete or stale" in str(exc)
    else:
        raise AssertionError("stale frontend must fail production smoke")


def test_public_smoke_rejects_different_asset_from_candidate(monkeypatch):
    responses = {
        "https://example.test/api/readiness": (200, json.dumps({"ready": True})),
        "https://example.test/api/status": (200, json.dumps({"status": "ok", "release": "current"})),
        "https://example.test/new/": (200, '<div id="root"></div><script src="/new/assets/old.js"></script>'),
    }
    monkeypatch.setattr(MODULE, "_get", lambda url, timeout=20: responses[url])

    try:
        MODULE.check_public("https://example.test/", expected_asset="current.js")
    except RuntimeError as exc:
        assert "asset is stale" in str(exc)
        assert "expected current.js, got old.js" in str(exc)
    else:
        raise AssertionError("different deployed asset must fail production smoke")


def test_public_smoke_rejects_different_backend_release(monkeypatch):
    responses = {
        "https://example.test/api/readiness": (200, json.dumps({"ready": True})),
        "https://example.test/api/status": (200, json.dumps({"status": "ok", "release": "old"})),
    }
    monkeypatch.setattr(MODULE, "_get", lambda url, timeout=20: responses[url])

    try:
        MODULE.check_public("https://example.test/", expected_release="new")
    except RuntimeError as exc:
        assert "backend release is stale" in str(exc)
        assert "expected new, got old" in str(exc)
    else:
        raise AssertionError("different backend release must fail production smoke")


def test_authenticated_smoke_reads_project_launch_and_dashboard(monkeypatch):
    responses = {
        "https://example.test/projects/": (200, json.dumps({"projects": [{"id": 7, "name": "Pilot"}]})),
        "https://example.test/projects/7/launch-readiness": (200, json.dumps({"project_id": 7})),
        "https://example.test/dashboard/project?project_id=7": (200, json.dumps({"summary": {"documents": 3}})),
    }
    seen_tokens = []

    def fake_get(url, timeout=20, token=None):
        seen_tokens.append(token)
        return responses[url]

    monkeypatch.setattr(MODULE, "_get", fake_get)
    result = MODULE.check_authenticated_flow("https://example.test", "secret-test-token")

    assert result == {
        "project_id": 7,
        "project_name": "Pilot",
        "launch_readiness": True,
        "dashboard": True,
    }
    assert seen_tokens == ["secret-test-token"] * 3
