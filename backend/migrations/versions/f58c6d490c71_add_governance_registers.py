"""add governance registers

Revision ID: f58c6d490c71
Revises: e47b9c128a55
"""
from alembic import op
import sqlalchemy as sa

revision = "f58c6d490c71"
down_revision = "e47b9c128a55"
branch_labels = None
depends_on = None


def _source_columns():
    return [
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(500), nullable=False),
        sa.Column("source_name", sa.String(1000), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "risks", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False), sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False), sa.Column("criticality", sa.String(30), nullable=False),
        sa.Column("status", sa.String(50), nullable=False), sa.Column("action_note", sa.Text(), nullable=True),
        *_source_columns(), sa.UniqueConstraint("project_id", "source_hash", name="uq_risk_source_hash"),
    )
    op.create_index("ix_risks_project_id", "risks", ["project_id"])
    op.create_index("ix_risks_owner_user_id", "risks", ["owner_user_id"])
    op.create_index("ix_risks_status", "risks", ["status"])
    op.create_index("ix_risks_kind", "risks", ["kind"])
    op.create_index("ix_risks_criticality", "risks", ["criticality"])
    op.create_table(
        "decisions", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("initiator_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False), sa.Column("options", sa.Text(), nullable=True),
        sa.Column("decision_text", sa.Text(), nullable=True), sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False), *_source_columns(),
        sa.UniqueConstraint("project_id", "source_hash", name="uq_decision_source_hash"),
    )
    op.create_index("ix_decisions_project_id", "decisions", ["project_id"])
    op.create_index("ix_decisions_initiator_user_id", "decisions", ["initiator_user_id"])
    op.create_index("ix_decisions_status", "decisions", ["status"])


def downgrade():
    op.drop_table("decisions")
    op.drop_table("risks")
