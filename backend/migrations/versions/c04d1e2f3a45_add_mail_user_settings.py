"""add per-user mail editor settings

Revision ID: c04d1e2f3a45
Revises: b01c9a7d4e21
"""
from alembic import op
import sqlalchemy as sa


revision = "c04d1e2f3a45"
down_revision = "b01c9a7d4e21"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mail_user_settings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("signature_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("auto_signature_new", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_signature_reply", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_font", sa.String(length=50), nullable=False, server_default="Arial"),
        sa.Column("default_font_size", sa.String(length=10), nullable=False, server_default="14px"),
        sa.Column("default_text_color", sa.String(length=20), nullable=False, server_default="#18211d"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade():
    op.drop_table("mail_user_settings")
