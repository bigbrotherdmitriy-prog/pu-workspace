"""Revocation-only capabilities; never usable for authentication, no new secrets/schema."""
import hashlib
import hmac
import re

from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, select

from app.models.auth_session import AuthSession

_FORMAT = re.compile(r'([1-9][0-9]{0,18})\.([a-f0-9]{64})')


def _signature(row):
    return hmac.new(bytes.fromhex(row.token_hash),
                    f'pu.logout.revocation.v1:{row.id}'.encode(), hashlib.sha256).hexdigest()


def ticket_for_session(db, raw_token):
    row = db.scalar(select(AuthSession).where(
        AuthSession.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()))
    if row is None:
        raise HTTPException(401, 'Session unavailable')
    return f'{row.id}.{_signature(row)}'


def revoke_ticket(db, ticket: str, request: Request, response: Response):
    match = _FORMAT.fullmatch(ticket)
    if not match or int(match[1]) > 9223372036854775807:
        raise HTTPException(422, 'Invalid revocation capability')
    row = db.scalar(select(AuthSession).where(AuthSession.id == int(match[1])))
    # An already removed session is an idempotent success. Never clear ambient
    # cookies here: they might now belong to another session/account.
    if row is None:
        return {'status': 'revoked'}
    if not hmac.compare_digest(match[2], _signature(row)):
        raise HTTPException(403, 'Invalid revocation capability')
    db.execute(delete(AuthSession).where(AuthSession.id == row.id,
                                         AuthSession.token_hash == row.token_hash))
    db.commit()
    # No Set-Cookie: a delayed response must never clear a newer login's cookie.
    # Normal online /auth/logout remains responsible for cookie deletion.
    return {'status': 'revoked'}
