"""FCM registration tokens of a user's devices (V6-04 push channel)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserDeviceToken(Base):
    """One FCM registration token, owned by exactly one user at a time.

    A token identifies an app install, not a person: when another user signs in
    on the same phone the existing row is re-bound to that user instead of
    keeping two owners for one device.
    """

    __tablename__ = "user_device_tokens"
    __table_args__ = (
        CheckConstraint("platform IN ('android')", name="ck_user_device_tokens_platform"),
        UniqueConstraint("fcm_token", name="uq_user_device_tokens_fcm_token"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Optional client-side install id; the FCM token alone is the address.
    device_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fcm_token: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="android", server_default="android")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
