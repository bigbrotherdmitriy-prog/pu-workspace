"""add project site location

Revision ID: b42d7f9a1c35
Revises: b01c9a7d4e21
"""
from alembic import op
import sqlalchemy as sa


revision = "b42d7f9a1c35"
down_revision = "b01c9a7d4e21"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("site_label", sa.String(length=500), nullable=True))
    op.add_column("projects", sa.Column("site_latitude", sa.Float(), nullable=True))
    op.add_column("projects", sa.Column("site_longitude", sa.Float(), nullable=True))
    op.add_column("projects", sa.Column("site_geofence_radius_m", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("site_accuracy_m", sa.Float(), nullable=True))
    op.add_column("projects", sa.Column("site_location_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_projects_site_location_complete",
        "projects",
        "(site_latitude IS NULL AND site_longitude IS NULL AND site_geofence_radius_m IS NULL) OR "
        "(site_latitude IS NOT NULL AND site_longitude IS NOT NULL AND site_geofence_radius_m IS NOT NULL)",
    )
    op.create_check_constraint("ck_projects_site_latitude", "projects", "site_latitude IS NULL OR site_latitude BETWEEN -90 AND 90")
    op.create_check_constraint("ck_projects_site_longitude", "projects", "site_longitude IS NULL OR site_longitude BETWEEN -180 AND 180")
    op.create_check_constraint(
        "ck_projects_site_radius",
        "projects",
        "site_geofence_radius_m IS NULL OR site_geofence_radius_m BETWEEN 25 AND 5000",
    )
    op.create_check_constraint(
        "ck_projects_site_accuracy",
        "projects",
        "site_accuracy_m IS NULL OR site_accuracy_m BETWEEN 0 AND 100000",
    )


def downgrade():
    op.drop_constraint("ck_projects_site_accuracy", "projects", type_="check")
    op.drop_constraint("ck_projects_site_radius", "projects", type_="check")
    op.drop_constraint("ck_projects_site_longitude", "projects", type_="check")
    op.drop_constraint("ck_projects_site_latitude", "projects", type_="check")
    op.drop_constraint("ck_projects_site_location_complete", "projects", type_="check")
    op.drop_column("projects", "site_location_updated_at")
    op.drop_column("projects", "site_accuracy_m")
    op.drop_column("projects", "site_geofence_radius_m")
    op.drop_column("projects", "site_longitude")
    op.drop_column("projects", "site_latitude")
    op.drop_column("projects", "site_label")
