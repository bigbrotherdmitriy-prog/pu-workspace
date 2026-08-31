from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - register all mapped tables
from app.api.auth import _set_session_cookies
from app.core.auth import require_user
from app.database import Base
from app.models.auth_session import AuthSession
from app.models.user import User


def _request(method="GET", cookie="", csrf=""):
    headers = [(b"x-forwarded-proto", b"https")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if csrf:
        headers.append((b"x-csrf-token", csrf.encode()))
    return Request({"type": "http", "method": method, "scheme": "https", "path": "/", "headers": headers})


def _database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def test_session_cookie_is_httponly_and_csrf_cookie_is_readable():
    response = Response()
    _set_session_cookies(response, _request(), "raw-secret-token", datetime.now(timezone.utc) + timedelta(hours=1))
    cookies = response.headers.getlist("set-cookie")

    assert any("pu_session=raw-secret-token" in value and "HttpOnly" in value and "Secure" in value for value in cookies)
    assert any("pu_csrf=" in value and "HttpOnly" not in value and "SameSite=strict" in value for value in cookies)


def test_cookie_authenticated_write_requires_matching_csrf_header():
    engine = _database()
    with Session(engine) as db:
        user = User(name="Admin", email="admin@example.test", is_admin=True)
        db.add(user)
        db.flush()
        import hashlib
        db.add(AuthSession(
            user_id=user.id,
            token_hash=hashlib.sha256(b"session-token").hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.commit()

        with pytest.raises(HTTPException) as failure:
            require_user(_request("POST", "pu_session=session-token; pu_csrf=csrf-token"), None, db)
        assert failure.value.status_code == 403

        authenticated = require_user(
            _request("POST", "pu_session=session-token; pu_csrf=csrf-token", "csrf-token"),
            None,
            db,
        )
        assert authenticated.id == user.id
