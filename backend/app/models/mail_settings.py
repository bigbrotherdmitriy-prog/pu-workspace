from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MailUserSettings(Base):
    __tablename__ = "mail_user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    display_name: Mapped[str] = mapped_column(String(255), default="", server_default="")
    signature_html: Mapped[str] = mapped_column(Text, default="", server_default="")
    auto_signature_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    auto_signature_reply: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    default_font: Mapped[str] = mapped_column(String(50), default="Arial", server_default="Arial")
    default_font_size: Mapped[str] = mapped_column(String(10), default="14px", server_default="14px")
    default_text_color: Mapped[str] = mapped_column(String(20), default="#18211d", server_default="#18211d")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
