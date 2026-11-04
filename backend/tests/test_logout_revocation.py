"""Codex fallback: synthetic revocation-only capability and session isolation."""
from datetime import datetime, timedelta, timezone
import hashlib

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models
from app.database import Base
from app.models.auth_session import AuthSession
from app.models.user import User
from app.logout_revocation import ticket_for_session, revoke_ticket


@pytest.fixture
def world():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(name='SYNTHETIC', email='logout@example.test')
        db.add(user); db.flush()
        for raw in ['SYNTHETIC-old', 'SYNTHETIC-new']:
            db.add(AuthSession(user_id=user.id, token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                               expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.commit()
        yield db
    engine.dispose()


def request(raw=''):
    return Request({'type':'http', 'method':'POST', 'path':'/auth/logout/revoke',
                    'headers':[(b'cookie', ('pu_session='+raw).encode())]})


def test_revoke_original_without_cookie_and_idempotent_replay(world):
    ticket = ticket_for_session(world, 'SYNTHETIC-old')
    assert 'SYNTHETIC-old' not in ticket
    assert revoke_ticket(world, ticket, request(), Response()) == {'status':'revoked'}
    assert world.query(AuthSession).count() == 1
    response = Response()
    assert revoke_ticket(world, ticket, request('SYNTHETIC-new'), response) == {'status':'revoked'}
    assert not response.headers.getlist('set-cookie')
    assert world.query(AuthSession).count() == 1


def test_deferred_old_logout_cannot_clear_new_session_cookie(world):
    response = Response()
    revoke_ticket(world, ticket_for_session(world, 'SYNTHETIC-old'), request('SYNTHETIC-new'), response)
    assert not response.headers.getlist('set-cookie')
    assert ticket_for_session(world, 'SYNTHETIC-new')


def test_deferred_response_never_clears_even_matching_ambient_cookie(world):
    response = Response()
    revoke_ticket(world, ticket_for_session(world, 'SYNTHETIC-old'), request('SYNTHETIC-old'), response)
    assert not response.headers.getlist('set-cookie')


def test_forged_capability_never_revokes(world):
    ticket = ticket_for_session(world, 'SYNTHETIC-old')
    wrong = ticket.rsplit('.', 1)[0]+'.'+('0'*64)
    with pytest.raises(HTTPException) as exc:
        revoke_ticket(world, wrong, request('SYNTHETIC-new'), Response())
    assert exc.value.status_code == 403
    assert world.query(AuthSession).count() == 2


@pytest.mark.parametrize('value', ['', '1.x', '0.'+'a'*64, '9'*19+'.'+'a'*64])
def test_invalid_capability_is_bounded(world, value):
    with pytest.raises(HTTPException):
        revoke_ticket(world, value, request(), Response())
    assert world.query(AuthSession).count() == 2


from test_provisional_stock_api import api_stock  # Existing synthetic HTTP fixture.


def test_http_ticket_cannot_authenticate_and_old_revoke_preserves_new_session(api_stock):
    from app.api.auth import router
    from app.core.auth import require_user, issue_session
    client, actor, _, _, _, _, db = api_stock
    client.app.include_router(router); client.app.dependency_overrides.pop(require_user)
    old, _ = issue_session(db, actor['id'])
    client.cookies.set('pu_session', old)
    response = client.get('/auth/logout-intent')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    ticket = response.json()['logout_ticket']
    new, _ = issue_session(db, actor['id'])
    client.cookies.set('pu_session', new)
    revoked = client.post('/auth/logout/revoke', json={'ticket': ticket})
    assert revoked.status_code == 200 and 'set-cookie' not in revoked.headers
    assert client.get('/auth/me').status_code == 200
    assert client.post('/auth/logout/revoke', json={'ticket': ticket}).status_code == 200
    assert db.query(AuthSession).count() == 1
    client.cookies.clear()
    assert client.get('/auth/me', headers={'Authorization':'Bearer '+ticket}).status_code == 401


def test_http_login_supplies_revoke_only_ticket_and_forgery_is_rejected(api_stock):
    from app.api.auth import router
    from app.core.auth import require_user, hash_password
    client, actor, _, _, _, _, db = api_stock
    user = db.get(User, actor['id']); user.password_hash = hash_password('SYNTHETIC-long-password')
    db.commit(); client.app.include_router(router); client.app.dependency_overrides.pop(require_user)
    login = client.post('/auth/login', json={'email':user.email, 'password':'SYNTHETIC-long-password'})
    assert login.status_code == 200
    ticket = login.json()['logout_ticket']
    forged = ticket.split('.')[0]+'.'+'0'*64
    assert client.post('/auth/logout/revoke', json={'ticket':forged}).status_code == 403
    assert db.query(AuthSession).count() == 1
    client.cookies.clear()
    assert client.post('/auth/logout/revoke', json={'ticket':ticket}).json() == {'status':'revoked'}
    assert db.query(AuthSession).count() == 0


def test_http_local_signout_marker_blocks_old_cookie_and_saved_bearer(api_stock):
    from app.api.auth import router
    from app.core.auth import require_user, issue_session
    client, actor, _, _, _, _, db = api_stock
    client.app.include_router(router); client.app.dependency_overrides.pop(require_user)
    raw, _ = issue_session(db, actor['id'])
    client.cookies.set('pu_session', raw)
    ticket = client.get('/auth/logout-intent').json()['logout_ticket']
    client.cookies.set('pu_local_signed_out', '1')
    assert client.get('/auth/me').status_code == 401
    assert client.get('/auth/me', headers={'Authorization':'Bearer '+raw}).status_code == 401
    assert db.query(AuthSession).count() == 1  # Fenced before deferred revocation.
    assert client.post('/auth/logout/revoke', json={'ticket':ticket}).status_code == 200
    assert db.query(AuthSession).count() == 0
