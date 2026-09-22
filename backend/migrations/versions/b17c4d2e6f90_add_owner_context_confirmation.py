"""Add explicit owner confirmation provenance for Product AUTO context.

Revision ID: b17c4d2e6f90
Revises: a54f001c0a11
"""
from alembic import op
import sqlalchemy as sa


revision = "b17c4d2e6f90"
down_revision = "a54f001c0a11"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column(
        "context_confirmed_by_user_id", sa.Integer(), nullable=True,
    ))
    op.add_column("messages", sa.Column(
        "context_confirmed_by_user_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("messages", sa.Column(
        "context_confirmed_context_version", sa.Integer(), nullable=True,
    ))
    op.add_column("messages", sa.Column(
        "context_confirmed_authority_epoch", sa.Integer(), nullable=True,
    ))
    op.create_foreign_key(
        "fk_message_owner_context_confirmation_user",
        "messages", "users", ["context_confirmed_by_user_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_message_owner_context_confirmation_complete",
        "messages",
        "(context_confirmed_by_user_id IS NULL "
        "AND context_confirmed_by_user_at IS NULL "
        "AND context_confirmed_context_version IS NULL "
        "AND context_confirmed_authority_epoch IS NULL) OR "
        "(context_confirmed_by_user_id IS NOT NULL "
        "AND context_confirmed_by_user_at IS NOT NULL "
        "AND context_confirmed_context_version > 0 "
        "AND context_confirmed_authority_epoch > 0)",
    )


def downgrade():
    op.drop_constraint(
        "ck_message_owner_context_confirmation_complete", "messages", type_="check",
    )
    op.drop_constraint(
        "fk_message_owner_context_confirmation_user", "messages", type_="foreignkey",
    )
    op.drop_column("messages", "context_confirmed_authority_epoch")
    op.drop_column("messages", "context_confirmed_context_version")
    op.drop_column("messages", "context_confirmed_by_user_at")
    op.drop_column("messages", "context_confirmed_by_user_id")
