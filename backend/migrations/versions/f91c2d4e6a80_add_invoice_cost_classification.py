"""Add managed cost categories and human-confirmed invoice extraction proposals.

Revision ID: f91c2d4e6a80
Revises: e73c2b4a901d
"""
from alembic import op
import sqlalchemy as sa


revision = "f91c2d4e6a80"
down_revision = "e73c2b4a901d"
branch_labels = None
depends_on = None


_DEFAULT_CATEGORIES = (
    ("Прямые", "прямые", 10),
    ("Накладные", "накладные", 20),
    ("Зарплата", "зарплата", 30),
    ("Аренда", "аренда", 40),
    ("Командировки", "командировки", 50),
)


def upgrade():
    op.create_table(
        "cost_categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "normalized_name", name="uq_cost_categories_org_name"),
    )
    op.create_index("ix_cost_categories_organization_id", "cost_categories", ["organization_id"])
    op.create_index("ix_cost_categories_is_active", "cost_categories", ["is_active"])

    # INSERT ... SELECT works both online and in Alembic's offline SQL mode;
    # there is intentionally no dependency on a live connection here.
    for name, normalized, sort_order in _DEFAULT_CATEGORIES:
        op.execute(sa.text(
            "INSERT INTO cost_categories "
            "(organization_id, name, normalized_name, sort_order) "
            f"SELECT id, '{name}', '{normalized}', {sort_order} FROM organizations"
        ))

    with op.batch_alter_table("budget_lines") as batch:
        batch.add_column(sa.Column("cost_category_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_budget_lines_cost_category", "cost_categories",
            ["cost_category_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_budget_lines_cost_category_id", ["cost_category_id"])
    with op.batch_alter_table("cash_flow_entries") as batch:
        batch.add_column(sa.Column("cost_category_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_cash_flow_entries_cost_category", "cost_categories",
            ["cost_category_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_cash_flow_entries_cost_category_id", ["cost_category_id"])

    op.execute(sa.text(
        "UPDATE budget_lines SET cost_category_id = ("
        "SELECT cc.id FROM cost_categories cc JOIN projects p ON p.organization_id = cc.organization_id "
        "WHERE p.id = budget_lines.project_id AND cc.normalized_name = lower(trim(budget_lines.category))"
        ") WHERE category IS NOT NULL"
    ))
    op.execute(sa.text(
        "UPDATE cash_flow_entries SET cost_category_id = ("
        "SELECT cc.id FROM cost_categories cc JOIN projects p ON p.organization_id = cc.organization_id "
        "WHERE p.id = cash_flow_entries.project_id AND cc.normalized_name = lower(trim(cash_flow_entries.category))"
        ") WHERE category IS NOT NULL"
    ))

    op.create_table(
        "invoice_extraction_proposals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("source_document_version_id", sa.Integer(), nullable=False),
        sa.Column("source_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("proposed_cost_category_id", sa.Integer(), nullable=True),
        sa.Column("selected_cost_category_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount_evidence_quote", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("counterparty", sa.String(length=500), nullable=True),
        sa.Column("counterparty_evidence_quote", sa.Text(), nullable=True),
        sa.Column("payment_purpose", sa.String(length=1000), nullable=True),
        sa.Column("payment_purpose_evidence_quote", sa.Text(), nullable=True),
        sa.Column("category_evidence_quote", sa.Text(), nullable=True),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default="0", nullable=False),
        sa.Column("extraction_method", sa.String(length=30), nullable=False),
        sa.Column("fallback_reason", sa.String(length=100), nullable=True),
        sa.Column("target_kind", sa.String(length=20), server_default="cash_flow", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="proposed", nullable=False),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_cash_flow_id", sa.Integer(), nullable=True),
        sa.Column("created_budget_line_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('proposed','confirmed','rejected')", name="ck_invoice_extraction_status"),
        sa.CheckConstraint("target_kind IN ('cash_flow','budget')", name="ck_invoice_extraction_target_kind"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_document_version_id"], ["document_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposed_cost_category_id"], ["cost_categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["selected_cost_category_id"], ["cost_categories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_cash_flow_id"], ["cash_flow_entries.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_budget_line_id"], ["budget_lines.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "source_document_version_id",
            name="uq_invoice_extraction_project_version",
        ),
    )
    for column in (
        "project_id", "source_document_id", "source_document_version_id",
        "proposed_cost_category_id", "selected_cost_category_id", "status",
    ):
        op.create_index(f"ix_invoice_extraction_proposals_{column}", "invoice_extraction_proposals", [column])


def downgrade():
    op.drop_table("invoice_extraction_proposals")
    with op.batch_alter_table("cash_flow_entries") as batch:
        batch.drop_index("ix_cash_flow_entries_cost_category_id")
        batch.drop_constraint("fk_cash_flow_entries_cost_category", type_="foreignkey")
        batch.drop_column("cost_category_id")
    with op.batch_alter_table("budget_lines") as batch:
        batch.drop_index("ix_budget_lines_cost_category_id")
        batch.drop_constraint("fk_budget_lines_cost_category", type_="foreignkey")
        batch.drop_column("cost_category_id")
    op.drop_index("ix_cost_categories_is_active", table_name="cost_categories")
    op.drop_index("ix_cost_categories_organization_id", table_name="cost_categories")
    op.drop_table("cost_categories")
