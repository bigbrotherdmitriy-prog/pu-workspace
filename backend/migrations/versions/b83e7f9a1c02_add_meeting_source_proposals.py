"""Add exact meeting source bindings and reviewable proposals.

Revision ID: b83e7f9a1c02
Revises: a72d4e6f8b91
"""

from alembic import op
import sqlalchemy as sa


revision = "b83e7f9a1c02"
down_revision = "a72d4e6f8b91"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meeting_source_bindings",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meetings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("meeting_record_version", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("source_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("evidence_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("materialization_id", sa.Uuid(as_uuid=False), sa.ForeignKey("v54_materializations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("command_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("bound_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id", "source_version_id"],
            ["v54_source_versions.organization_id", "v54_source_versions.source_id", "v54_source_versions.id"],
            name="fk_meeting_source_binding_observation", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "evidence_id", "source_id", "source_version_id"],
            ["v54_evidence.organization_id", "v54_evidence.id", "v54_evidence.source_id", "v54_evidence.source_version_id"],
            name="fk_meeting_source_binding_evidence", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("meeting_id", "meeting_record_version", name="uq_meeting_source_binding_version"),
        sa.UniqueConstraint("meeting_id", "command_id", name="uq_meeting_source_binding_command"),
        sa.CheckConstraint("meeting_record_version > 1", name="ck_meeting_source_binding_version"),
    )
    for column in ("organization_id", "project_id", "meeting_id"):
        op.create_index(f"ix_meeting_source_bindings_{column}", "meeting_source_bindings", [column])

    op.create_table(
        "meeting_proposals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("record_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("meeting_id", sa.Integer(), sa.ForeignKey("meetings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("binding_id", sa.Uuid(as_uuid=False), sa.ForeignKey("meeting_source_bindings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("proposal_type", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("confirmation_command_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("confirmation_hash", sa.String(64), nullable=True),
        sa.Column("target_entity_type", sa.String(20), nullable=True),
        sa.Column("target_entity_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("binding_id", "fingerprint", name="uq_meeting_proposal_fingerprint"),
        sa.CheckConstraint("record_version > 0", name="ck_meeting_proposal_version"),
        sa.CheckConstraint("proposal_type IN ('task','risk','decision')", name="ck_meeting_proposal_type"),
        sa.CheckConstraint("status IN ('proposed','confirmed')", name="ck_meeting_proposal_status"),
    )
    for column in ("organization_id", "project_id", "meeting_id", "binding_id", "proposal_type", "status"):
        op.create_index(f"ix_meeting_proposals_{column}", "meeting_proposals", [column])


def downgrade():
    op.drop_table("meeting_proposals")
    op.drop_table("meeting_source_bindings")
