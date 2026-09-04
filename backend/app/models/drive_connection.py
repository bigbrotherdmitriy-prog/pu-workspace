from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DriveConnection(Base):
    __tablename__ = "drive_connections"

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            name="uq_drive_connection_project",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="google_drive",
    )

    account_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    root_folder_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    connection_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    root_display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sync_settings: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="connected",
    )
