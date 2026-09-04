import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "smoke_api.py"
spec = importlib.util.spec_from_file_location("smoke_api", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
ENV = {"CI_SMOKE_PASSWORD": "synthetic-password-123", "BOOTSTRAP_TOKEN": "synthetic-bootstrap-token-123456", "PU_RELEASE_REVISION": "a" * 40}
SECRET = "DO-NOT-PRINT-RESPONSE-SECRET"


class API:
    def __init__(self, mutate=None):
        self.calls = []
        self.mutate = mutate
        self.active = False

    def __call__(self, request):
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        headers = []
        code = 200
        if path == "/health":
            data = {"status": "healthy"}
        elif path == "/api/status":
            data = {"status": "ok", "release": ENV["PU_RELEASE_REVISION"]}
        elif path == "/api/readiness":
            data = {"ready": True}
        elif path == "/auth/me":
            if self.active:
                assert "pu_session=session-secret" in request.headers.get("cookie", "")
                data = {"email": "smoke@example.test"}
            else:
                code, data = 401, {"detail": "Authentication required"}
        elif path == "/auth/bootstrap":
            assert request.headers["X-Bootstrap-Token"] == ENV["BOOTSTRAP_TOKEN"]
            assert json.loads(request.content) == {"name": "CI Smoke", "email": "smoke@example.test", "password": ENV["CI_SMOKE_PASSWORD"]}
            self.active = True
            headers = [("set-cookie", "pu_session=session-secret; Path=/; HttpOnly"), ("set-cookie", "pu_csrf=csrf-secret; Path=/")]
            data = {"token_type": "cookie", "user": {"is_admin": True}}
        elif path == "/organizations":
            assert json.loads(request.content) == {"name": "CI Organization"}
            data = {"id": 7, "name": "CI Organization"}
        elif path == "/projects/" and method == "POST":
            assert json.loads(request.content) == {"name": "CI Project", "organization_id": 7}
            data = {"id": 9, "name": "CI Project", "organization_id": 7}
        elif path == "/projects/9":
            data = {"id": 9, "name": "CI Project", "organization_id": 7}
        elif path == "/projects/":
            data = {"projects": [{"id": 9, "name": "CI Project", "archived_at": None}]}
        elif path == "/auth/logout":
            self.active = False
            headers = [("set-cookie", "pu_session=; Max-Age=0; Path=/"), ("set-cookie", "pu_csrf=; Max-Age=0; Path=/")]
            data = {"status": "logged_out"}
        else:
            raise AssertionError("Unexpected endpoint")
        if method == "POST" and path != "/auth/bootstrap":
            assert request.headers["X-CSRF-Token"] == "csrf-secret"
            assert "pu_session=session-secret" in request.headers["cookie"]
            assert "pu_csrf=csrf-secret" in request.headers["cookie"]
        response = httpx.Response(code, json=data, headers=headers)
        return self.mutate(request, response) if self.mutate else response


def run(api, **kwargs):
    return smoke.main(ENV, transport=httpx.MockTransport(api), readiness_seconds=0.05, poll_seconds=0.001, **kwargs)


def test_success_cookie_csrf_and_request_order(capsys):
    api = API()
    assert run(api) == 0
    assert api.calls == [("GET", "/health"), ("GET", "/api/status"), ("GET", "/api/readiness"),
                         ("GET", "/auth/me"), ("POST", "/auth/bootstrap"), ("GET", "/auth/me"),
                         ("POST", "/organizations"), ("POST", "/projects/"), ("GET", "/projects/9"),
                         ("GET", "/projects/"), ("POST", "/auth/logout"), ("GET", "/auth/me")]
    assert "PASS smoke-api" in capsys.readouterr().out


@pytest.mark.parametrize("path,method,payload", [
    ("/health", "GET", {"status": "bad"}),
    ("/api/status", "GET", {"status": "ok", "release": "wrong"}),
    ("/api/status", "GET", {"status": "bad", "release": ENV["PU_RELEASE_REVISION"]}),
    ("/api/readiness", "GET", {"ready": False}),
    ("/api/readiness", "GET", {"ready": "true"}),
    ("/auth/bootstrap", "POST", {"token_type": "bearer", "user": {"is_admin": True}}),
    ("/auth/bootstrap", "POST", {"token_type": "cookie", "user": {"is_admin": False}}),
    ("/auth/bootstrap", "POST", {"token_type": "cookie", "user": None}),
    ("/organizations", "POST", {"id": True, "name": "CI Organization"}),
    ("/projects/", "POST", {"id": 9, "name": "CI Project", "organization_id": 99}),
    ("/projects/9", "GET", {"id": 8, "name": "CI Project", "organization_id": 7}),
    ("/projects/9", "GET", {"id": 9, "name": "wrong", "organization_id": 7}),
    ("/projects/9", "GET", {"id": 9, "name": "CI Project", "organization_id": 8}),
    ("/projects/", "GET", {"projects": []}),
    ("/projects/", "GET", {"projects": None}),
    ("/auth/logout", "POST", {"status": "wrong"}),
])
def test_rejects_contract_mismatch(path, method, payload):
    def mutate(req, response):
        return httpx.Response(200, json=payload, headers=response.headers) if (req.method, req.url.path) == (method, path) else response
    assert run(API(mutate)) == 1


@pytest.mark.parametrize("path", ["/auth/bootstrap", "/organizations", "/projects/", "/auth/logout"])
@pytest.mark.parametrize("failure", ["transport", "http", "json", "shape"])
def test_writes_never_replayed_and_diagnostics_redacted(path, failure, capsys):
    def mutate(req, response):
        if req.method == "POST" and req.url.path == path:
            if failure == "transport":
                raise httpx.ReadTimeout(SECRET, request=req)
            if failure == "http":
                return httpx.Response(503, text=SECRET)
            if failure == "json":
                return httpx.Response(200, text=SECRET)
            return httpx.Response(200, json=[SECRET])
        return response
    api = API(mutate)
    assert run(api) == 1
    assert api.calls.count(("POST", path)) == 1
    output = capsys.readouterr()
    for value in [SECRET, ENV["CI_SMOKE_PASSWORD"], ENV["BOOTSTRAP_TOKEN"], "session-secret", "csrf-secret"]:
        assert value not in output.out + output.err
    assert "FAIL step=" in output.err and "http=" in output.err


@pytest.mark.parametrize("stage", ["anonymous", "authenticated", "after-logout"])
def test_auth_failures(stage):
    count = 0
    def mutate(req, response):
        nonlocal count
        if req.url.path == "/auth/me":
            count += 1
            if (stage, count) in {("anonymous", 1), ("authenticated", 2), ("after-logout", 3)}:
                return httpx.Response(200, json={"email": "wrong@example.test"})
        return response
    assert run(API(mutate)) == 1


def test_missing_cookies():
    assert run(API(lambda req, res: httpx.Response(200, json=res.json()) if req.url.path == "/auth/bootstrap" else res)) == 1


def test_transient_startup_retry():
    api = API()
    attempts = 0
    def handler(req):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError(SECRET, request=req)
        if attempts == 2:
            return httpx.Response(503)
        return api(req)
    assert run(handler) == 0


def test_deadline_cancels_inflight_request(capsys):
    async def handler(req):
        await asyncio.sleep(10)
        return httpx.Response(200)
    assert run(handler) == 1
    assert "startup deadline exceeded" in capsys.readouterr().err


def test_startup_deadline_is_shared_across_endpoints(capsys):
    api = API()
    async def handler(req):
        await asyncio.sleep(0.1)
        return api(req)
    assert smoke.main(ENV, transport=httpx.MockTransport(handler), readiness_seconds=0.25) == 1
    assert not any(method == "POST" for method, _ in api.calls)
    assert "startup deadline exceeded" in capsys.readouterr().err


def test_readiness_false_then_true():
    attempts = 0
    def mutate(req, response):
        nonlocal attempts
        if req.url.path == "/api/readiness":
            attempts += 1
            if attempts == 1:
                return httpx.Response(200, json={"ready": False})
        return response
    assert run(API(mutate)) == 0
    assert attempts == 2


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:secret@backend:8000", "http://backend:8000/?token=secret"])
def test_invalid_origin_is_rejected_without_leaking_value(url, capsys):
    api = API()
    assert smoke.main({**ENV, "CI_SMOKE_BASE_URL": url}, transport=httpx.MockTransport(api)) == 1
    assert not api.calls
    assert url not in capsys.readouterr().err


@pytest.mark.parametrize("key", ["CI_SMOKE_PASSWORD", "BOOTSTRAP_TOKEN", "PU_RELEASE_REVISION"])
def test_missing_configuration_makes_no_requests(key):
    env = dict(ENV)
    del env[key]
    api = API()
    assert smoke.main(env, transport=httpx.MockTransport(api)) == 1
    assert not api.calls


def test_stdin_entrypoint_without_neighbor_files(tmp_path):
    # Execute exactly as `python -` in an unrelated directory, no app imports.
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    env["CI_SMOKE_PASSWORD"] = ""
    result = subprocess.run([sys.executable, "-"], input=SCRIPT.read_text(encoding="utf-8"),
                            text=True, cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode == 1
    assert "FAIL step=configuration" in result.stderr
    assert "Traceback" not in result.stderr
