"""Review candidate: fail-closed tenant FKs/index and required timestamps.

Codex fallback. Not activated in the server root checkout or production.
Revision c2d3e4f5a6b7 follows independently reviewed c1 additions.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c2d3e4f5a6b7'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None

TIMESTAMPS = (('acceptance_acts', 'created_at'), ('acceptance_acts', 'updated_at'), ('bookable_resources', 'created_at'), ('bookable_resources', 'updated_at'), ('budget_lines', 'created_at'), ('budget_lines', 'updated_at'), ('cash_flow_entries', 'created_at'), ('cash_flow_entries', 'updated_at'), ('meeting_participants', 'created_at'), ('meeting_proposals', 'created_at'), ('meeting_resources', 'created_at'), ('meeting_source_bindings', 'bound_at'), ('meetings', 'created_at'), ('meetings', 'updated_at'), ('notifications', 'created_at'), ('obligations', 'created_at'), ('obligations', 'updated_at'), ('procurement_items', 'created_at'), ('procurement_items', 'updated_at'), ('project_ai_policies', 'updated_at'), ('schedule_baselines', 'created_at'), ('schedule_items', 'created_at'))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError('This reviewed schema transition requires PostgreSQL')
    bind.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    bind.execute(sa.text("SET LOCAL statement_timeout = '15s'"))
    tables = sorted({t for t, _ in TIMESTAMPS} | {'messages','organizations','projects','tenant_workspaces'})
    # Identifiers come only from this fixed, reviewed source allowlist.
    bind.exec_driver_sql('LOCK TABLE '+', '.join('public.'+t for t in tables)+' IN SHARE ROW EXCLUSIVE MODE')
    for table, column in TIMESTAMPS:
        expected = bind.execute(sa.text(
            "SELECT NOT a.attnotnull AND a.atttypid='timestamptz'::regtype "
            "FROM pg_attribute a WHERE a.attrelid=CAST(:table AS regclass) "
            "AND a.attname=:column AND a.attnum>0 AND NOT a.attisdropped"
        ), {'table':'public.'+table, 'column':column}).scalar_one_or_none()
        if expected is not True:
            raise RuntimeError('Unexpected timestamp schema; explicit review required')
        if bind.exec_driver_sql('SELECT EXISTS (SELECT 1 FROM public.'+table+' WHERE '+column+' IS NULL)').scalar_one():
            raise RuntimeError('Historical timestamp NULLs require explicit data resolution')
    for table, name in [('organizations','fk_organizations_tenant_workspace_review'),
                        ('projects','fk_projects_tenant_workspace_review')]:
        conflict = bind.execute(sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint c WHERE c.conrelid=CAST(:table AS regclass) "
            "AND (c.conname=:name OR (c.contype='f' AND EXISTS(SELECT 1 FROM pg_attribute a "
            "WHERE a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey) AND a.attname='tenant_workspace_id'))))"
        ), {'table':'public.'+table,'name':name}).scalar_one()
        if conflict:
            raise RuntimeError('Existing tenant constraint requires explicit review')
        if bind.exec_driver_sql('SELECT EXISTS (SELECT 1 FROM public.'+table+' p LEFT JOIN public.tenant_workspaces t ON t.id=p.tenant_workspace_id WHERE p.tenant_workspace_id IS NOT NULL AND t.id IS NULL)').scalar_one():
            raise RuntimeError('Orphan tenant links require explicit data resolution')
    if bind.execute(sa.text("SELECT to_regclass('public.ix_messages_context_confirmed')")).scalar_one() is not None:
        raise RuntimeError('Existing index requires explicit review')
    bind.exec_driver_sql('SELECT context_confirmed FROM public.messages LIMIT 0')
    # All guards above precede every DDL operation; no backfill or type conversion.
    for table, column in TIMESTAMPS:
        op.alter_column(table,column,nullable=False,schema='public')
    op.create_foreign_key('fk_organizations_tenant_workspace_review','organizations','tenant_workspaces',['tenant_workspace_id'],['id'],source_schema='public',referent_schema='public',ondelete='SET NULL')
    op.create_foreign_key('fk_projects_tenant_workspace_review','projects','tenant_workspaces',['tenant_workspace_id'],['id'],source_schema='public',referent_schema='public',ondelete='SET NULL')
    op.create_index('ix_messages_context_confirmed','messages',['context_confirmed'],unique=False,schema='public')


def downgrade():
    raise RuntimeError('Schema weakening needs a separately reviewed rollback decision')
