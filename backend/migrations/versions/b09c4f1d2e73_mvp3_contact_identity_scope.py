"""MVP3 mailbox-scoped, replay-safe contact resolution.

Revision ID: b09c4f1d2e73
Revises: a72d4e6f8b91
"""

from alembic import op
import sqlalchemy as sa


revision = "b09c4f1d2e73"
down_revision = "a72d4e6f8b91"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_project_contact_org_email", "project_contacts", type_="unique")
    op.add_column("project_contacts", sa.Column("mail_connection_id", sa.Uuid(), nullable=True))
    op.add_column("project_contacts", sa.Column("source_message_id", sa.Integer(), nullable=True))
    op.add_column("project_contacts", sa.Column("normalized_domain", sa.String(253), nullable=True))
    op.add_column("project_contacts", sa.Column("phone", sa.String(100), nullable=True))
    op.add_column("project_contacts", sa.Column("normalized_phone", sa.String(20), nullable=True))
    op.add_column(
        "project_contacts",
        sa.Column("resolution_state", sa.String(20), server_default="proposed", nullable=False),
    )
    op.add_column(
        "project_contacts",
        sa.Column("resolution_reason_code", sa.String(50), server_default="legacy_backfill", nullable=False),
    )
    domain_expression = (
        "lower(split_part(normalized_email, '@', 2))"
        if op.get_bind().dialect.name == "postgresql"
        else "lower(substr(normalized_email, instr(normalized_email, '@') + 1))"
    )
    op.execute(
        "UPDATE project_contacts "
        "SET resolution_state = CASE WHEN confirmed THEN 'confirmed' ELSE 'proposed' END, "
        f"normalized_domain = {domain_expression}"
    )
    op.create_foreign_key(
        "fk_project_contact_mailbox_scope", "project_contacts", "v54_mail_connections",
        ["organization_id", "mail_connection_id"], ["organization_id", "id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_project_contact_source_message", "project_contacts", "messages",
        ["source_message_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_project_contact_resolution_state", "project_contacts",
        "resolution_state IN ('proposed','conflict','confirmed','corrected','rejected')",
    )
    for column in (
        "mail_connection_id", "source_message_id", "normalized_domain",
        "normalized_phone", "resolution_state",
    ):
        op.create_index(f"ix_project_contacts_{column}", "project_contacts", [column])
    op.create_index(
        "uq_project_contact_legacy_email", "project_contacts",
        ["organization_id", "normalized_email"], unique=True,
        postgresql_where=sa.text("mail_connection_id IS NULL"),
        sqlite_where=sa.text("mail_connection_id IS NULL"),
    )
    op.create_index(
        "uq_project_contact_mailbox_email", "project_contacts",
        ["organization_id", "mail_connection_id", "normalized_email"], unique=True,
        postgresql_where=sa.text("mail_connection_id IS NOT NULL"),
        sqlite_where=sa.text("mail_connection_id IS NOT NULL"),
    )

    # Reuse the accepted generic append-only history architecture rather than
    # creating a second contact-only history table.
    op.add_column("management_history", sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.add_column("management_history", sa.Column("command_hash", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_management_history_idempotency", "management_history",
        ["organization_id", "entity_type", "idempotency_key"],
    )


def downgrade():
    op.drop_constraint("uq_management_history_idempotency", "management_history", type_="unique")
    op.drop_column("management_history", "command_hash")
    op.drop_column("management_history", "idempotency_key")

    op.drop_index("uq_project_contact_mailbox_email", table_name="project_contacts")
    op.drop_index("uq_project_contact_legacy_email", table_name="project_contacts")
    for column in reversed((
        "mail_connection_id", "source_message_id", "normalized_domain",
        "normalized_phone", "resolution_state",
    )):
        op.drop_index(f"ix_project_contacts_{column}", table_name="project_contacts")
    op.drop_constraint("ck_project_contact_resolution_state", "project_contacts", type_="check")
    op.drop_constraint("fk_project_contact_source_message", "project_contacts", type_="foreignkey")
    op.drop_constraint("fk_project_contact_mailbox_scope", "project_contacts", type_="foreignkey")
    for column in (
        "resolution_reason_code", "resolution_state", "normalized_phone", "phone",
        "normalized_domain", "source_message_id", "mail_connection_id",
    ):
        op.drop_column("project_contacts", column)
    op.create_unique_constraint(
        "uq_project_contact_org_email", "project_contacts", ["organization_id", "normalized_email"],
    )
