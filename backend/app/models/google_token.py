from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GoogleOAuthToken(Base):
    __tablename__ = "google_oauth_tokens"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    # Advances on explicit OAuth reconnect, not on routine access-token refresh.
    credential_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    access_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    refresh_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    token_uri: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="https://oauth2.googleapis.com/token",
    )

    scopes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
