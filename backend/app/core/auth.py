from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth_session import AuthSession
from app.models.project_member import ProjectMember
from app.models.user import User

bearer = HTTPBearer(auto_error=False)
ROLE_LEVEL = {"viewer": 10, "member": 20, "editor": 30, "manager": 40, "owner": 50}
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
_login_failures: dict[str, deque[datetime]] = defaultdict(deque)
_login_failure_lock = Lock()


def _recent_failures(key: str, now: datetime) -> deque[datetime]:
    failures = _login_failures[key]
    cutoff = now - LOGIN_FAILURE_WINDOW
    while failures and failures[0] <= cutoff:
        failures.popleft()
    return failures


def login_is_throttled(key: str, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    with _login_failure_lock:
        return len(_recent_failures(key, current)) >= LOGIN_FAILURE_LIMIT


def record_login_failure(key: str, now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    with _login_failure_lock:
        _recent_failures(key, current).append(current)


def clear_login_failures(key: str) -> None:
    with _login_failure_lock:
        _login_failures.pop(key, None)


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt:v1:" + base64.urlsafe_b64encode(salt + digest).decode()


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded or not encoded.startswith("scrypt:v1:"):
        return False
    try:
        raw = base64.urlsafe_b64decode(encoded.split(":", 2)[2])
        salt, expected = raw[:16], raw[16:]
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_session(db: Session, user_id: int) -> tuple[str, datetime]:
    cleanup_expired_sessions(db)
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=int(os.getenv("AUTH_SESSION_HOURS", "24")))
    db.add(AuthSession(user_id=user_id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=expires))
    db.commit()
    return token, expires


def cleanup_expired_sessions(db: Session) -> int:
    result = db.execute(delete(AuthSession).where(AuthSession.expires_at <= datetime.now(timezone.utc)))
    db.commit()
    return result.rowcount or 0


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash, AuthSession.expires_at > now))
    if not row:
        raise HTTPException(401, "Session is invalid or expired")
    user = db.get(User, row.user_id)
    if not user:
        raise HTTPException(401, "User not found")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Administrator access required")
    return user


def require_project_role(db: Session, user: User, project_id: int, minimum: str = "viewer") -> str:
    if user.is_admin:
        return "admin"
    role = db.scalar(select(ProjectMember.role).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id))
    if not role or ROLE_LEVEL.get(role, 0) < ROLE_LEVEL[minimum]:
        raise HTTPException(403, "Insufficient project access")
    return role
