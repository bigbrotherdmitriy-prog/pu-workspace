"""add project contacts with deterministic email routing

Revision ID: e6b24a91c301
Revises: a31f6c9e20d4
"""
from alembic import op
import sqlalchemy as sa

revision = "e6b24a91c301"
down_revision = "a31f6c9e20d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("company", sa.String(length=500), nullable=True),
        sa.Column("email", sa.String(length=500), nullable=False),
        sa.Column("normalized_email", sa.String(length=500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="manual"),
        sa.Column("company_activity", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("organization_id", "normalized_email", name="uq_project_contact_org_email"),
    )
    op.create_index("ix_project_contacts_organization_id", "project_contacts", ["organization_id"])
    op.create_index("ix_project_contacts_project_id", "project_contacts", ["project_id"])
    op.create_index("ix_project_contacts_contract_id", "project_contacts", ["contract_id"])
    op.create_index("ix_project_contacts_created_by_user_id", "project_contacts", ["created_by_user_id"])
    op.create_index("ix_project_contacts_normalized_email", "project_contacts", ["normalized_email"])
    op.create_index("ix_project_contacts_active", "project_contacts", ["active"])
    op.create_index("ix_project_contacts_confirmed", "project_contacts", ["confirmed"])


def downgrade():
    op.drop_table("project_contacts")
