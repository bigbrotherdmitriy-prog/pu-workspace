"""Whole-batch CAS/undo with real migrations and only synthetic financial data."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app import dds_budget_links as service
from app.models.audit_log import AuditLog
from app.models.execution_finance import CashFlowEntry, DdsBudgetLinkOperation
from app.models.project import Project
from app.models.user import User
from test_dds_budget_links import build_world, payload
from test_mvp4_finance_source_pins_postgres import _test_url


@pytest.fixture
def pg_world(monkeypatch):
    url = _test_url()  # Explicit opt-in and test database name guard.
    base = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "dds_links_" + uuid4().hex
    with base.begin() as connection:
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
            request = payload(world)
            identifiers = [row.id for row in world.rows]
            context = engine, world.document.id, user.id, request, identifiers, config
        # The migration gate drops FK constraints. No fixture session may keep
        # read locks on their referenced tables while that DDL is running.
        yield context
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def test_concurrent_apply_has_one_receipt_and_one_audit_per_link(pg_world):
    engine, document_id, user_id, request, ids, _ = pg_world
    barrier = Barrier(2)
    def run():
        with Session(engine) as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=20)
            return service.apply_budget_links(document_id, request, db, user)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        results = [future.result(timeout=40) for future in futures]
    assert results[0]["operation_id"] == results[1]["operation_id"]
    assert sorted(item["replayed"] for item in results) == [False, True]
    with Session(engine) as db:
        assert db.query(DdsBudgetLinkOperation).count() == 1
        assert db.query(AuditLog).filter_by(action="cash_flow_budget_link_applied").count() == len(ids)
        assert all(db.get(CashFlowEntry, i).record_version == 2 for i in ids)


def test_other_session_update_rejects_stale_preview_without_partial_writes(pg_world):
    engine, document_id, user_id, request, ids, _ = pg_world
    with Session(engine) as writer:
        row = writer.get(CashFlowEntry, ids[1])
        row.planned_amount += Decimal("0.01"); row.record_version += 1; writer.commit()
    with Session(engine) as db:
        with pytest.raises(HTTPException, match="CASH_FLOW_VERSION_MISMATCH"):
            service.apply_budget_links(document_id, request, db, db.get(User, user_id))
        assert all(db.get(CashFlowEntry, i).budget_line_id is None for i in ids)
        assert db.query(DdsBudgetLinkOperation).count() == 0


def test_audit_failure_rolls_back_links_receipt_and_trigger_revision(pg_world, monkeypatch):
    engine, document_id, user_id, request, ids, _ = pg_world
    with Session(engine) as db:
        before_revision = db.get(Project, request.project_id).cash_flow_revision
        original = service.AuditLog
        count = 0
        def fault(**values):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("synthetic-second-audit-fault")
            return original(**values)
        monkeypatch.setattr(service, "AuditLog", fault)
        with pytest.raises(RuntimeError, match="synthetic-second-audit-fault"):
            service.apply_budget_links(document_id, request, db, db.get(User, user_id))
        db.expire_all()
        assert db.get(Project, request.project_id).cash_flow_revision == before_revision
        assert db.query(DdsBudgetLinkOperation).count() == 0
        assert db.query(AuditLog).filter_by(action="cash_flow_budget_link_applied").count() == 0
        assert all(db.get(CashFlowEntry, i).budget_line_id is None and db.get(CashFlowEntry, i).record_version == 1 for i in ids)


def test_whole_undo_cas_and_parallel_retry(pg_world):
    engine, document_id, user_id, request, ids, _ = pg_world
    with Session(engine) as db:
        result = service.apply_budget_links(document_id, request, db, db.get(User, user_id))
    with Session(engine) as writer:
        row = writer.get(CashFlowEntry, ids[1]); row.note = "Synthetic edit"; row.record_version += 1; writer.commit()
    with Session(engine) as db:
        with pytest.raises(HTTPException, match="LINK_UNDO_STALE"):
            service.undo_budget_links(result["operation_id"], db, db.get(User, user_id))
        assert all(db.get(CashFlowEntry, i).budget_line_id is not None for i in ids)
        assert db.query(AuditLog).filter_by(action="cash_flow_budget_link_undone").count() == 0
    # Restore the exact saved state in this isolated fixture only, then test a
    # real concurrent undo retry (not a second per-row unlink operation).
    with Session(engine) as writer:
        row = writer.get(CashFlowEntry, ids[1]); row.note = None; row.record_version = 2; writer.commit()
    barrier = Barrier(2)
    def run():
        with Session(engine) as db:
            user = db.get(User, user_id); barrier.wait(timeout=20)
            return service.undo_budget_links(result["operation_id"], db, user)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=40) for future in [executor.submit(run) for _ in range(2)]]
    assert all(item["undone"] for item in results)
    assert sorted(item["replayed"] for item in results) == [False, True]
    with Session(engine) as db:
        assert all(db.get(CashFlowEntry, i).budget_line_id is None and db.get(CashFlowEntry, i).record_version == 3 for i in ids)
        assert db.query(AuditLog).filter_by(action="cash_flow_budget_link_undone").count() == len(ids)


def test_additive_migration_and_history_preserving_downgrade(pg_world):
    engine, document_id, user_id, request, ids, config = pg_world
    query = text("SELECT id,planned_amount,actual_amount,planned_date,status,budget_line_id,record_version,entry_kind FROM cash_flow_entries ORDER BY id")
    with engine.connect() as connection:
        before = connection.execute(query).all()
    # Empty link journal is compatible with a downgrade; pre-existing article
    # budget history is not touched by this migration.
    command.downgrade(config, "d021a6c0b001")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(query).all() == before
    with Session(engine) as db:
        result = service.apply_budget_links(document_id, request, db, db.get(User, user_id))
        service.undo_budget_links(result["operation_id"], db, db.get(User, user_id))
    with pytest.raises(RuntimeError, match="DDS_BUDGET_LINK_DOWNGRADE_BLOCKED"):
        command.downgrade(config, "d021a6c0b001")
    with Session(engine) as db:
        assert db.query(DdsBudgetLinkOperation).count() == 1
        assert db.query(DdsBudgetLinkOperation).one().undone_at is not None
        assert all(db.get(CashFlowEntry, i).budget_line_id is None for i in ids)
