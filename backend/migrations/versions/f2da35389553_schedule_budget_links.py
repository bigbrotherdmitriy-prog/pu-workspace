"""Add schedule_budget_links (ADR-GPR-PER-CONTRACT-BUDGET-ALLOCATION-RU)."""

from alembic import op
import sqlalchemy as sa


revision = "f2da35389553"
down_revision = "fde452722a0f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schedule_budget_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("budget_line_id", sa.Integer(), sa.ForeignKey("budget_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schedule_item_id", sa.Integer(), sa.ForeignKey("schedule_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_schedule_budget_link_amount_positive"),
    )
    op.create_index("ix_schedule_budget_links_budget_line_id", "schedule_budget_links", ["budget_line_id"])
    op.create_index("ix_schedule_budget_links_schedule_item_id", "schedule_budget_links", ["schedule_item_id"])


def downgrade():
    # A brand-new, empty-by-default table: no backfill ever wrote into it, so
    # there is nothing pre-existing to protect. Any row here is itself real
    # allocation data entered after this migration -- still refuse to drop it
    # silently.
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM schedule_budget_links)")):
        raise RuntimeError("SCHEDULE_BUDGET_LINKS_DOWNGRADE_BLOCKED: preserve stage allocations")
    op.drop_index("ix_schedule_budget_links_schedule_item_id", table_name="schedule_budget_links")
    op.drop_index("ix_schedule_budget_links_budget_line_id", table_name="schedule_budget_links")
    op.drop_table("schedule_budget_links")
