"""add organizations and contracts

Revision ID: a17c4d820e31
Revises: f15d7b329a41
"""
from alembic import op
import sqlalchemy as sa

revision = "a17c4d820e31"
down_revision = "f15d7b329a41"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("INSERT INTO organizations (name) VALUES ('PU Workspace')")
    op.add_column("projects", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.execute("UPDATE projects SET organization_id=(SELECT id FROM organizations ORDER BY id LIMIT 1)")
    op.alter_column("projects", "organization_id", nullable=False)
    op.create_foreign_key("fk_projects_organization", "projects", "organizations", ["organization_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"])
    op.create_table(
        "contracts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("counterparty", sa.String(length=500), nullable=True),
        sa.Column("signed_at", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="active"),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_contracts_project_id", "contracts", ["project_id"])
    op.create_index("ix_contracts_status", "contracts", ["status"])
    op.create_index("ix_contracts_source_document_id", "contracts", ["source_document_id"])


def downgrade():
    op.drop_table("contracts")
    op.drop_index("ix_projects_organization_id", table_name="projects")
    op.drop_constraint("fk_projects_organization", "projects", type_="foreignkey")
    op.drop_column("projects", "organization_id")
    op.drop_table("organizations")
