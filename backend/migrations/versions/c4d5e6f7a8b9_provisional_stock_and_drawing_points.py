"""Provisional explicit stock ledger and immutable drawing points. No production activation."""
from alembic import op
import sqlalchemy as sa
revision='c4d5e6f7a8b9'
down_revision='c3d4e5f6a7b8'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('material_writeoffs',
        sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('project_id',sa.Integer(),sa.ForeignKey('projects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('purchase_item_id',sa.Integer(),sa.ForeignKey('purchase_items.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('work_log_entry_id',sa.Integer(),sa.ForeignKey('work_log_entries.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('operation_key',sa.String(160),nullable=False),sa.Column('qty',sa.Numeric(18,3),nullable=False),
        sa.Column('unit',sa.String(40),nullable=False),
        sa.Column('reverses_id',sa.Integer(),sa.ForeignKey('material_writeoffs.id',ondelete='RESTRICT'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('project_id','operation_key',name='uq_material_writeoff_operation'),
        sa.UniqueConstraint('reverses_id',name='uq_material_writeoff_reversal'),
        sa.CheckConstraint('qty > 0',name='ck_material_writeoff_qty_positive'),
        sa.CheckConstraint('reverses_id IS NULL OR reverses_id <> id',name='ck_material_writeoff_not_self'))
    for name in ['project_id','purchase_item_id','work_log_entry_id']:op.create_index('ix_material_writeoffs_'+name,'material_writeoffs',[name])
    op.create_table('defect_drawing_points',
        sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('project_id',sa.Integer(),sa.ForeignKey('projects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('defect_id',sa.Integer(),sa.ForeignKey('defects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('rd_version_id',sa.Integer(),sa.ForeignKey('rd_volume_versions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('page',sa.Integer(),nullable=False),sa.Column('x',sa.String(32),nullable=False),sa.Column('y',sa.String(32),nullable=False),
        sa.Column('coordinate_system',sa.String(40),server_default='normalized_page_v1',nullable=False),
        sa.UniqueConstraint('defect_id',name='uq_defect_drawing_point'))
    op.create_index('ix_defect_drawing_points_project_id','defect_drawing_points',['project_id'])


def downgrade():
    raise RuntimeError('Removing stock/drawing history requires separately reviewed restore')
