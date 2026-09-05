"""Standalone, stdin-compatible API smoke; only httpx and the standard library."""

import asyncio
import os
import sys

import httpx


class SmokeFailure(Exception):
    def __init__(self, step, status, reason):
        self.step, self.status, self.reason = step, status, reason


def require(condition, step, status, reason):
    if not condition:
        raise SmokeFailure(step, status, reason)


async def smoke(env, *, transport=None, readiness_seconds=60, poll_seconds=1):
    password = env.get("CI_SMOKE_PASSWORD", "")
    bootstrap = env.get("BOOTSTRAP_TOKEN", "")
    revision = env.get("PU_RELEASE_REVISION", "")
    require(12 <= len(password) <= 256, "configuration", "n/a", "CI_SMOKE_PASSWORD must contain 12..256 characters")
    require(len(bootstrap) >= 24, "configuration", "n/a", "BOOTSTRAP_TOKEN must contain at least 24 characters")
    require(bool(revision.strip()), "configuration", "n/a", "PU_RELEASE_REVISION is required")
    base = env.get("CI_SMOKE_BASE_URL", "http://backend:8000")
    try:
        url = httpx.URL(base)
        valid_url = url.scheme in {"http", "https"} and bool(url.host) and not url.userinfo and not url.query and not url.fragment and url.path in {"", "/"}
    except (httpx.InvalidURL, ValueError):
        valid_url = False
    require(valid_url, "configuration", "n/a", "CI_SMOKE_BASE_URL must be an HTTP(S) origin without credentials")

    step, status = "startup", "n/a"
    async with httpx.AsyncClient(base_url=base, timeout=10, follow_redirects=False,
                                 trust_env=False, transport=transport) as client:
        async def request(label, method, path, expected=200, **kwargs):
            nonlocal step, status
            step, status = label, "n/a"
            try:
                response = await client.request(method, path, **kwargs)
            except httpx.RequestError:
                raise SmokeFailure(step, status, "transport error; request was not replayed") from None
            status = response.status_code
            require(status == expected, step, status, f"expected HTTP {expected}")
            return response

        def body(response):
            try:
                data = response.json()
            except ValueError:
                raise SmokeFailure(step, status, "invalid JSON") from None
            require(isinstance(data, dict), step, status, "expected JSON object")
            return data

        def check(condition, reason):
            require(condition, step, status, reason)

        def passed():
            print(f"PASS step={step} http={status}", flush=True)

        # Only startup GETs may be polled. The entire wait, including HTTP I/O,
        # is cancelled by one deadline, even if a peer slowly streams a body.
        try:
            async with asyncio.timeout(readiness_seconds):
                for label, path in (("health", "/health"), ("release", "/api/status"), ("readiness", "/api/readiness")):
                    while True:
                        try:
                            response = await request(label, "GET", path)
                        except SmokeFailure as exc:
                            if exc.status not in {"n/a", 502, 503, 504}:
                                raise
                            await asyncio.sleep(poll_seconds)
                            continue
                        data = body(response)
                        if label == "health":
                            check(data.get("status") == "healthy", "status is not healthy")
                        elif label == "release":
                            check(data.get("status") == "ok", "status is not ok")
                            check(data.get("release") == revision, "release revision mismatch")
                        else:
                            check(type(data.get("ready")) is bool, "ready must be a boolean")
                            if not data["ready"]:
                                await asyncio.sleep(poll_seconds)
                                continue
                        passed()
                        break
        except TimeoutError:
            raise SmokeFailure(step, status, "startup deadline exceeded") from None

        # Ensure this negative check is anonymous even if startup set cookies.
        client.cookies.clear()
        await request("anonymous", "GET", "/auth/me", expected=401)
        passed()
        data = body(await request("bootstrap", "POST", "/auth/bootstrap",
                                  headers={"X-Bootstrap-Token": bootstrap},
                                  json={"name": "CI Smoke", "email": "smoke@example.test", "password": password}))
        check(data.get("token_type") == "cookie", "expected cookie authentication")
        check(isinstance(data.get("user"), dict) and data["user"].get("is_admin") is True, "expected administrator")
        try:
            session = client.cookies.get("pu_session")
            csrf = client.cookies.get("pu_csrf")
        except httpx.CookieConflict:
            raise SmokeFailure(step, status, "ambiguous authentication cookies") from None
        check(bool(session) and bool(csrf), "authentication cookies missing")
        headers = {"X-CSRF-Token": csrf}
        passed()

        data = body(await request("authenticated", "GET", "/auth/me"))
        check(data.get("email") == "smoke@example.test", "user email mismatch")
        passed()
        data = body(await request("organization-create", "POST", "/organizations", headers=headers, json={"name": "CI Organization"}))
        organization_id = data.get("id")
        check(type(organization_id) is int and organization_id > 0, "invalid organization ID")
        check(data.get("name") == "CI Organization", "organization name mismatch")
        passed()
        data = body(await request("project-create", "POST", "/projects/", headers=headers,
                                  json={"name": "CI Project", "organization_id": organization_id}))
        project_id = data.get("id")
        check(type(project_id) is int and project_id > 0, "invalid project ID")
        check(data.get("name") == "CI Project" and type(data.get("organization_id")) is int
              and data["organization_id"] == organization_id, "created project mismatch")
        passed()
        data = body(await request("project-read", "GET", f"/projects/{project_id}"))
        check(type(data.get("id")) is int and data["id"] == project_id and data.get("name") == "CI Project"
              and type(data.get("organization_id")) is int and data["organization_id"] == organization_id, "project fields mismatch")
        passed()
        data = body(await request("project-list", "GET", "/projects/"))
        projects = data.get("projects")
        check(isinstance(projects, list), "expected projects list")
        check(any(isinstance(item, dict) and type(item.get("id")) is int and item["id"] == project_id
                  and item.get("name") == "CI Project" for item in projects), "created project missing from list")
        passed()
        data = body(await request("logout", "POST", "/auth/logout", headers=headers))
        check(data.get("status") == "logged_out", "logout status mismatch")
        passed()
        await request("after-logout", "GET", "/auth/me", expected=401)
        passed()
    print("PASS smoke-api", flush=True)


def main(env=None, **kwargs):
    try:
        asyncio.run(smoke(os.environ if env is None else env, **kwargs))
    except SmokeFailure as exc:
        print(f"FAIL step={exc.step} http={exc.status} reason={exc.reason}", file=sys.stderr)
        return 1
    except Exception:
        # Exception messages, URLs and response bodies may contain credentials.
        print("FAIL step=internal http=n/a reason=unexpected smoke error", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
