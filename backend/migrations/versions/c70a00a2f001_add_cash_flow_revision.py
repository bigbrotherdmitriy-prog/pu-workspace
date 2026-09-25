"""Atomic DDS snapshot revision for every writer, including bulk SQL/imports.

Revision ID: c70a00a2f001
Revises: c70a00a1f001
"""
from alembic import op
import sqlalchemy as sa

revision = "c70a00a2f001"
down_revision = "c70a00a1f001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("cash_flow_revision", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("projects", sa.Column("cash_flow_revision_txid", sa.BigInteger(), nullable=True))
    op.execute("""
        CREATE FUNCTION bump_cash_flow_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE old_project integer; new_project integer; affected integer;
        BEGIN
          IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN RETURN NULL; END IF;
          IF TG_OP <> 'INSERT' THEN old_project := OLD.project_id; END IF;
          IF TG_OP <> 'DELETE' THEN new_project := NEW.project_id; END IF;
          FOR affected IN SELECT DISTINCT p FROM unnest(ARRAY[old_project,new_project]) AS p
                          WHERE p IS NOT NULL ORDER BY p LOOP
            UPDATE projects SET cash_flow_revision = cash_flow_revision + 1,
                                cash_flow_revision_txid = txid_current()
            WHERE id = affected AND cash_flow_revision_txid IS DISTINCT FROM txid_current();
          END LOOP;
          RETURN NULL;
        END $$;
        CREATE TRIGGER cash_flow_revision_changed AFTER INSERT OR UPDATE OR DELETE
        ON cash_flow_entries FOR EACH ROW EXECUTE FUNCTION bump_cash_flow_revision();
        CREATE FUNCTION bump_cash_flow_currency_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.currency IS DISTINCT FROM OLD.currency
             AND NEW.cash_flow_revision_txid IS DISTINCT FROM txid_current() THEN
            NEW.cash_flow_revision := OLD.cash_flow_revision + 1;
            NEW.cash_flow_revision_txid := txid_current();
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER cash_flow_currency_revision_changed BEFORE UPDATE OF currency
        ON projects FOR EACH ROW EXECUTE FUNCTION bump_cash_flow_currency_revision();
    """)


def downgrade():
    op.execute("DROP TRIGGER cash_flow_currency_revision_changed ON projects")
    op.execute("DROP FUNCTION bump_cash_flow_currency_revision()")
    op.execute("DROP TRIGGER cash_flow_revision_changed ON cash_flow_entries")
    op.execute("DROP FUNCTION bump_cash_flow_revision()")
    op.drop_column("projects", "cash_flow_revision_txid")
    op.drop_column("projects", "cash_flow_revision")
