"""add proposal completion timestamps

Revision ID: 7b0f3e3d9a12
Revises: 310c0a8342cf
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b0f3e3d9a12"
down_revision: Union[str, Sequence[str], None] = "310c0a8342cf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE organizer_proposals ADD COLUMN IF NOT EXISTS applied_at TIMESTAMP WITH TIME ZONE")
        op.execute("ALTER TABLE organizer_proposals ADD COLUMN IF NOT EXISTS rolled_back_at TIMESTAMP WITH TIME ZONE")
        return
    existing = {column["name"] for column in sa.inspect(bind).get_columns("organizer_proposals")}
    if "applied_at" not in existing:
        op.add_column("organizer_proposals", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    if "rolled_back_at" not in existing:
        op.add_column("organizer_proposals", sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE organizer_proposals DROP COLUMN IF EXISTS rolled_back_at")
        op.execute("ALTER TABLE organizer_proposals DROP COLUMN IF EXISTS applied_at")
        return
    existing = {column["name"] for column in sa.inspect(bind).get_columns("organizer_proposals")}
    if "rolled_back_at" in existing:
        op.drop_column("organizer_proposals", "rolled_back_at")
    if "applied_at" in existing:
        op.drop_column("organizer_proposals", "applied_at")
