from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth_session import AuthSession
from app.models.project_member import ProjectMember
from app.models.user import User

bearer = HTTPBearer(auto_error=False)
ROLE_LEVEL = {"viewer": 10, "member": 20, "editor": 30, "manager": 40, "owner": 50}


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
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=int(os.getenv("AUTH_SESSION_HOURS", "24")))
    db.add(AuthSession(user_id=user_id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=expires))
    db.commit()
    return token, expires


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
