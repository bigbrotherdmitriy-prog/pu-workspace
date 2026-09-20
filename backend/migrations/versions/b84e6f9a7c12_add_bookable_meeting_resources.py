"""Add tenant-scoped bookable meeting resources.

Revision ID: b84e6f9a7c12
Revises: a72d4e6f8b91
"""

from alembic import op
import sqlalchemy as sa


revision = "b84e6f9a7c12"
down_revision = "a72d4e6f8b91"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bookable_resources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "organization_id", sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("managing_project_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_by_user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False, server_default="Europe/Moscow"),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("record_version > 0", name="ck_bookable_resources_record_version"),
        sa.CheckConstraint(
            "kind IN ('room', 'equipment', 'other')",
            name="ck_bookable_resources_kind",
        ),
        sa.CheckConstraint(
            "capacity IS NULL OR capacity > 0",
            name="ck_bookable_resources_capacity",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "managing_project_id"],
            ["projects.organization_id", "projects.id"],
            name="fk_bookable_resources_managing_project_scope",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_bookable_resources_organization_id", "bookable_resources", ["organization_id"])
    op.create_index("ix_bookable_resources_managing_project_id", "bookable_resources", ["managing_project_id"])
    op.create_index("ix_bookable_resources_active", "bookable_resources", ["active"])
    op.create_table(
        "meeting_resources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "meeting_id", sa.Integer(),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "resource_id", sa.Integer(),
            sa.ForeignKey("bookable_resources.id", ondelete="RESTRICT"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("meeting_id", "resource_id", name="uq_meeting_resource"),
    )
    op.create_index("ix_meeting_resources_meeting_id", "meeting_resources", ["meeting_id"])
    op.create_index("ix_meeting_resources_resource_id", "meeting_resources", ["resource_id"])


def downgrade():
    op.drop_table("meeting_resources")
    op.drop_table("bookable_resources")
