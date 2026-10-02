"""Real PostgreSQL transaction/CAS gates; disposable schema, synthetic data."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.api import execution_finance as api
from app.models.audit_log import AuditLog
from app.models.execution_finance import CashFlowEntry
from app.models.user import User
from test_dds_confirmation import build_world, batch
from test_mvp4_finance_source_pins_postgres import _test_url


@pytest.fixture
def pg_world(monkeypatch):
    url = _test_url()
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "dds_confirm_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    scoped = make_url(url).update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=10s"})
    engine = create_engine(scoped, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "head")
        with Session(engine) as db:
            user = User(name="Synthetic manager", email=f"{uuid4().hex}@example.test", is_admin=True)
            db.add(user); db.flush()
            world = build_world(db, user)
            world.row.entry_kind = "legacy_unclassified"; db.commit()
            request = batch(world, [world.row])
            identifiers = (user.id, world.row.id)
        yield engine, request, identifiers
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def test_other_session_change_rejects_stale_batch_without_writes(pg_world):
    engine, request, (user_id, row_id) = pg_world
    with Session(engine) as writer:
        row = writer.get(CashFlowEntry, row_id); row.record_version += 1; writer.commit()
    with Session(engine) as db:
        with pytest.raises(HTTPException) as error:
            api.confirm_cash_flow_batch(request, db, db.get(User, user_id))
        assert error.value.status_code == 409
        assert db.get(CashFlowEntry, row_id).status == "proposed"
        assert db.query(AuditLog).count() == 0


def test_commit_fault_rolls_back_flushed_approval_classification_and_audit(pg_world):
    engine, request, (user_id, row_id) = pg_world
    with Session(engine) as db:
        def fail_commit(session):
            raise RuntimeError("synthetic-before-commit-fault")
        event.listen(db, "before_commit", fail_commit)
        with pytest.raises(HTTPException) as error:
            api.confirm_cash_flow_batch(request, db, db.get(User, user_id))
        assert error.value.status_code == 500
        row = db.get(CashFlowEntry, row_id)
        assert row.status == "proposed" and row.entry_kind == "legacy_unclassified" and row.record_version == 1
        assert db.query(AuditLog).count() == 0


def test_concurrent_confirmation_has_one_commit_and_one_version_increment(pg_world):
    engine, request, (user_id, row_id) = pg_world
    barrier = Barrier(2)
    def run():
        with Session(engine) as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=20)
            try:
                return api.confirm_cash_flow_batch(request, db, user)["confirmed_count"]
            except HTTPException as exc:
                return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        assert sorted(future.result(timeout=40) for future in futures) == [1, 409]
    with Session(engine) as db:
        row = db.get(CashFlowEntry, row_id)
        assert row.status == "approved" and row.entry_kind == "plan_forecast" and row.record_version == 2
        assert db.query(AuditLog).count() == 1
