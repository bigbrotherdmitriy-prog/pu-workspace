"""Permit CONFIRM-only Google Workspace effects in the existing provider ledger.

Revision ID: e73c2b4a901d
Revises: d29a6c4f1e83
"""
from alembic import op
import sqlalchemy as sa

revision = "e73c2b4a901d"
down_revision = "d29a6c4f1e83"
branch_labels = None
depends_on = None

_PRODUCT_POLICY = (
    "mode = 'CONFIRM' AND ((synthetic_only = true AND provider = 'synthetic' "
    "AND action_kind LIKE 'synthetic.%') OR (synthetic_only = false "
    "AND provider = 'google_workspace' AND action_kind IN "
    "('gmail.message.send','google.tasks.upsert','google.calendar.upsert')))"
)


def upgrade():
    op.add_column("google_oauth_tokens", sa.Column("credential_generation", sa.Integer(),
                                                  nullable=False, server_default="1"))
    op.drop_constraint("ck_v54_provider_confirm_synthetic", "v54_provider_actions", type_="check")
    op.create_check_constraint("ck_v54_provider_confirm_synthetic", "v54_provider_actions", _PRODUCT_POLICY)


def downgrade():
    op.drop_constraint("ck_v54_provider_confirm_synthetic", "v54_provider_actions", type_="check")
    op.create_check_constraint("ck_v54_provider_confirm_synthetic", "v54_provider_actions",
                               "mode = 'CONFIRM' AND synthetic_only = true AND provider = 'synthetic'")
    op.drop_column("google_oauth_tokens", "credential_generation")
