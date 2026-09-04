"""add privacy-safe AI analysis cache

Revision ID: b7e1a2c3d4f5
Revises: fa91b2c3d4e5
"""

from alembic import op
import sqlalchemy as sa


revision = "b7e1a2c3d4f5"
down_revision = "fa91b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_analysis_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("policy_mode", sa.String(30), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_analysis_cache_cache_key", "ai_analysis_cache", ["cache_key"], unique=True)
    op.create_index("ix_ai_analysis_cache_provider", "ai_analysis_cache", ["provider"])
    op.create_index("ix_ai_analysis_cache_operation", "ai_analysis_cache", ["operation"])


def downgrade():
    op.drop_index("ix_ai_analysis_cache_operation", table_name="ai_analysis_cache")
    op.drop_index("ix_ai_analysis_cache_provider", table_name="ai_analysis_cache")
    op.drop_index("ix_ai_analysis_cache_cache_key", table_name="ai_analysis_cache")
    op.drop_table("ai_analysis_cache")
