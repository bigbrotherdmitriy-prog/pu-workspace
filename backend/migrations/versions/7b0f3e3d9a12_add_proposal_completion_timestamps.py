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
    op.add_column("organizer_proposals", sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizer_proposals", sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("organizer_proposals", "rolled_back_at")
    op.drop_column("organizer_proposals", "applied_at")
