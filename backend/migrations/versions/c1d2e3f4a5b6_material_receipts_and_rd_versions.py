"""INERT REVIEW PROPOSAL ONLY: material arrivals and retained drawing versions.

Revision ID: c1d2e3f4a5b6
Revises: b9c0d1e2f3a4 of the verified 86a4 source (116-revision chain).
Supersedes the inactive receipt-only c0d1e2f3a5b6 proposal; never install both.
Not part of backend/migrations/versions. No production upgrade is authorized.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c1d2e3f4a5b6'
down_revision = 'b9c0d1e2f3a4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect not in ('sqlite', 'postgresql'):
        raise RuntimeError('Combined schema proposal supports SQLite/PostgreSQL only')
    op.create_table(
        'material_receipts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('purchase_item_id', sa.Integer(), sa.ForeignKey('purchase_items.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('operation_key', sa.String(160), nullable=False),
        sa.Column('qty', sa.Numeric(18, 3), nullable=False),
        sa.Column('unit', sa.String(40), nullable=False),
        sa.Column('source_document_ref', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('project_id', 'operation_key', name='uq_material_receipt_project_operation'),
        sa.CheckConstraint('qty > 0', name='ck_material_receipt_qty_positive'),
    )
    op.create_index('ix_material_receipts_project_id', 'material_receipts', ['project_id'])
    op.create_index('ix_material_receipts_purchase_item_id', 'material_receipts', ['purchase_item_id'])
    op.create_table(
        'rd_volume_versions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column('volume_id', sa.Integer(), sa.ForeignKey('rd_volumes.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('file_name', sa.String(500), nullable=False),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('volume_id', 'content_sha256', name='uq_rd_volume_version_digest'),
        sa.CheckConstraint('size_bytes >= 0', name='ck_rd_volume_version_size_nonneg'),
        sa.CheckConstraint('length(content_sha256) = 64', name='ck_rd_volume_version_digest_len'),
    )
    op.create_index('ix_rd_volume_versions_volume_id', 'rd_volume_versions', ['volume_id'])
    if dialect == 'sqlite':
        # Nullable ADD COLUMN is supported; avoid rebuilding the parent with FK CASCADE children.
        op.execute('ALTER TABLE defects ADD COLUMN rd_version_id INTEGER '
                   'CONSTRAINT fk_defects_rd_version_id REFERENCES rd_volume_versions (id) ON DELETE RESTRICT')
    else:
        op.add_column('defects', sa.Column('rd_version_id', sa.Integer(), nullable=True))
        op.create_foreign_key('fk_defects_rd_version_id', 'defects', 'rd_volume_versions',
                              ['rd_version_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_defects_rd_version_id', 'defects', ['rd_version_id'])


def downgrade() -> None:
    raise RuntimeError('Destructive removal of arrivals/version history needs a separately reviewed retention and restore decision')
