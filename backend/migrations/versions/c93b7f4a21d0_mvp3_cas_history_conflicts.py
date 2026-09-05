"""MVP3 CAS, append-only history and contact conflicts.

Revision ID: c93b7f4a21d0
Revises: e75c4a1d9f20
"""

from alembic import op
import sqlalchemy as sa


revision = "c93b7f4a21d0"
down_revision = "e75c4a1d9f20"
branch_labels = None
depends_on = None


VERSIONED = ("obligations", "meetings", "notifications", "risks", "decisions", "project_contacts")


def upgrade():
    for table in VERSIONED:
        op.add_column(table, sa.Column("record_version", sa.Integer(), server_default="1", nullable=False))
        op.create_check_constraint(f"ck_{table}_record_version", table, "record_version > 0")
    # Task already gained the column in the v5.4 foundation; enforce its invariant here.
    op.create_check_constraint("ck_tasks_record_version", "tasks", "record_version > 0")

    op.create_table(
        "management_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("old_values", sa.JSON(), nullable=False),
        sa.Column("new_values", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("record_version > 0", name="ck_management_history_record_version"),
    )
    for column in ("organization_id", "project_id", "entity_type", "entity_id", "actor_user_id", "created_at"):
        op.create_index(f"ix_management_history_{column}", "management_history", [column])

    op.create_table(
        "contact_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("record_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("project_contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("normalized_email", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), server_default="pending", nullable=False),
        sa.Column("resolution", sa.String(50), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("record_version > 0", name="ck_contact_conflicts_record_version"),
    )
    for column in ("organization_id", "contact_id", "current_project_id", "candidate_project_id", "normalized_email", "status"):
        op.create_index(f"ix_contact_conflicts_{column}", "contact_conflicts", [column])


def downgrade():
    op.drop_table("contact_conflicts")
    op.drop_table("management_history")
    op.drop_constraint("ck_tasks_record_version", "tasks", type_="check")
    for table in reversed(VERSIONED):
        op.drop_constraint(f"ck_{table}_record_version", table, type_="check")
        op.drop_column(table, "record_version")
