"""Merge the v5.4 trust runtime and production MPP schema lines.

Revision ID: e74a1c5d09b2
Revises: a54f001c0a09, c05e2f3a4b56
"""

revision = "e74a1c5d09b2"
down_revision = ("a54f001c0a09", "c05e2f3a4b56")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two already-applied schema lines without changing data."""


def downgrade() -> None:
    """Split the graph back to the two parent revisions."""
