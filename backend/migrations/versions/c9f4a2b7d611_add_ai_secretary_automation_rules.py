"""add AI Secretary automation rules

Revision ID: c9f4a2b7d611
Revises: b84d6e2f190a
"""
from alembic import op
import sqlalchemy as sa


revision = "c9f4a2b7d611"
down_revision = "b84d6e2f190a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("response_drafts", sa.Column("recipient_to", sa.String(1000), nullable=True))
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_id", sa.Integer(), sa.ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False, server_default="monthly_email"),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("recipient_to", sa.String(1000), nullable=False),
        sa.Column("subject_template", sa.String(500), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("task_title_template", sa.String(500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_on", sa.Date(), nullable=False),
        sa.Column("last_run_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("project_id", "contract_id", "source_document_id", "kind", "active", "next_run_on"):
        op.create_index(f"ix_automation_rules_{column}", "automation_rules", [column])
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("automation_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("response_draft_id", sa.Integer(), sa.ForeignKey("response_drafts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="prepared"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("rule_id", "scheduled_for", name="uq_automation_run_schedule"),
    )
    for column in ("rule_id", "scheduled_for", "status"):
        op.create_index(f"ix_automation_runs_{column}", "automation_runs", [column])


def downgrade():
    op.drop_table("automation_runs")
    op.drop_table("automation_rules")
    op.drop_column("response_drafts", "recipient_to")
