"""Add lossless WBS hierarchy and derived summary intent."""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a21"
down_revision = "a54f001c0a20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("schedule_items", sa.Column("wbs_parent_id", sa.Integer(), nullable=True))
    op.add_column("schedule_items", sa.Column("wbs_order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("schedule_items", sa.Column("is_summary", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_foreign_key("fk_schedule_items_wbs_parent", "schedule_items", "schedule_items",
                          ["wbs_parent_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_schedule_items_wbs_parent_id", "schedule_items", ["wbs_parent_id"])
    op.create_check_constraint("ck_schedule_wbs_order", "schedule_items", "wbs_order >= 0")
    op.create_check_constraint("ck_schedule_summary_intent", "schedule_items",
        "is_summary = false OR (duration_days IS NULL AND is_milestone IS NULL "
        "AND predecessor_ids IS NULL AND constraint_type IS NULL AND constraint_date IS NULL "
        "AND not_before_date IS NULL)")


def downgrade():
    op.execute("LOCK TABLE schedule_items IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS (
        SELECT 1 FROM schedule_items
        WHERE wbs_parent_id IS NOT NULL OR wbs_order != 0 OR is_summary = true
    ) THEN RAISE EXCEPTION 'schedule_wbs_downgrade_requires_verified_restore';
    END IF; END $$""")
    op.drop_constraint("ck_schedule_summary_intent", "schedule_items", type_="check")
    op.drop_constraint("ck_schedule_wbs_order", "schedule_items", type_="check")
    op.drop_index("ix_schedule_items_wbs_parent_id", table_name="schedule_items")
    op.drop_constraint("fk_schedule_items_wbs_parent", "schedule_items", type_="foreignkey")
    for name in ("is_summary", "wbs_order", "wbs_parent_id"):
        op.drop_column("schedule_items", name)
