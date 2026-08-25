import hashlib
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import hash_password, issue_session, require_user, verify_password
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
def login(payload: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.strip().lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
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
