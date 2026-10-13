"""Device registration for the FCM push channel (V6-04).

The Android wrapper hands its FCM token to the SPA, which registers it here
under the signed-in user.  The token is only an address; it grants no access.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.database import get_db
from app.models.notification_device import UserDeviceToken
from app.models.user import User

router = APIRouter(prefix="/api/notifications/devices", tags=["notification-devices"])

# Bounded per user: a phone reinstall issues a new token while the old one
# lingers until FCM reports it UNREGISTERED.
MAX_DEVICES_PER_USER = 10
# FCM registration tokens are URL-safe base64 with ':' separators.
_TOKEN_PATTERN = r"^[A-Za-z0-9_:\-]+$"


class DeviceRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fcm_token: str = Field(min_length=20, max_length=512, pattern=_TOKEN_PATTERN)
    device_id: str | None = Field(default=None, min_length=1, max_length=100)
    platform: Literal["android"] = "android"


class DeviceRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fcm_token: str = Field(min_length=1, max_length=512)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _locked_token(db: Session, fcm_token: str) -> UserDeviceToken | None:
    return db.scalar(select(UserDeviceToken).where(UserDeviceToken.fcm_token == fcm_token).with_for_update())


def _prune_old_devices(db: Session, user_id: int) -> None:
    stale = list(db.scalars(
        select(UserDeviceToken.id).where(UserDeviceToken.user_id == user_id)
        .order_by(UserDeviceToken.updated_at.desc(), UserDeviceToken.id.desc())
        .offset(MAX_DEVICES_PER_USER)
    ))
    if stale:
        db.execute(delete(UserDeviceToken).where(UserDeviceToken.id.in_(stale)))


@router.post("/register")
def register_device(payload: DeviceRegistration, db: Session = Depends(get_db),
                    user: User = Depends(require_user)):
    now = _utcnow()
    row = _locked_token(db, payload.fcm_token)
    if row is None:
        row = UserDeviceToken(user_id=user.id, fcm_token=payload.fcm_token, device_id=payload.device_id,
                              platform=payload.platform, created_at=now, updated_at=now)
        try:
            with db.begin_nested():
                db.add(row); db.flush()
        except IntegrityError:
            # A concurrent registration of the same token won the insert.
            row = _locked_token(db, payload.fcm_token)
            if row is None:
                raise
    # The same install signed in by another user now belongs to that user only.
    row.user_id, row.platform, row.updated_at = user.id, payload.platform, now
    if payload.device_id is not None:
        row.device_id = payload.device_id
    db.flush()
    _prune_old_devices(db, user.id)
    db.commit()
    return {"registered": True}


@router.post("/revoke")
def revoke_device(payload: DeviceRevocation, db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    # Only the current owner may drop a token; a foreign token is a silent no-op.
    result = db.execute(delete(UserDeviceToken).where(
        UserDeviceToken.fcm_token == payload.fcm_token, UserDeviceToken.user_id == user.id,
    ))
    db.commit()
    return {"revoked": bool(result.rowcount)}
