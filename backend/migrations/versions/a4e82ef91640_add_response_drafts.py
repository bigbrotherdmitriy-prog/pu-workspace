"""add response drafts

Revision ID: a4e82ef91640
Revises: 9c31b5b53a21
"""
from alembic import op
import sqlalchemy as sa

revision = "a4e82ef91640"
down_revision = "9c31b5b53a21"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "response_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organizer_session_id", sa.Integer(), sa.ForeignKey("organizer_sessions.id", ondelete="SET NULL")),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("source_file_id", sa.String(255), nullable=False),
        sa.Column("source_file_name", sa.String(1000), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("source_excerpt_hash", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "source_file_id", "source_excerpt_hash", name="uq_response_source_excerpt"),
    )
    op.create_index("ix_response_drafts_project_id", "response_drafts", ["project_id"])
    op.create_index("ix_response_drafts_reviewer_user_id", "response_drafts", ["reviewer_user_id"])
    op.create_index("ix_response_drafts_organizer_session_id", "response_drafts", ["organizer_session_id"])
    op.create_index("ix_response_drafts_status", "response_drafts", ["status"])
    op.create_index("ix_response_drafts_source_file_id", "response_drafts", ["source_file_id"])


def downgrade():
    op.drop_table("response_drafts")
