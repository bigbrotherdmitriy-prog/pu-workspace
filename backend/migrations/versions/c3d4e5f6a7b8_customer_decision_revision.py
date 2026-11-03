"""Local review candidate: monotonic customer decision revision.

No production activation. Filled PostgreSQL upgrade/rollback gates remain required.
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6a7b8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Customer decision revision upgrade requires PostgreSQL review")
    bind.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    bind.execute(sa.text("SET LOCAL statement_timeout = '15s'"))
    bind.exec_driver_sql("LOCK TABLE public.ks_acts IN ACCESS EXCLUSIVE MODE")
    columns = sa.inspect(bind).get_columns("ks_acts", schema="public")
    if any(column["name"] == "decision_revision" for column in columns):
        raise RuntimeError("Existing decision revision column requires explicit schema review")
    op.add_column("ks_acts", sa.Column("decision_revision", sa.Integer(), nullable=False,
                                     server_default=sa.text("0")), schema="public")


def downgrade():
    raise RuntimeError("Dropping customer decision revisions requires a separately reviewed restore")
