"""Explicit graph intent and edit CAS; legacy schedule values stay untouched."""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a20"
down_revision = "a54f001c0a19"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("schedule_baselines", sa.Column("graph_revision", sa.BigInteger(), nullable=False, server_default="1"))
    op.add_column("schedule_baselines", sa.Column("planning_mode", sa.String(20), nullable=False, server_default="dates_only"))
    op.add_column("schedule_baselines", sa.Column("project_start", sa.Date(), nullable=True))
    op.create_check_constraint("ck_schedule_graph_revision", "schedule_baselines", "graph_revision > 0")
    op.create_check_constraint("ck_schedule_graph_mode", "schedule_baselines", "planning_mode IN ('dates_only','calendar_graph')")
    op.create_check_constraint("ck_schedule_graph_anchor", "schedule_baselines", "planning_mode != 'calendar_graph' OR project_start IS NOT NULL")
    for name, datatype in (("duration_days", sa.Integer()), ("is_milestone", sa.Boolean()),
                           ("predecessor_ids", sa.String(2000)), ("constraint_type", sa.String(30)),
                           ("constraint_date", sa.Date()), ("not_before_date", sa.Date())):
        op.add_column("schedule_items", sa.Column(name, datatype, nullable=True))
    op.create_check_constraint("ck_schedule_duration_intent", "schedule_items",
        "(duration_days IS NULL AND is_milestone IS NULL) OR "
        "(duration_days IS NOT NULL AND is_milestone IS NOT NULL AND "
        "((is_milestone = true AND duration_days = 0) OR "
        "(is_milestone = false AND duration_days BETWEEN 1 AND 10000)))")
    op.create_check_constraint("ck_schedule_constraint_intent", "schedule_items",
        "(constraint_type IS NULL AND constraint_date IS NULL) OR "
        "(constraint_type IS NOT NULL AND ((constraint_type = 'asap' AND constraint_date IS NULL) OR "
        "(constraint_type IN ('snet','fnet','snlt','fnlt','mso','mfo') AND constraint_date IS NOT NULL)))")


def downgrade():
    # Refuse silent loss even in offline PostgreSQL migration scripts.
    op.execute("LOCK TABLE schedule_baselines, schedule_items IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS (
        SELECT 1 FROM schedule_baselines WHERE planning_mode = 'calendar_graph' OR project_start IS NOT NULL
    ) OR EXISTS (SELECT 1 FROM schedule_items WHERE duration_days IS NOT NULL OR is_milestone IS NOT NULL
        OR predecessor_ids IS NOT NULL OR constraint_type IS NOT NULL OR constraint_date IS NOT NULL
        OR not_before_date IS NOT NULL
    ) THEN RAISE EXCEPTION 'schedule_graph_downgrade_requires_verified_restore';
    END IF; END $$""")
    op.drop_constraint("ck_schedule_constraint_intent", "schedule_items", type_="check")
    op.drop_constraint("ck_schedule_duration_intent", "schedule_items", type_="check")
    for name in ("not_before_date", "constraint_date", "constraint_type", "predecessor_ids", "is_milestone", "duration_days"):
        op.drop_column("schedule_items", name)
    for name in ("ck_schedule_graph_anchor", "ck_schedule_graph_mode", "ck_schedule_graph_revision"):
        op.drop_constraint(name, "schedule_baselines", type_="check")
    for name in ("project_start", "planning_mode", "graph_revision"):
        op.drop_column("schedule_baselines", name)
