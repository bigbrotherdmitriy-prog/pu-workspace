"""Preserve optional exact local time on v5.4 deadline claims.

Revision ID: a54f001c0a09
Revises: a54f001c0a08
"""
from alembic import context, op
import sqlalchemy as sa


revision = "a54f001c0a09"
down_revision = "a54f001c0a08"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "v54_deadline_claims",
        sa.Column("due_time", sa.Time(), nullable=True),
    )


def downgrade():
    message = "Timed deadline claims require explicit archival before downgrade"
    if context.is_offline_mode():
        op.execute(sa.text(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM v54_deadline_claims
                    WHERE due_time IS NOT NULL LIMIT 1
                ) THEN
                    RAISE EXCEPTION '{message}';
                END IF;
            END $$
        """))
    elif op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM v54_deadline_claims WHERE due_time IS NOT NULL LIMIT 1)"
    )):
        raise RuntimeError(message)
    op.drop_column("v54_deadline_claims", "due_time")
