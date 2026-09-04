"""Switch Message uniqueness to verified mailbox scope.

Revision ID: a54f001c0a04
Revises: a54f001c0a03
"""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a04"
down_revision = "a54f001c0a03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("uq_message_source_legacy", "messages", ["source_type", "source_external_id"],
                    unique=True, postgresql_where=sa.text("mail_connection_id IS NULL"))
    op.drop_constraint("uq_message_source", "messages", type_="unique")


def downgrade():
    connection = op.get_bind()
    duplicate = connection.scalar(sa.text("""
        SELECT EXISTS (
          SELECT 1 FROM messages GROUP BY source_type, source_external_id HAVING count(*) > 1
        )
    """))
    if duplicate:
        raise RuntimeError("Global Message identity cannot be restored without data loss")
    op.create_unique_constraint("uq_message_source", "messages", ["source_type", "source_external_id"])
    op.drop_index("uq_message_source_legacy", table_name="messages")
