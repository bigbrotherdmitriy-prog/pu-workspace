"""add organizer idempotency keys

Revision ID: d93b5f607c82
Revises: c82a4e5f6b71
"""
from alembic import op
import sqlalchemy as sa

revision = "d93b5f607c82"
down_revision = "c82a4e5f6b71"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("organizer_proposals", sa.Column("idempotency_key", sa.String(100)))
    op.execute("UPDATE organizer_proposals SET idempotency_key='organizer-proposal-' || id")
    op.alter_column("organizer_proposals", "idempotency_key", nullable=False)
    op.create_index("ix_organizer_proposals_idempotency_key", "organizer_proposals", ["idempotency_key"], unique=True)
    op.add_column("organizer_operations", sa.Column("idempotency_key", sa.String(128)))
    op.execute("UPDATE organizer_operations SET idempotency_key='legacy-operation-' || id")
    op.alter_column("organizer_operations", "idempotency_key", nullable=False)
    op.create_index("ix_organizer_operations_idempotency_key", "organizer_operations", ["idempotency_key"], unique=True)


def downgrade():
    op.drop_index("ix_organizer_operations_idempotency_key", table_name="organizer_operations")
    op.drop_column("organizer_operations", "idempotency_key")
    op.drop_index("ix_organizer_proposals_idempotency_key", table_name="organizer_proposals")
    op.drop_column("organizer_proposals", "idempotency_key")
