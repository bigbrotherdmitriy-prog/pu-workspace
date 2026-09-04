"""add provider-neutral mail client draft state

Revision ID: b01c9a7d4e21
Revises: a31c7d8e9f20
"""
from alembic import op
import sqlalchemy as sa


revision = "b01c9a7d4e21"
down_revision = "a31c7d8e9f20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("mail_headers_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("messages", sa.Column("mail_labels_json", sa.Text(), nullable=False, server_default="[]"))

    op.add_column("response_drafts", sa.Column("contract_id", sa.Integer(), nullable=True))
    op.add_column("response_drafts", sa.Column("recipient_cc", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("response_drafts", sa.Column("recipient_bcc", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("response_drafts", sa.Column("attachments_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("response_drafts", sa.Column("provider", sa.String(length=50), nullable=False, server_default="google_workspace"))
    op.add_column("response_drafts", sa.Column("operation_kind", sa.String(length=30), nullable=False, server_default="reply"))
    op.add_column("response_drafts", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("response_drafts", sa.Column("approved_revision", sa.Integer(), nullable=True))
    op.add_column("response_drafts", sa.Column("approved_by_user_id", sa.Integer(), nullable=True))
    op.add_column("response_drafts", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("response_drafts", sa.Column("send_idempotency_key", sa.String(length=64), nullable=True))
    op.add_column("response_drafts", sa.Column("send_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("response_drafts", sa.Column("last_send_error_code", sa.String(length=100), nullable=True))
    op.add_column("response_drafts", sa.Column("last_send_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_response_drafts_contract_id", "response_drafts", "contracts", ["contract_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_response_drafts_approved_by", "response_drafts", "users", ["approved_by_user_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_response_drafts_contract_id", "response_drafts", ["contract_id"])
    op.create_index("ix_response_drafts_provider", "response_drafts", ["provider"])
    op.create_index("uq_response_drafts_send_idempotency_key", "response_drafts", ["send_idempotency_key"], unique=True)


def downgrade():
    op.drop_index("uq_response_drafts_send_idempotency_key", table_name="response_drafts")
    op.drop_index("ix_response_drafts_provider", table_name="response_drafts")
    op.drop_index("ix_response_drafts_contract_id", table_name="response_drafts")
    op.drop_constraint("fk_response_drafts_approved_by", "response_drafts", type_="foreignkey")
    op.drop_constraint("fk_response_drafts_contract_id", "response_drafts", type_="foreignkey")
    for column in (
        "last_send_at", "last_send_error_code", "send_attempts", "send_idempotency_key",
        "approved_at", "approved_by_user_id", "approved_revision", "revision",
        "operation_kind", "provider", "attachments_json", "recipient_bcc", "recipient_cc", "contract_id",
    ):
        op.drop_column("response_drafts", column)
    op.drop_column("messages", "mail_labels_json")
    op.drop_column("messages", "mail_headers_json")
