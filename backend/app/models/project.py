from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            "(site_latitude IS NULL AND site_longitude IS NULL AND site_geofence_radius_m IS NULL) OR "
            "(site_latitude IS NOT NULL AND site_longitude IS NOT NULL AND site_geofence_radius_m IS NOT NULL)",
            name="ck_projects_site_location_complete",
        ),
        CheckConstraint("site_latitude IS NULL OR site_latitude BETWEEN -90 AND 90", name="ck_projects_site_latitude"),
        CheckConstraint("site_longitude IS NULL OR site_longitude BETWEEN -180 AND 180", name="ck_projects_site_longitude"),
        CheckConstraint(
            "site_geofence_radius_m IS NULL OR site_geofence_radius_m BETWEEN 25 AND 5000",
            name="ck_projects_site_radius",
        ),
        CheckConstraint(
            "site_accuracy_m IS NULL OR site_accuracy_m BETWEEN 0 AND 100000",
            name="ck_projects_site_accuracy",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    site_label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    site_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    site_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    site_geofence_radius_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    site_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    site_location_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
