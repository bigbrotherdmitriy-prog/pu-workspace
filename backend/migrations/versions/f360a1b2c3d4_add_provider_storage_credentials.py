"""add provider-neutral storage credentials and project locator metadata

Revision ID: f360a1b2c3d4
Revises: c83d0a24b512
"""
from alembic import op
import sqlalchemy as sa

revision = "f360a1b2c3d4"
down_revision = "c83d0a24b512"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("drive_connections", sa.Column("connection_id", sa.String(255), nullable=True))
    op.add_column("drive_connections", sa.Column("root_display_name", sa.String(500), nullable=True))
    op.add_column("drive_connections", sa.Column("sync_settings", sa.Text(), server_default="{}", nullable=False))
    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("capability", sa.String(50), server_default="storage", nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_uri", sa.String(500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("account_external_id", sa.String(255), nullable=True),
        sa.Column("account_email", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "provider", "capability", name="uq_integration_credential"),
    )
    op.create_index("ix_integration_credentials_project_id", "integration_credentials", ["project_id"])
    op.create_index("ix_integration_credentials_provider", "integration_credentials", ["provider"])


def downgrade():
    op.drop_index("ix_integration_credentials_provider", table_name="integration_credentials")
    op.drop_index("ix_integration_credentials_project_id", table_name="integration_credentials")
    op.drop_table("integration_credentials")
    op.drop_column("drive_connections", "sync_settings")
    op.drop_column("drive_connections", "root_display_name")
    op.drop_column("drive_connections", "connection_id")
