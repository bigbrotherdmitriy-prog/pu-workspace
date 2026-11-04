from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_frontend_uses_httponly_cookie_session_and_csrf_header():
    source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    login = (ROOT / "frontend" / "src" / "auth" / "Login.tsx").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    signout = (ROOT / 'frontend/src/auth/localSignout.ts').read_text(encoding='utf-8')

    assert 'credentials: "same-origin"' in source
    assert 'cookie("pu_csrf")' in source
    assert '"X-CSRF-Token": csrfToken' in source
    assert "pu_token" not in source
    assert "pu_token" not in login
    assert "pu_token" not in app
    # Offline logout fences locally first; revocation is capability-scoped and
    # must never send an ambient cookie belonging to a newer login.
    assert 'beginLocalSignout()' in app
    assert "'/auth/logout/revoke'" in signout
    assert "credentials: 'omit'" in signout
    assert 'isLocallySignedOut()' in source
