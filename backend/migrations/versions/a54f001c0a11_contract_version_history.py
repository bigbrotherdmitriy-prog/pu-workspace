"""Immutable contract version history.

Revision ID: a54f001c0a11
Revises: a54f001c0a10
"""
from alembic import op
import sqlalchemy as sa

revision = "a54f001c0a11"
down_revision = "a54f001c0a10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contract_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Deliberately no contract FK: deletion of an empty erroneous draft must
        # leave its immutable history available to the project audit.
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(30), nullable=False),
        sa.Column("resulting_record_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("contract_id", "sequence", name="uq_contract_version_sequence"),
        sa.CheckConstraint("sequence > 0 AND resulting_record_version > 0", name="ck_contract_version_positive"),
    )
    op.create_index("ix_contract_versions_contract_id", "contract_versions", ["contract_id"])
    op.create_index("ix_contract_versions_project_id", "contract_versions", ["project_id"])
    op.create_index("ix_contract_versions_actor_user_id", "contract_versions", ["actor_user_id"])


def downgrade():
    op.drop_index("ix_contract_versions_actor_user_id", table_name="contract_versions")
    op.drop_index("ix_contract_versions_project_id", table_name="contract_versions")
    op.drop_index("ix_contract_versions_contract_id", table_name="contract_versions")
    op.drop_table("contract_versions")
