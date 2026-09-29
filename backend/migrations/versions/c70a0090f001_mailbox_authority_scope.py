"""Narrow renewed mailbox authority to one project and credential generation."""
from alembic import op
import sqlalchemy as sa

revision = "c70a0090f001"
down_revision = "c70a00a2f001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("v54_mailbox_authority_states", sa.Column("scope_project_id", sa.Integer(), nullable=True))
    op.add_column("v54_mailbox_authority_states", sa.Column("scope_credential_generation", sa.Integer(), nullable=True))


def downgrade():
    # Dropping scope must never turn a live narrow grant into a broad grant.
    op.execute("UPDATE v54_mailbox_authority_states SET state='revoked', authority_version=authority_version+1 WHERE scope_project_id IS NOT NULL OR scope_credential_generation IS NOT NULL")
    op.drop_column("v54_mailbox_authority_states", "scope_credential_generation")
    op.drop_column("v54_mailbox_authority_states", "scope_project_id")
