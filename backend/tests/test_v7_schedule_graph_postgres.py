"""Owned migrated PostgreSQL only; no SQLite concurrency or production database."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from app.models.audit_log import AuditLog
from app.models.execution_finance import ScheduleBaseline, ScheduleItem
from app.models.user import User
import app.api.execution_finance as api
from test_v7_schedule_graph_api import world, request


def _owned_url(value):
    try:
        url = make_url(value)
        hosts = {"localhost", "127.0.0.1", "::1", "db"}
        if os.getenv("GITHUB_ACTIONS") == "true": hosts.add("postgres")
        valid = (url.get_backend_name() == "postgresql" and url.host in hosts and not url.query
                 and re.fullmatch(r"puw_mvp4_test_[a-z0-9_]+", url.database or ""))
    except Exception:
        raise ValueError("owned_schedule_postgres_required") from None
    if not valid:
        raise ValueError("owned_schedule_postgres_required")
    return url.set(drivername="postgresql+psycopg") if url.drivername == "postgresql" else url


@pytest.fixture
def pg_graph(monkeypatch):
    value = os.getenv("PUW_MVP4_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: owned MVP4 PostgreSQL is not configured")
    url = _owned_url(value)
    schema = "schedule_graph_test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    engine = None
    created = False
    try:
        with admin.begin() as db:
            assert db.scalar(text("SELECT current_database()")) == url.database
            assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        scoped = url.update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000"})
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        config.set_main_option("sqlalchemy.url", scoped.render_as_string(hide_password=False).replace("%", "%%"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        command.upgrade(config, "a54f001c0a20")
        engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
        with Session(engine) as db:
            def factory():
                user = User(name="Synthetic graph manager", email="pg-graph@example.test", is_admin=False)
                db.add(user); db.flush(); return user
            user, baseline, rows = world(db, factory)
            ids = (user.id, baseline.id, [row.id for row in rows])
        yield engine, config, ids
    finally:
        if engine is not None: engine.dispose()
        if created:
            assert re.fullmatch(r"schedule_graph_test_[0-9a-f]{32}", schema)
            with admin.begin() as db:
                db.execute(text("SET LOCAL lock_timeout = '5000ms'"))
                db.execute(text("SET LOCAL statement_timeout = '10000ms'"))
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_graph_cas_allows_one_complete_winner(pg_graph):
    engine, _, (actor_id, baseline_id, row_ids) = pg_graph
    barrier = Barrier(2, timeout=10)
    def invoke(start):
        try:
            with Session(engine) as db:
                user = db.get(User, actor_id)
                rows = [db.get(ScheduleItem, identifier) for identifier in row_ids]
                payload = request(rows, project_start=start)
                barrier.wait()
                try:
                    result = api.put_schedule_graph(baseline_id, payload, db, user)
                    return (200, result["project_start"])
                except HTTPException as error:
                    db.rollback()
                    return (error.status_code, None)
        except Exception:
            return ("runtime_failure", None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, day) for day in ("2026-09-01", "2026-09-10")]
        results = [future.result(timeout=25) for future in futures]
    assert sorted(str(result[0]) for result in results) == ["200", "409"]
    winner = next(value for status, value in results if status == 200)
    with Session(engine) as db:
        result = api.get_schedule_graph(baseline_id, db, db.get(User, actor_id))
        assert result["graph_revision"] == 2 and result["project_start"] == winner
        assert result["items"][0]["planned_start"] == winner
        assert db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "schedule_graph_saved")) == 1


def test_postgres_upgrade_legacy_and_safe_downgrade(pg_graph):
    engine, config, (_, baseline_id, _) = pg_graph
    # Seed at a20 defaults then downgrade to a19: the real a19 tables contain
    # unchanged legacy rows, which are the input to the tested a19->a20 upgrade.
    with engine.begin() as db:
        db.execute(text("UPDATE schedule_items SET planned_finish='2026-09-17', actual_progress=35"))
        before = list(db.execute(text("SELECT id,baseline_id,title,planned_start,planned_finish,actual_progress FROM schedule_items ORDER BY id")))
    command.downgrade(config, "a54f001c0a19")
    command.upgrade(config, "a54f001c0a20")
    with engine.connect() as db:
        after = list(db.execute(text("SELECT id,baseline_id,title,planned_start,planned_finish,actual_progress FROM schedule_items ORDER BY id")))
        assert before == after
        assert db.scalar(text("SELECT count(*) FROM schedule_items WHERE duration_days IS NOT NULL OR is_milestone IS NOT NULL")) == 0
        assert db.scalar(text("SELECT graph_revision FROM schedule_baselines WHERE id=:id"), {"id": baseline_id}) == 1
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "a54f001c0a20"


@pytest.mark.parametrize("intent", ["active_graph", "legacy_intent"])
def test_postgres_downgrade_refuses_graph_intent(pg_graph, intent):
    engine, config, (actor_id, baseline_id, row_ids) = pg_graph
    if intent == "active_graph":
        with Session(engine) as db:
            api.put_schedule_graph(baseline_id, request([db.get(ScheduleItem, identifier) for identifier in row_ids]),
                                   db, db.get(User, actor_id))
    else:
        with engine.begin() as db:
            db.execute(text("UPDATE schedule_items SET not_before_date='2026-09-17'"))
    try:
        command.downgrade(config, "a54f001c0a19")
    except DBAPIError as error:
        assert getattr(error.orig, "sqlstate", None) == "P0001"
        assert error.orig.diag.message_primary == "schedule_graph_downgrade_requires_verified_restore"
    else:
        pytest.fail("downgrade_did_not_refuse_graph_intent")
    with engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "a54f001c0a20"
        assert db.scalar(text("SELECT count(*) FROM schedule_items")) == 2
        if intent == "active_graph":
            assert db.scalar(text("SELECT planning_mode FROM schedule_baselines")) == "calendar_graph"
            assert db.scalar(text("SELECT sum(duration_days) FROM schedule_items")) == 5
        else:
            assert db.scalar(text("SELECT count(*) FROM schedule_items WHERE not_before_date='2026-09-17'")) == 2


@pytest.mark.parametrize("value", [
    "postgresql://test@remote.invalid/puw_mvp4_test_graph",
    "postgresql://test@localhost/production",
    "postgresql://test@localhost/puw_mvp4_test_graph?options=unsafe",
    "sqlite://", "not-a-url",
])
def test_schedule_pg_guard_rejects_unsafe_urls(value):
    with pytest.raises(ValueError, match="^owned_schedule_postgres_required$"):
        _owned_url(value)
