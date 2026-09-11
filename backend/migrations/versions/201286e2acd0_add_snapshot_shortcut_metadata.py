"""add snapshot shortcut metadata

Revision ID: 201286e2acd0
Revises: 21d8f9354c18
Create Date: 2026-09-11 02:53:26.361304
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '201286e2acd0'
down_revision: Union[str, Sequence[str], None] = '21d8f9354c18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("virtual_nodes", sa.Column("shortcut_target_id", sa.String(length=255), nullable=True))
    op.add_column("virtual_nodes", sa.Column("shortcut_target_mime_type", sa.String(length=255), nullable=True))
    op.add_column("virtual_nodes", sa.Column("shortcut_target_resource_key", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("virtual_nodes", "shortcut_target_resource_key")
    op.drop_column("virtual_nodes", "shortcut_target_mime_type")
    op.drop_column("virtual_nodes", "shortcut_target_id")
