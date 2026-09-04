"""persist snapshot analysis state

Revision ID: f15d7b329a41
Revises: e04c6a718d93
"""
from alembic import op
import sqlalchemy as sa

revision = "f15d7b329a41"
down_revision = "e04c6a718d93"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workspace_snapshots", sa.Column("analysis_status", sa.String(length=30), nullable=False, server_default="pending"))
    op.add_column("workspace_snapshots", sa.Column("analysis_result", sa.JSON(), nullable=True))
    op.add_column("workspace_snapshots", sa.Column("analysis_error", sa.Text(), nullable=True))
    op.create_index("ix_workspace_snapshots_analysis_status", "workspace_snapshots", ["analysis_status"])
    op.execute("""
        UPDATE workspace_snapshots ws SET analysis_status='ready'
        WHERE EXISTS (
            SELECT 1 FROM organizer_proposals op
            WHERE op.project_id=ws.project_id AND op.copy_folder_id='virtual:' || ws.id::text
        )
    """)


def downgrade():
    op.drop_index("ix_workspace_snapshots_analysis_status", table_name="workspace_snapshots")
    op.drop_column("workspace_snapshots", "analysis_error")
    op.drop_column("workspace_snapshots", "analysis_result")
    op.drop_column("workspace_snapshots", "analysis_status")
