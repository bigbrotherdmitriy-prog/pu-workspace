from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IntegrationCredential(Base):
    """Encrypted credentials for non-legacy provider adapters.

    Google continues to use GoogleOAuthToken so existing rows and OAuth sessions
    are not migrated or refreshed as a side effect of this feature.
    """

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint("project_id", "provider", "capability", name="uq_integration_credential"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    capability: Mapped[str] = mapped_column(String(50), default="storage")
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
