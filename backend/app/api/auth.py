import hashlib
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import (
    clear_login_failures,
    hash_password,
    issue_session,
    login_is_throttled,
    record_login_failure,
    require_user,
    verify_password,
)
from app.database import get_db
from app.models.auth_session import AuthSession
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=256)


class BootstrapRequest(Credentials):
    name: str = Field(min_length=1, max_length=255)


@router.post("/bootstrap")
def bootstrap(payload: BootstrapRequest, x_bootstrap_token: str = Header(default=""), db: Session = Depends(get_db)):
    configured = os.getenv("BOOTSTRAP_TOKEN", "")
    if len(configured) < 24 or not hmac.compare_digest(configured, x_bootstrap_token):
        raise HTTPException(403, "Invalid bootstrap token")
    if db.scalar(select(func.count()).select_from(User).where(User.password_hash.is_not(None))):
        raise HTTPException(409, "Authentication is already initialized")
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.strip().lower()))
    if user is None:
        user = User(name=payload.name.strip(), email=payload.email.strip().lower())
        db.add(user)
    try:
        user.password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    user.is_admin = True
    db.commit(); db.refresh(user)
    token, expires = issue_session(db, user.id)
    return {"access_token": token, "token_type": "bearer", "expires_at": expires, "user": _user(user)}


@router.post("/login")
def login(payload: Credentials, request: Request, db: Session = Depends(get_db)):
    normalized_email = payload.email.strip().lower()
    client_host = request.client.host if request.client else "unknown"
    throttle_key = hashlib.sha256(f"{client_host}:{normalized_email}".encode()).hexdigest()
    if login_is_throttled(throttle_key):
        raise HTTPException(429, "Too many failed login attempts. Try again later.", headers={"Retry-After": "900"})
    user = db.scalar(select(User).where(func.lower(User.email) == normalized_email))
    if not user or not verify_password(payload.password, user.password_hash):
        record_login_failure(throttle_key)
        raise HTTPException(401, "Invalid email or password")
    clear_login_failures(throttle_key)
    token, expires = issue_session(db, user.id)
    return {"access_token": token, "token_type": "bearer", "expires_at": expires, "user": _user(user)}


@router.get("/me")
def me(user: User = Depends(require_user)):
    return _user(user)


@router.post("/logout")
def logout(user: User = Depends(require_user), authorization: str = Header(default=""), db: Session = Depends(get_db)):
    raw = authorization.removeprefix("Bearer ").strip()
    if raw:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hashlib.sha256(raw.encode()).hexdigest(), AuthSession.user_id == user.id))
        if row:
            db.delete(row); db.commit()
    return {"status": "logged_out"}


def _user(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "is_admin": user.is_admin}
