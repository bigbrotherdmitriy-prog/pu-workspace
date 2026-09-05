"""Add durable management digest preferences and proposal origins.

Revision ID: a54f001c0a17
Revises: a54f001c0a16
"""

from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a17"
down_revision = "a54f001c0a16"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "management_digest_preferences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("quiet_start", sa.Time(), nullable=False),
        sa.Column("quiet_end", sa.Time(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("cadence", sa.String(20), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "user_id", name="uq_management_digest_preference_scope"),
        sa.CheckConstraint("record_version > 0", name="ck_management_digest_preference_version"),
        sa.CheckConstraint("channel IN ('in_app','disabled')", name="ck_management_digest_preference_channel"),
        sa.CheckConstraint("cadence IN ('daily','weekdays')", name="ck_management_digest_preference_cadence"),
    )
    op.create_index(
        "ix_management_digest_preferences_project_id",
        "management_digest_preferences",
        ["project_id"],
    )
    op.create_index(
        "ix_management_digest_preferences_user_id",
        "management_digest_preferences",
        ["user_id"],
    )

    op.create_table(
        "management_proposal_origins",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin_type", sa.String(20), nullable=False),
        sa.Column("origin_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("proposal_kind", sa.String(20), nullable=False),
        sa.Column("evidence_pins", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "project_id", "origin_type", "origin_id", "entity_type", "entity_id",
            name="uq_management_proposal_origin_target",
        ),
        sa.CheckConstraint("origin_type IN ('meeting','message')", name="ck_management_proposal_origin_type"),
        sa.CheckConstraint("entity_type IN ('obligation','decision')", name="ck_management_proposal_entity_type"),
        sa.CheckConstraint("proposal_kind IN ('obligation','task','decision')", name="ck_management_proposal_kind"),
    )
    for column in ("project_id", "origin_type", "origin_id", "entity_id", "created_by_user_id"):
        op.create_index(
            f"ix_management_proposal_origins_{column}",
            "management_proposal_origins",
            [column],
        )


def downgrade():
    op.drop_table("management_proposal_origins")
    op.drop_table("management_digest_preferences")
