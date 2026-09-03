"""Opt-in real PostgreSQL barriers. Never substitutes SQLite for row locking.

Set PUW_V54_CONTEXT_TEST_DATABASE_URL to an explicitly disposable localhost
database puw_v54_test_*. Each test creates its own context_test_<uuid> schema;
schemas are deliberately left for inspection, not dropped by this test.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import os
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from app.database import Base
from app.context_communication import ContextError
from app.models.ai_secretary import Message
from app.models.v54_pilot import ContextRelation
from test_v54_context_communication import (
    confirmation, confirmed, completed_receipt_fixture, new_sources, obj, propose, service, vp,
)
from v54_pilot_fixture import scope, seed, uid


@pytest.fixture
def pg_engine():
    raw = os.getenv("PUW_V54_CONTEXT_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL concurrency NOT RUN: no explicit isolated database")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql", "PostgreSQL required"
    assert url.host in {"localhost", "127.0.0.1", "::1"}, "Local isolated database required"
    assert url.database and url.database.startswith("puw_v54_test_"), "Test database name required"
    # No URL, password or DSN is printed. No production env or default URL.
    base = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "context_test_" + uuid4().hex
    with base.begin() as conn:
        conn.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    with Session(engine) as db, db.begin():
        seed(db)
    try:
        yield engine
    finally:
        base.dispose()


@pytest.mark.parametrize("mode", ["confirm", "correct"])
def test_postgres_concurrent_context_cas_one_winner(pg_engine, mode):
    with Session(pg_engine) as db, db.begin():
        pins = propose(db) if mode == "confirm" else confirmed(db)
        command = confirmation(db, pins, 1 if mode == "confirm" else 2)
    locked, release, second_started = Event(), Event(), Event()
    first = service()
    original = first._cas_message

    def pause_after_lock(db, msg, expected, **values):
        locked.set()
        assert release.wait(10), "test barrier timed out"
        return original(db, msg, expected, **values)
    first._cas_message = pause_after_lock

    def run(svc, second=False):
        try:
            with Session(pg_engine) as db, db.begin():
                if second:
                    db.get(Message, 6)  # A preloaded identity map must not defeat CAS.
                    second_started.set()
                if mode == "confirm":
                    svc.confirm(db, scope=scope(), command=command)
                else:
                    svc.correct(db, scope=scope(), command=command, project=vp("project", 4),
                                contract=None, evidence=(vp("evidence", uid(16)),))
            return "committed"
        except ContextError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, first)
        try:
            assert locked.wait(10)
            b = pool.submit(run, service(), True)
            assert second_started.wait(5)
            with pytest.raises(TimeoutError):
                b.result(timeout=0.2)  # Must block on the real database owner lock.
        finally:
            release.set()
        assert a.result(timeout=10) == "committed"
        assert b.result(timeout=10) == "context_version_conflict"
    with Session(pg_engine) as db:
        assert db.get(Message, 6).context_version == (2 if mode == "confirm" else 3)
        counts = dict(db.execute(select(ContextRelation.state, func.count()).group_by(ContextRelation.state)).all())
        assert counts == ({"confirmed": 2} if mode == "confirm" else {"confirmed": 1, "superseded": 2})


@pytest.mark.parametrize("operation", ["ingress", "receipt"])
def test_postgres_duplicate_consumers(pg_engine, operation):
    with Session(pg_engine) as db, db.begin():
        if operation == "ingress":
            source, attachment = new_sources(db)
        else:
            completed_receipt_fixture(db)
    go = Event()

    def run():
        assert go.wait(5)
        with Session(pg_engine) as db, db.begin():
            svc = service()
            if operation == "ingress":
                return svc.register(db, scope=scope(), mailbox=vp("mail_connection", uid(11)),
                                    source=source, attachment=attachment)
            return svc.project_receipt(db, scope=scope(), receipt=obj("receipt", uid(25)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run), pool.submit(run)
        go.set()
        assert a.result(timeout=15) == b.result(timeout=15)
    with Session(pg_engine) as db:
        if operation == "ingress":
            assert db.scalar(select(func.count()).select_from(Message)) == 2
        else:
            from app.models.task import Task
            from app.models.v54_pilot import ActionReceipt
            assert db.scalar(select(func.count()).select_from(ContextRelation)) == 1
            assert db.scalar(select(func.count()).select_from(ActionReceipt)) == 1
            assert db.scalar(select(func.count()).select_from(Task)) == 1
