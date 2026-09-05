"""Real PostgreSQL finance concurrency, only on a pre-migrated owned test DB.

No provider or payment API: these commands record explicit human confirmations.
The CI orchestrator creates, migrates and removes the complete test database.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import os
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.execution_finance import (
    CashFlowCreate, PaymentConfirmation, PaymentCorrection, StatusUpdate,
    confirm_payment, correct_payment, create_cash_flow, update_status,
)
from app.models.audit_log import AuditLog
from app.models.execution_finance import CashFlowEntry, CashFlowFactHistory
from app.models.project_member import ProjectMember
from app.models.user import User
from app.schema import CURRENT_SCHEMA_REVISION
from test_mvp4_budget_dds import _chain


def _safe_url(value):
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db"} or (
        parsed.host == "postgres" and os.getenv("GITHUB_ACTIONS") == "true"
    )
    assert (parsed.database or "").startswith("puw_mvp4_test_")
    assert not parsed.query
    return value


@pytest.fixture
def pg_finance():
    value = os.getenv("PUW_MVP4_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: isolated MVP4 PostgreSQL is not configured")
    engine = create_engine(_safe_url(value), hide_parameters=True, connect_args={
        "connect_timeout": 5, "options": "-clock_timeout=8000 -cstatement_timeout=15000",
    })
    try:
        with engine.connect() as db:
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
        yield engine
    finally:
        engine.dispose()


def _approved_entry(engine):
    with Session(engine) as db:
        actor = User(name="Synthetic finance owner", email=f"finance-{uuid4().hex}@example.test", is_admin=False)
        db.add(actor)
        db.flush()
        project, contract, stage, task, budget, document, _version = _chain(db, actor)
        db.add(ProjectMember(project_id=project.id, user_id=actor.id, role="owner"))
        db.flush()
        created = create_cash_flow(CashFlowCreate(
            project_id=project.id, contract_id=contract.id, schedule_item_id=stage.id,
            task_id=task.id, budget_line_id=budget.id, source_document_id=document.id,
            direction="outflow", title="Synthetic supplier confirmation",
            planned_date="2026-09-10", planned_amount="75000", confidence="0.62",
        ), db, actor)
        entry_id = created["id"]
        update_status("cash-flow", entry_id, StatusUpdate(status="approved"), db, actor)
        entry = db.get(CashFlowEntry, entry_id)
        assert entry.actual_amount == 0 and entry.review_status == "confirmed"
        return entry_id, actor.id, entry.record_version


def _race(engine, actor_id, calls):
    barrier = Barrier(len(calls))

    def invoke(call):
        try:
            with Session(engine) as db:
                actor = db.get(User, actor_id)
                barrier.wait(timeout=8)
                try:
                    return {"result": call(db, actor)}
                except HTTPException as exc:
                    db.rollback()
                    return {"http_status": exc.status_code}
        except Exception as exc:
            # Do not publish SQL, DSN, exception messages or fixture contents.
            return {"failure_type": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        results = list(executor.map(invoke, calls))
    assert all("failure_type" not in result for result in results), results
    return results


def test_postgres_concurrent_payment_confirmation_creates_one_fact(pg_finance):
    entry_id, actor_id, version = _approved_entry(pg_finance)
    payload = PaymentConfirmation(expected_record_version=version, actual_amount="74250", actual_date="2026-09-11")
    results = _race(pg_finance, actor_id, [
        lambda db, actor: confirm_payment(entry_id, payload, db, actor),
        lambda db, actor: confirm_payment(entry_id, payload, db, actor),
    ])
    assert all("result" in result for result in results)
    assert sorted(result["result"]["already_confirmed"] for result in results) == [False, True]
    with Session(pg_finance) as db:
        entry = db.get(CashFlowEntry, entry_id)
        assert entry.actual_amount == Decimal("74250") and entry.record_version == version + 1
        assert db.scalar(select(func.count()).select_from(CashFlowFactHistory).where(
            CashFlowFactHistory.cash_flow_entry_id == entry_id)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.entity_type == "cash_flow", AuditLog.entity_id == entry_id,
            AuditLog.action == "cash_flow_payment_confirmed")) == 1


def test_postgres_competing_payment_corrections_are_cas_serialized(pg_finance):
    entry_id, actor_id, version = _approved_entry(pg_finance)
    with Session(pg_finance) as db:
        confirm_payment(entry_id, PaymentConfirmation(expected_record_version=version,
                        actual_amount="74250", actual_date="2026-09-11"), db, db.get(User, actor_id))
    def correction(amount):
        return lambda db, actor: correct_payment(entry_id, PaymentCorrection(
            expected_record_version=version + 1, expected_actual_amount="74250",
            expected_actual_date="2026-09-11", actual_amount=amount,
            actual_date="2026-09-12", reason="Synthetic explicit correction",
        ), db, actor)
    results = _race(pg_finance, actor_id, [correction("73000"), correction("72000")])
    assert sum("result" in result for result in results) == 1
    assert [result["http_status"] for result in results if "http_status" in result] == [409]
    with Session(pg_finance) as db:
        entry = db.get(CashFlowEntry, entry_id)
        assert entry.record_version == version + 2 and entry.actual_date == date(2026, 9, 12)
        assert entry.actual_amount in {Decimal("73000"), Decimal("72000")}
        assert db.scalar(select(func.count()).select_from(CashFlowFactHistory).where(
            CashFlowFactHistory.cash_flow_entry_id == entry_id)) == 2


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://synthetic@remote.example.test/puw_mvp4_test_one",
    "postgresql+psycopg://synthetic@localhost/production",
    "postgresql+psycopg://synthetic@localhost/puw_mvp4_test_one?sslmode=disable",
])
def test_finance_postgres_guard_refuses_nonisolated_targets(url):
    with pytest.raises(AssertionError):
        _safe_url(url)


def test_finance_fixture_uses_real_permissions_and_valid_links(db_session):
    entry_id, actor_id, version = _approved_entry(db_session.get_bind())
    entry = db_session.get(CashFlowEntry, entry_id)
    assert entry.status == "approved" and version >= 1
    assert db_session.scalar(select(ProjectMember.role).where(
        ProjectMember.project_id == entry.project_id, ProjectMember.user_id == actor_id)) == "owner"
