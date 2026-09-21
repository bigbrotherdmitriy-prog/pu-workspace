"""Allow the closed internal notification AUTO action.

Revision ID: a54f001c0a10
Revises: e26b8d0f3c51
"""
from alembic import context, op
import sqlalchemy as sa


revision = "a54f001c0a10"
down_revision = "e26b8d0f3c51"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_v54_action_type", "v54_actions", type_="check")
    op.create_check_constraint(
        "ck_v54_action_type",
        "v54_actions",
        "action_type IN ('task.internal.create','task.internal.cancel','notification.internal.create')",
    )


def downgrade():
    message = "Internal notification actions must be archived before downgrade"
    check = sa.text(
        "SELECT EXISTS (SELECT 1 FROM v54_actions "
        "WHERE action_type = 'notification.internal.create' LIMIT 1)"
    )
    if context.is_offline_mode():
        op.execute(sa.text(f"""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM v54_actions
                           WHERE action_type = 'notification.internal.create' LIMIT 1)
                THEN RAISE EXCEPTION '{message}'; END IF;
            END $$
        """))
    elif op.get_bind().scalar(check):
        raise RuntimeError(message)
    op.drop_constraint("ck_v54_action_type", "v54_actions", type_="check")
    op.create_check_constraint(
        "ck_v54_action_type", "v54_actions",
        "action_type IN ('task.internal.create','task.internal.cancel')",
    )
