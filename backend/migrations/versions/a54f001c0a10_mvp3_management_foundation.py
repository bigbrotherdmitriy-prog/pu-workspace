"""Evidence-backed MVP3 lifecycle foundation.

Revision ID: a54f001c0a10
Revises: a54f001c0a09
"""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a10"
down_revision = "a54f001c0a09"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("obligations", sa.Column("record_version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("obligations", sa.Column("due_time", sa.Time(), nullable=True))
    op.add_column("obligations", sa.Column("timezone", sa.String(100), server_default="Europe/Moscow", nullable=False))
    op.add_column("obligations", sa.Column("deadline_policy", sa.JSON(), nullable=True))
    op.add_column("obligations", sa.Column("escalation_level", sa.Integer(), server_default="0", nullable=False))
    op.add_column("obligations", sa.Column("last_escalated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("obligations", sa.Column("evidence_pins", sa.JSON(), nullable=True))
    op.add_column("obligations", sa.Column("review_state", sa.String(30), server_default="unverified", nullable=False))
    op.create_index("ix_obligations_review_state", "obligations", ["review_state"])
    op.create_check_constraint("ck_obligation_lifecycle_version", "obligations", "record_version > 0 AND escalation_level >= 0")
    op.create_check_constraint("ck_obligation_review_state", "obligations", "review_state IN ('unverified','needs_review','verified')")

    for table in ("risks", "decisions"):
        op.add_column(table, sa.Column("record_version", sa.Integer(), server_default="1", nullable=False))
        op.add_column(table, sa.Column("obligation_id", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("task_id", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("evidence_pins", sa.JSON(), nullable=True))
        op.add_column(table, sa.Column("review_state", sa.String(30), server_default="unverified", nullable=False))
        op.create_foreign_key(f"fk_{table}_obligation", table, "obligations", ["obligation_id"], ["id"], ondelete="SET NULL")
        op.create_foreign_key(f"fk_{table}_task", table, "tasks", ["task_id"], ["id"], ondelete="SET NULL")
        op.create_index(f"ix_{table}_obligation_id", table, ["obligation_id"])
        op.create_index(f"ix_{table}_task_id", table, ["task_id"])
        op.create_index(f"ix_{table}_review_state", table, ["review_state"])
        op.create_check_constraint(f"ck_{table[:-1]}_record_version", table, "record_version > 0")
        op.create_check_constraint(f"ck_{table[:-1]}_review_state", table, "review_state IN ('unverified','needs_review','verified')")
    op.add_column("decisions", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.add_column("decisions", sa.Column("risk_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_decisions_owner", "decisions", "users", ["owner_user_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_decisions_risk", "decisions", "risks", ["risk_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_decisions_owner_user_id", "decisions", ["owner_user_id"])
    op.create_index("ix_decisions_risk_id", "decisions", ["risk_id"])

    op.create_table(
        "obligation_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("obligation_id", sa.Integer(), sa.ForeignKey("obligations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("event", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(40)), sa.Column("to_status", sa.String(40), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.Text()), sa.Column("evidence_pins", sa.JSON()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("obligation_id", "sequence", name="uq_obligation_history_sequence"),
    )
    op.create_index("ix_obligation_history_obligation_id", "obligation_history", ["obligation_id"])
    op.create_index("ix_obligation_history_project_id", "obligation_history", ["project_id"])
    op.create_index("ix_obligation_history_actor_user_id", "obligation_history", ["actor_user_id"])

    op.create_table(
        "governance_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False), sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("event", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(50)), sa.Column("to_status", sa.String(50), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.Text()), sa.Column("evidence_pins", sa.JSON()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_id", "sequence", name="uq_governance_history_sequence"),
    )
    op.create_index("ix_governance_history_project_id", "governance_history", ["project_id"])
    op.create_index("ix_governance_history_entity_type", "governance_history", ["entity_type"])
    op.create_index("ix_governance_history_entity_id", "governance_history", ["entity_id"])
    op.create_index("ix_governance_history_actor_user_id", "governance_history", ["actor_user_id"])


def downgrade():
    op.drop_table("governance_history")
    op.drop_table("obligation_history")
    op.drop_index("ix_decisions_risk_id", table_name="decisions")
    op.drop_index("ix_decisions_owner_user_id", table_name="decisions")
    op.drop_constraint("fk_decisions_risk", "decisions", type_="foreignkey")
    op.drop_constraint("fk_decisions_owner", "decisions", type_="foreignkey")
    op.drop_column("decisions", "risk_id"); op.drop_column("decisions", "owner_user_id")
    for table in ("decisions", "risks"):
        op.drop_constraint(f"ck_{table[:-1]}_review_state", table, type_="check")
        op.drop_constraint(f"ck_{table[:-1]}_record_version", table, type_="check")
        op.drop_index(f"ix_{table}_review_state", table_name=table)
        op.drop_index(f"ix_{table}_task_id", table_name=table)
        op.drop_index(f"ix_{table}_obligation_id", table_name=table)
        op.drop_constraint(f"fk_{table}_task", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_obligation", table, type_="foreignkey")
        for column in ("review_state", "evidence_pins", "task_id", "obligation_id", "record_version"):
            op.drop_column(table, column)
    op.drop_index("ix_obligations_review_state", table_name="obligations")
    op.drop_constraint("ck_obligation_review_state", "obligations", type_="check")
    op.drop_constraint("ck_obligation_lifecycle_version", "obligations", type_="check")
    for column in ("review_state", "evidence_pins", "last_escalated_at", "escalation_level", "deadline_policy",
                   "timezone", "due_time", "record_version"):
        op.drop_column("obligations", column)
