"""Add DB-backed authority epochs for the inactive v5.4 CONFIRM pilot.

Revision ID: a54f001c0a02
Revises: a54f001c0a01
No grants, production policy, credentials or user data are seeded.
"""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a02"
down_revision = "a54f001c0a01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "v54_authority_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("principal_kind", sa.String(length=16), nullable=False),
        sa.Column("principal_id", sa.String(length=100), nullable=False),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("membership_role", sa.String(length=50), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("authority_epoch", sa.Integer(), server_default="1", nullable=False),
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("principal_kind IN ('user','service')", name="ck_v54_authority_principal_kind"),
        sa.CheckConstraint("state IN ('active','revoked')", name="ck_v54_authority_state"),
        sa.CheckConstraint("authority_epoch > 0 AND record_version > 0", name="ck_v54_authority_versions"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"], ["projects.organization_id", "projects.id"],
            name="fk_v54_authority_project", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "project_id", "principal_kind", "principal_id", "scope",
            name="uq_v54_authority_principal_scope",
        ),
    )
    op.create_index(
        "ix_v54_authority_lookup", "v54_authority_states",
        ["organization_id", "project_id", "principal_kind", "principal_id", "scope"], unique=False,
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM v54_authority_states LIMIT 1)")):
        raise RuntimeError("Authority data exists; explicit archival is required")
    op.drop_index("ix_v54_authority_lookup", table_name="v54_authority_states")
    op.drop_table("v54_authority_states")
