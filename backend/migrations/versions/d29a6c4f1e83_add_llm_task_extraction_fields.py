"""add llm task extraction fields

Revision ID: d29a6c4f1e83
Revises: c13606d92787
Create Date: 2026-09-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd29a6c4f1e83'
down_revision: Union[str, Sequence[str], None] = 'c13606d92787'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrored on both tables: Obligation duplicates Task's evidence columns
# already (source_excerpt/source_hash/confidence), same pattern continues here.
# A Column can only be bound to one table, so each column is built fresh per
# call rather than shared/copied.
def _new_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("amount_currency", sa.String(8), nullable=True),
        sa.Column("amount_evidence_quote", sa.Text(), nullable=True),
        sa.Column("due_date_evidence_quote", sa.Text(), nullable=True),
        sa.Column("assignee_hint", sa.String(300), nullable=True),
        sa.Column("assignee_evidence_quote", sa.Text(), nullable=True),
        # 'regex' default backfills every pre-existing row honestly: they were.
        sa.Column("extraction_method", sa.String(20), nullable=False, server_default="regex"),
    )


_COLUMN_NAMES = tuple(column.name for column in _new_columns())


def upgrade() -> None:
    for table in ("tasks", "obligations"):
        for column in _new_columns():
            op.add_column(table, column)


def downgrade() -> None:
    for table in ("tasks", "obligations"):
        for name in reversed(_COLUMN_NAMES):
            op.drop_column(table, name)
