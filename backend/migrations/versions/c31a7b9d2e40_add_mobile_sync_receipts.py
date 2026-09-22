"""Add durable Android/PWA offline-sync receipts and conflicts.

Revision ID: c31a7b9d2e40
Revises: b17c4d2e6f90
"""

from alembic import op
import sqlalchemy as sa


revision = "c31a7b9d2e40"
down_revision = "b17c4d2e6f90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mobile_sync_commands",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("client_mutation_id", sa.String(length=100), nullable=False),
        sa.Column("device_id", sa.String(length=100), nullable=False),
        sa.Column("operation", sa.String(length=50), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("base_record_version", sa.Integer(), nullable=True),
        sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "organization_id", "user_id", "client_mutation_id",
            name="uq_mobile_sync_command_identity",
        ),
        sa.CheckConstraint(
            "status IN ('processing','applied','conflict','rejected')",
            name="ck_mobile_sync_commands_status",
        ),
    )
    op.create_index("ix_mobile_sync_commands_organization_id", "mobile_sync_commands", ["organization_id"])
    op.create_index("ix_mobile_sync_commands_project_id", "mobile_sync_commands", ["project_id"])
    op.create_index("ix_mobile_sync_commands_user_id", "mobile_sync_commands", ["user_id"])

    op.create_table(
        "mobile_sync_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("command_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("base_values", sa.JSON(), nullable=False),
        sa.Column("local_values", sa.JSON(), nullable=False),
        sa.Column("server_values", sa.JSON(), nullable=False),
        sa.Column("conflicting_fields", sa.JSON(), nullable=False),
        sa.Column("server_record_version", sa.Integer(), nullable=False),
        sa.Column("server_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("resolution", sa.String(length=30), nullable=True),
        sa.Column("resolved_values", sa.JSON(), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["command_id"], ["mobile_sync_commands.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("command_id", name="uq_mobile_sync_conflict_command"),
        sa.CheckConstraint("status IN ('unresolved','resolved')", name="ck_mobile_sync_conflicts_status"),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN "
            "('keep_server','apply_local','latest_write_wins')",
            name="ck_mobile_sync_conflicts_resolution",
        ),
    )
    op.create_index("ix_mobile_sync_conflicts_command_id", "mobile_sync_conflicts", ["command_id"])
    op.create_index("ix_mobile_sync_conflicts_organization_id", "mobile_sync_conflicts", ["organization_id"])
    op.create_index("ix_mobile_sync_conflicts_project_id", "mobile_sync_conflicts", ["project_id"])
    op.create_index("ix_mobile_sync_conflicts_user_id", "mobile_sync_conflicts", ["user_id"])


def downgrade():
    op.drop_table("mobile_sync_conflicts")
    op.drop_table("mobile_sync_commands")
