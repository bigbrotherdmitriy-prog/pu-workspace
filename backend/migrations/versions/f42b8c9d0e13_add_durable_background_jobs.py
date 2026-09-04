"""add durable background jobs

Revision ID: f42b8c9d0e13
Revises: e31a7b8c9d02
"""
from alembic import op
import sqlalchemy as sa

revision = "f42b8c9d0e13"
down_revision = "e31a7b8c9d02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.String(255)),
        sa.Column("idempotency_key", sa.String(255), unique=True),
        sa.Column("result", sa.JSON()),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_background_jobs_kind", "background_jobs", ["kind"])
    op.create_index("ix_background_jobs_status", "background_jobs", ["status"])
    op.create_index("ix_background_jobs_lease_expires_at", "background_jobs", ["lease_expires_at"])
    op.create_index("ix_background_jobs_claim", "background_jobs", ["status", "available_at", "priority", "id"])
    op.create_table(
        "service_heartbeats",
        sa.Column("service_id", sa.String(255), primary_key=True),
        sa.Column("service_kind", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_service_heartbeats_service_kind", "service_heartbeats", ["service_kind"])
    op.create_index("ix_service_heartbeats_last_seen", "service_heartbeats", ["last_seen"])


def downgrade():
    op.drop_table("service_heartbeats")
    op.drop_table("background_jobs")
