"""Durable idempotent defect photos; no production activation."""
from alembic import op
import sqlalchemy as sa
revision='c5d6e7f8a9b0'
down_revision='c4d5e6f7a8b9'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('defect_photo_receipts',
        sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('project_id',sa.Integer(),sa.ForeignKey('projects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('defect_id',sa.Integer(),sa.ForeignKey('defects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('operation_key',sa.String(160),nullable=False),
        sa.Column('request_sha256',sa.String(64),nullable=False),
        sa.Column('photo_id',sa.Integer(),sa.ForeignKey('defect_photos.id',ondelete='SET NULL'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('project_id','user_id','operation_key',name='uq_defect_photo_receipt_operation'),
        sa.CheckConstraint('length(request_sha256) = 64',name='ck_defect_photo_receipt_digest'))
    for column in ['project_id','defect_id']:
        op.create_index('ix_defect_photo_receipts_'+column,'defect_photo_receipts',[column])

def downgrade():
    raise RuntimeError('Removing upload tombstones requires separately reviewed restore')
