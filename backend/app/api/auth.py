import hashlib
import hmac
import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
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
from app.models.audit_log import AuditLog
from app.models.user import User
from app.logout_revocation import ticket_for_session, revoke_ticket

router = APIRouter(prefix="/auth", tags=["auth"])


def _bearer_token(authorization: str) -> str:
    """Extract a bearer token case-insensitively, mirroring require_user.

    The scheme match is case-insensitive (``Bearer``/``bearer``/``BEARER``).
    Literal ``undefined``/``null`` credentials are treated as absent so a stray
    header injected by browser extensions cannot shadow a valid cookie.
    """
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return ""
    token = value.strip()
    if token in ("undefined", "null"):
        return ""
    return token


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=256)


class BootstrapRequest(Credentials):
    name: str = Field(min_length=1, max_length=255)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=12, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


def _secure_cookie(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"


def _set_session_cookies(response: Response, request: Request, token: str, expires) -> None:
    response.headers['Cache-Control'] = 'no-store'
    secure = _secure_cookie(request)
    response.set_cookie("pu_session", token, expires=expires, httponly=True, secure=secure, samesite="strict", path="/")
    response.set_cookie("pu_csrf", secrets.token_urlsafe(24), expires=expires, httponly=False, secure=secure, samesite="strict", path="/")


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie("pu_session", path="/")
    response.delete_cookie("pu_csrf", path="/")


@router.post("/bootstrap")
def bootstrap(payload: BootstrapRequest, request: Request, response: Response, x_bootstrap_token: str = Header(default=""), db: Session = Depends(get_db)):
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
    _set_session_cookies(response, request, token, expires)
    return {"token_type": "cookie", "expires_at": expires, "user": _user(user),
            "logout_ticket": ticket_for_session(db, token)}


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
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
    _set_session_cookies(response, request, token, expires)
    return {"token_type": "cookie", "expires_at": expires, "user": _user(user),
            "logout_ticket": ticket_for_session(db, token)}


@router.get("/me")
def me(user: User = Depends(require_user)):
    return _user(user)


@router.get('/logout-intent')
def logout_intent(request: Request, response: Response,
                  user: User = Depends(require_user), authorization: str = Header(default=''),
                  db: Session = Depends(get_db)):
    raw = _bearer_token(authorization) or request.cookies.get('pu_session', '')
    response.headers['Cache-Control'] = 'no-store'
    return {'logout_ticket': ticket_for_session(db, raw), 'user_id': user.id}


class PendingLogout(BaseModel):
    ticket: str = Field(min_length=66, max_length=84, pattern=r'^[1-9][0-9]{0,18}\.[a-f0-9]{64}$')


@router.post('/logout/revoke')
def revoke_pending_logout(body: PendingLogout, request: Request, response: Response,
                          db: Session = Depends(get_db)):
    # Deliberately ignores ambient authentication: a scoped capability can ONLY
    # revoke its original session and must not revoke a new user's cookie.
    response.headers['Cache-Control'] = 'no-store'
    return revoke_ticket(db, body.ticket, request, response)


@router.post("/logout")
def logout(request: Request, response: Response, user: User = Depends(require_user), authorization: str = Header(default=""), db: Session = Depends(get_db)):
    bearer_token = _bearer_token(authorization)
    cookie_token = request.cookies.get("pu_session", "")
    raw = bearer_token or cookie_token
    if raw:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hashlib.sha256(raw.encode()).hexdigest(), AuthSession.user_id == user.id))
        if row:
            db.delete(row); db.commit()
    _clear_session_cookies(response)
    return {"status": "logged_out"}


@router.post("/change-password")
def change_password(
    payload: PasswordChange,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(401, "Текущий пароль указан неверно")
    if hmac.compare_digest(payload.current_password, payload.new_password):
        raise HTTPException(422, "Новый пароль должен отличаться от текущего")
    try:
        user.password_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    revoked = db.execute(delete(AuthSession).where(AuthSession.user_id == user.id)).rowcount or 0
    db.add(AuditLog(
        action="password_changed",
        entity_type="user",
        entity_id=user.id,
        details=f"sessions_revoked={revoked}; password_stored=false",
    ))
    db.commit()
    _clear_session_cookies(response)
    return {"status": "password_changed", "reauthentication_required": True}


def _user(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email, "is_admin": user.is_admin}
