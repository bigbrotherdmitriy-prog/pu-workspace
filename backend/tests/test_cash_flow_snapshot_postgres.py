"""Real migration/transaction gates; CI requires all cases to run, never skip."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.cash_flow_snapshot import read_snapshot
from test_mvp4_finance_source_pins_postgres import _test_url


@pytest.fixture
def pg(monkeypatch):
    base = _test_url()
    schema = "dds_snapshot_" + uuid4().hex
    admin = create_engine(base, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(base).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "c70a00a1f001")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO organizations(id,name) VALUES (1,'Snapshot org')"))
            conn.execute(text("INSERT INTO projects(id,name,organization_id) VALUES (1,'One',1),(2,'Two',1)"))
            conn.execute(text("""INSERT INTO cash_flow_entries
                (id,project_id,title,direction,planned_date,planned_amount,actual_amount,currency,status)
                VALUES (1,1,'Legacy','outflow','2026-01-01',1500.50,0,'RUB','approved')"""))
        command.upgrade(config, "c70a00a2f001")
        yield engine, config
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def view(engine):
    with Session(engine) as db:
        return read_snapshot(db, project_id=1, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))


def revisions(engine):
    with engine.connect() as conn:
        return list(conn.execute(text("SELECT cash_flow_revision FROM projects ORDER BY id")).scalars())


def test_revision_migration_backfill_and_reversible_downgrade(pg):
    engine, config = pg
    assert revisions(engine) == [0, 0]
    assert view(engine)["summary"]["totals"]["plan"]["outflow"]["amount"] == "1500.50"
    command.downgrade(config, "c70a00a1f001")
    with engine.connect() as conn:
        assert str(conn.execute(text("SELECT planned_amount FROM cash_flow_entries WHERE id=1")).scalar_one()) == "1500.50"
    command.upgrade(config, "c70a00a2f001")
    assert revisions(engine) == [0, 0]


def test_revision_once_per_transaction_bulk_insert_update_delete_and_rollback(pg):
    engine, _ = pg
    before = view(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE cash_flow_entries SET planned_amount=1500.51 WHERE id=1"))
        conn.execute(text("UPDATE cash_flow_entries SET category='Updated' WHERE id=1"))
        conn.execute(text("""INSERT INTO cash_flow_entries
            (id,project_id,title,direction,planned_date,planned_amount,actual_amount,currency,status)
            SELECT n,1,'Batch','outflow','2026-01-02',0.01,0,'RUB','proposed' FROM generate_series(2,4) AS n"""))
    assert revisions(engine) == [1, 0]
    assert before["snapshot_hash"] != view(engine)["snapshot_hash"]
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM cash_flow_entries WHERE project_id=1"))
        conn.rollback()
    assert revisions(engine) == [1, 0]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM cash_flow_entries WHERE title='Batch'"))
    assert revisions(engine) == [2, 0]
    with engine.begin() as conn:
        conn.execute(text("UPDATE cash_flow_entries SET title=title WHERE id=1"))
    assert revisions(engine) == [2, 0]


def test_move_between_projects_invalidates_both_and_currency_change(pg):
    engine, _ = pg
    with engine.begin() as conn:
        conn.execute(text("UPDATE cash_flow_entries SET project_id=2 WHERE id=1"))
    assert revisions(engine) == [1, 1]
    with engine.begin() as conn:
        conn.execute(text("UPDATE projects SET currency='USD' WHERE id=1"))
    assert revisions(engine) == [2, 1]


def test_snapshot_does_not_mix_uncommitted_amount_and_revision(pg):
    engine, _ = pg
    before = view(engine)
    with engine.connect() as writer:
        writer.execute(text("UPDATE cash_flow_entries SET planned_amount=2000.01 WHERE id=1"))
        assert view(engine) == before
        writer.commit()
    after = view(engine)
    assert after["snapshot_revision"] == 1
    assert after["summary"]["totals"]["plan"]["outflow"]["amount"] == "2000.01"


def test_concurrent_writers_no_lost_revision_and_snapshot_consistency(pg):
    engine, _ = pg
    barrier = Barrier(3)
    def writer():
        barrier.wait(timeout=10)
        for _ in range(8):
            with engine.begin() as conn:
                conn.execute(text("UPDATE cash_flow_entries SET planned_amount=planned_amount+0.01 WHERE id=1"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(writer), pool.submit(writer)]
        barrier.wait(timeout=10)
        for _ in range(20):
            current = view(engine)
            assert int(current["summary"]["totals"]["plan"]["outflow"]["amount_minor"]) == 150050 + current["snapshot_revision"]
        for future in futures:
            future.result(timeout=20)
    assert revisions(engine) == [16, 0]


def test_read_only_database_transaction_supports_snapshot(pg):
    engine, _ = pg
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        first = read_snapshot(db, project_id=1, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
        second = read_snapshot(db, project_id=1, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
        assert first == second
