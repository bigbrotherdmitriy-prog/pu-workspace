import importlib.util
import json
from pathlib import Path

import pytest


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


def test_authenticated_smoke_reads_core_project_contours(monkeypatch):
    responses = {
        "https://example.test/projects/": (200, json.dumps({"projects": [{"id": 7, "name": "Pilot"}]})),
        "https://example.test/projects/7/launch-readiness": (200, json.dumps({"project_id": 7})),
        "https://example.test/dashboard/project?project_id=7": (200, json.dumps({"summary": {"documents": 3}})),
        "https://example.test/projects/7/documents?limit=1": (200, json.dumps({"total": 3, "documents": []})),
        "https://example.test/projects/7/contracts": (200, json.dumps({"contracts": []})),
        "https://example.test/execution/overview?project_id=7": (200, json.dumps({"summary": {}})),
        "https://example.test/ai-secretary/daily-briefing?project_id=7": (200, json.dumps({"project_id": 7})),
        "https://example.test/integrations/project?project_id=7": (200, json.dumps({"project_id": 7, "adapters": []})),
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
        "documents": True,
        "contracts": True,
        "execution_finance": True,
        "ai_secretary": True,
        "integrations": True,
    }
    assert seen_tokens == ["secret-test-token"] * 8


def test_staging_session_smoke_logs_in_checks_contours_and_logs_out(monkeypatch):
    responses = {
        "https://staging.example.test/auth/login": (200, {}),
        "https://staging.example.test/auth/me": (200, {"email": "ci-admin@example.test"}),
        "https://staging.example.test/projects/": (200, {"projects": [{"id": 7, "name": "Pilot"}]}),
        "https://staging.example.test/projects/7/launch-readiness": (200, {"project_id": 7}),
        "https://staging.example.test/dashboard/project?project_id=7": (200, {"summary": {}}),
        "https://staging.example.test/projects/7/documents?limit=1": (200, {"documents": []}),
        "https://staging.example.test/projects/7/contracts": (200, {"contracts": []}),
        "https://staging.example.test/execution/overview?project_id=7": (200, {"summary": {}}),
        "https://staging.example.test/ai-secretary/daily-briefing?project_id=7": (200, {"project_id": 7}),
        "https://staging.example.test/integrations/project?project_id=7": (200, {"adapters": []}),
        "https://staging.example.test/auth/logout": (200, {}),
    }

    class FakeResponse:
        def __init__(self, status, payload):
            self.status = status
            self.payload = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.payload

    class FakeOpener:
        def __init__(self):
            self.requests = []

        def open(self, request, timeout):
            self.requests.append((request.full_url, request.data, timeout))
            status, payload = responses[request.full_url]
            return FakeResponse(status, payload)

    class FakeCookie:
        def __init__(self, name, value, secure):
            self.name = name
            self.value = value
            self.secure = secure

    jar = [
        FakeCookie("pu_session", "session", True),
        FakeCookie("pu_csrf", "csrf", True),
    ]
    opener = FakeOpener()
    monkeypatch.setattr(MODULE.http.cookiejar, "CookieJar", lambda: jar)
    monkeypatch.setattr(MODULE, "build_opener", lambda *_args: opener)

    result = MODULE.check_authenticated_session(
        "https://staging.example.test", "ci-admin@example.test", "secret-password",
    )

    assert result["project_id"] == 7
    assert opener.requests[0][0].endswith("/auth/login")
    assert json.loads(opener.requests[0][1]) == {
        "email": "ci-admin@example.test", "password": "secret-password",
    }
    assert opener.requests[-1][0].endswith("/auth/logout")


def test_staging_session_smoke_rejects_non_secure_cookies(monkeypatch):
    class FakeCookie:
        def __init__(self, name, secure):
            self.name = name
            self.value = name
            self.secure = secure

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    class FakeOpener:
        def open(self, *_args, **_kwargs):
            return FakeResponse()

    jar = [FakeCookie("pu_session", False), FakeCookie("pu_csrf", False)]
    monkeypatch.setattr(MODULE.http.cookiejar, "CookieJar", lambda: jar)
    monkeypatch.setattr(MODULE, "build_opener", lambda *_args: FakeOpener())

    with pytest.raises(RuntimeError, match="Secure attribute"):
        MODULE.check_authenticated_session(
            "https://staging.example.test", "ci-admin@example.test", "secret-password",
        )


@pytest.mark.parametrize("url", [
    "http://staging.example.test",
    "https://pu-workspace.duckdns.org",
    "https://37.252.23.204",
])
def test_staging_session_smoke_rejects_non_https_and_production(url):
    with pytest.raises(ValueError, match="non-production host"):
        MODULE.check_authenticated_session(url, "ci-admin@example.test", "secret-password")
