"""Add project_service_settings and backfill from Google OAuth tokens."""

from alembic import op
import sqlalchemy as sa


revision = "d3e5f6a7b8c9"
down_revision = "c2b8d4e6f7a9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_service_settings",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("gmail_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("google_tasks_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("google_calendar_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("primary_mail", sa.String(20), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.execute(
        "INSERT INTO project_service_settings "
        "(project_id, gmail_enabled, google_tasks_enabled, google_calendar_enabled, primary_mail) "
        "SELECT project_id, TRUE, TRUE, TRUE, 'gmail' FROM google_oauth_tokens"
    )


def downgrade():
    op.drop_table("project_service_settings")
