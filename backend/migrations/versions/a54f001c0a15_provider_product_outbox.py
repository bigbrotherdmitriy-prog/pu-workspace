"""Allow explicitly approved Google Workspace provider actions.

Revision ID: a54f001c0a15
Revises: a54f001c0a14
"""
from alembic import op


revision = "a54f001c0a15"
down_revision = "a54f001c0a14"
branch_labels = None
depends_on = None


_PRODUCT_POLICY = (
    "mode = 'CONFIRM' AND ((synthetic_only = true AND provider = 'synthetic' "
    "AND action_kind LIKE 'synthetic.%') OR (synthetic_only = false "
    "AND provider = 'google_workspace' AND action_kind IN "
    "('gmail.message.send','google.tasks.upsert','google.calendar.upsert')))"
)


def upgrade():
    op.drop_constraint(
        "ck_v54_provider_confirm_synthetic",
        "v54_provider_actions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_v54_provider_confirm_synthetic",
        "v54_provider_actions",
        _PRODUCT_POLICY,
    )


def downgrade():
    op.drop_constraint(
        "ck_v54_provider_confirm_synthetic",
        "v54_provider_actions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_v54_provider_confirm_synthetic",
        "v54_provider_actions",
        "mode = 'CONFIRM' AND synthetic_only = true AND provider = 'synthetic'",
    )
