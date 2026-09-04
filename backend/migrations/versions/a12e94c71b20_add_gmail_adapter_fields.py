"""add Gmail adapter fields

Revision ID: a12e94c71b20
Revises: f42a81c9d3e6
"""
from alembic import op
import sqlalchemy as sa

revision = "a12e94c71b20"
down_revision = "f42a81c9d3e6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("source_sender", sa.String(1000)))
    op.add_column("messages", sa.Column("source_thread_id", sa.String(500)))
    op.create_index("ix_messages_source_thread_id", "messages", ["source_thread_id"])
    op.add_column("response_drafts", sa.Column("sent_external_id", sa.String(500)))
    op.add_column("response_drafts", sa.Column("sent_at", sa.DateTime(timezone=True)))
    op.create_index("ix_response_drafts_sent_external_id", "response_drafts", ["sent_external_id"], unique=True)


def downgrade():
    op.drop_index("ix_response_drafts_sent_external_id", table_name="response_drafts")
    op.drop_column("response_drafts", "sent_at")
    op.drop_column("response_drafts", "sent_external_id")
    op.drop_index("ix_messages_source_thread_id", table_name="messages")
    op.drop_column("messages", "source_thread_id")
    op.drop_column("messages", "source_sender")
