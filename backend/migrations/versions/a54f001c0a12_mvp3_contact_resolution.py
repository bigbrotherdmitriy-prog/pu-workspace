"""Mailbox/project scoped contact resolution and append-only decisions.

Revision ID: a54f001c0a12
Revises: a54f001c0a11
"""
from alembic import op
import sqlalchemy as sa


revision = "a54f001c0a12"
down_revision = "a54f001c0a11"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_project_contact_org_email", "project_contacts", type_="unique")
    op.add_column("project_contacts", sa.Column("mail_connection_id", sa.Uuid(), nullable=True))
    op.add_column("project_contacts", sa.Column("source_message_id", sa.Integer(), nullable=True))
    op.add_column("project_contacts", sa.Column("normalized_domain", sa.String(253), nullable=True))
    op.add_column("project_contacts", sa.Column("phone", sa.String(100), nullable=True))
    op.add_column("project_contacts", sa.Column("normalized_phone", sa.String(20), nullable=True))
    op.add_column("project_contacts", sa.Column("record_version", sa.Integer(), server_default="1", nullable=False))
    op.add_column("project_contacts", sa.Column("resolution_state", sa.String(20), server_default="proposed", nullable=False))
    op.add_column("project_contacts", sa.Column("resolution_reason_code", sa.String(50), server_default="legacy_backfill", nullable=False))
    op.execute(
        "UPDATE project_contacts SET resolution_state = CASE WHEN confirmed THEN 'confirmed' ELSE 'proposed' END, "
        "normalized_domain = lower(split_part(normalized_email, '@', 2))"
    )
    op.create_foreign_key(
        "fk_project_contact_mailbox_scope", "project_contacts", "v54_mail_connections",
        ["organization_id", "mail_connection_id"], ["organization_id", "id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_project_contact_source_message", "project_contacts", "messages",
        ["source_message_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint("ck_project_contact_record_version", "project_contacts", "record_version > 0")
    op.create_check_constraint(
        "ck_project_contact_resolution_state", "project_contacts",
        "resolution_state IN ('proposed','conflict','confirmed','corrected','rejected')",
    )
    op.create_index("ix_project_contacts_mail_connection_id", "project_contacts", ["mail_connection_id"])
    op.create_index("ix_project_contacts_source_message_id", "project_contacts", ["source_message_id"])
    op.create_index("ix_project_contacts_normalized_domain", "project_contacts", ["normalized_domain"])
    op.create_index("ix_project_contacts_normalized_phone", "project_contacts", ["normalized_phone"])
    op.create_index("ix_project_contacts_resolution_state", "project_contacts", ["resolution_state"])
    op.create_index(
        "uq_project_contact_legacy_email", "project_contacts", ["organization_id", "normalized_email"],
        unique=True, postgresql_where=sa.text("mail_connection_id IS NULL"),
    )
    op.create_index(
        "uq_project_contact_mailbox_project_email", "project_contacts",
        ["organization_id", "project_id", "mail_connection_id", "normalized_email"],
        unique=True, postgresql_where=sa.text("mail_connection_id IS NOT NULL"),
    )

    op.create_table(
        "project_contact_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("project_contacts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("mail_connection_id", sa.Uuid(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("decision_key", sa.String(100), nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("event", sa.String(20), nullable=False),
        sa.Column("from_state", sa.String(20), nullable=False),
        sa.Column("to_state", sa.String(20), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason_code", sa.String(50), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("contact_id", "sequence", name="uq_project_contact_history_sequence"),
        sa.UniqueConstraint("organization_id", "decision_key", name="uq_project_contact_history_decision"),
        sa.CheckConstraint("sequence > 0 AND resulting_version > 1", name="ck_project_contact_history_versions"),
    )
    for column in ("contact_id", "organization_id", "project_id", "mail_connection_id", "actor_user_id"):
        op.create_index(f"ix_project_contact_history_{column}", "project_contact_history", [column])


def downgrade():
    op.drop_table("project_contact_history")
    op.drop_index("uq_project_contact_mailbox_project_email", table_name="project_contacts")
    op.drop_index("uq_project_contact_legacy_email", table_name="project_contacts")
    for name in (
        "resolution_state", "normalized_phone", "normalized_domain", "source_message_id", "mail_connection_id",
    ):
        op.drop_index(f"ix_project_contacts_{name}", table_name="project_contacts")
    op.drop_constraint("ck_project_contact_resolution_state", "project_contacts", type_="check")
    op.drop_constraint("ck_project_contact_record_version", "project_contacts", type_="check")
    op.drop_constraint("fk_project_contact_source_message", "project_contacts", type_="foreignkey")
    op.drop_constraint("fk_project_contact_mailbox_scope", "project_contacts", type_="foreignkey")
    for column in (
        "resolution_reason_code", "resolution_state", "record_version", "normalized_phone", "phone",
        "normalized_domain", "source_message_id", "mail_connection_id",
    ):
        op.drop_column("project_contacts", column)
    op.create_unique_constraint(
        "uq_project_contact_org_email", "project_contacts", ["organization_id", "normalized_email"]
    )
