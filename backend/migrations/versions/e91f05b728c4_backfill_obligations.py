"""backfill obligations from existing tasks

Revision ID: e91f05b728c4
Revises: d64c27a113f0
"""
from alembic import op

revision = "e91f05b728c4"
down_revision = "d64c27a113f0"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO obligations (
            project_id, owner_user_id, task_id, title, status, due_date,
            result_note, source_type, source_id, source_name, source_excerpt,
            source_hash, confidence, created_at, updated_at
        )
        SELECT
            project_id, assignee_user_id, id, title,
            CASE
                WHEN status = 'completed' THEN 'fulfilled'
                WHEN status = 'in_progress' THEN 'in_progress'
                WHEN status = 'cancelled' THEN 'dismissed'
                ELSE 'needs_confirmation'
            END,
            due_date, result_note, source_type, source_file_id,
            source_file_name, source_excerpt, source_excerpt_hash,
            confidence, created_at, updated_at
        FROM tasks
        ON CONFLICT (project_id, source_id, source_hash) DO NOTHING
    """)


def downgrade():
    op.execute("DELETE FROM obligations WHERE task_id IS NOT NULL")
