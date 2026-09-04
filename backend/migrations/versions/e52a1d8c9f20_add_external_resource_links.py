"""add provider-neutral external resource links

Revision ID: e52a1d8c9f20
Revises: c42e71d58a13
"""
from alembic import op
import sqlalchemy as sa

revision = "e52a1d8c9f20"
down_revision = "c42e71d58a13"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_resource_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("external_id", sa.String(500), nullable=False),
        sa.Column("container_id", sa.String(500), nullable=True),
        sa.Column("sync_status", sa.String(40), nullable=False, server_default="synced"),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("entity_type", "entity_id", "provider", "resource_type", name="uq_external_resource_entity_provider_type"),
    )
    op.create_index("ix_external_resource_links_project_id", "external_resource_links", ["project_id"])
    op.create_index("ix_external_resource_links_entity_type", "external_resource_links", ["entity_type"])
    op.create_index("ix_external_resource_links_entity_id", "external_resource_links", ["entity_id"])
    op.create_index("ix_external_resource_links_provider", "external_resource_links", ["provider"])
    op.create_index("ix_external_resource_links_external_id", "external_resource_links", ["external_id"])
    op.create_index("ix_external_resource_links_sync_status", "external_resource_links", ["sync_status"])
    op.execute("""
        INSERT INTO external_resource_links
            (project_id, entity_type, entity_id, provider, resource_type, external_id, container_id, sync_status, synced_at)
        SELECT project_id, 'task', id, 'google_workspace', 'task', google_task_id,
               google_task_list_id, 'synced', google_synced_at
        FROM tasks WHERE google_task_id IS NOT NULL
        ON CONFLICT (entity_type, entity_id, provider, resource_type) DO NOTHING
    """)
    op.execute("""
        INSERT INTO external_resource_links
            (project_id, entity_type, entity_id, provider, resource_type, external_id, sync_status, synced_at)
        SELECT project_id, 'task', id, 'google_workspace', 'calendar_event',
               google_calendar_event_id, 'synced', google_calendar_synced_at
        FROM tasks WHERE google_calendar_event_id IS NOT NULL
        ON CONFLICT (entity_type, entity_id, provider, resource_type) DO NOTHING
    """)


def downgrade():
    op.drop_table("external_resource_links")
