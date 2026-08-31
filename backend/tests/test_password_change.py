from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import func, select

import app.models  # noqa: F401 - register all mapped tables
from app.api.auth import PasswordChange, change_password
from app.core.auth import hash_password, verify_password
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.user import User


def test_password_change_revokes_sessions_and_records_safe_audit(db_session):
    db = db_session
    user = User(
            name="Администратор",
            email="admin@example.test",
            password_hash=hash_password("temporary-password-1"),
            is_admin=True,
    )
    db.add(user)
    db.flush()
    db.add(AuthSession(
            user_id=user.id,
            token_hash="a" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.commit()

    result = change_password(
            PasswordChange(
                current_password="temporary-password-1",
                new_password="permanent-password-2",
            ),
            Response(),
            db,
            user,
    )

    assert result == {"status": "password_changed", "reauthentication_required": True}
    assert verify_password("permanent-password-2", user.password_hash)
    assert not verify_password("temporary-password-1", user.password_hash)
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "password_changed"))
    assert audit is not None
    assert "sessions_revoked=1" in audit.details
    assert "permanent-password-2" not in audit.details


def test_password_change_rejects_wrong_current_password_without_mutation(db_session):
    db = db_session
    user = User(
            name="Администратор",
            email="admin@example.test",
            password_hash=hash_password("temporary-password-1"),
            is_admin=True,
    )
    db.add(user)
    db.commit()

    with pytest.raises(HTTPException) as failure:
        change_password(
                PasswordChange(
                    current_password="incorrect-password-1",
                    new_password="permanent-password-2",
                ),
                Response(),
                db,
                user,
        )

    assert failure.value.status_code == 401
    assert verify_password("temporary-password-1", user.password_hash)
