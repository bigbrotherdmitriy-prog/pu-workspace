"""Opt-in real PostgreSQL gate for the MVP4 payment ledger."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.execution_finance import PaymentConfirmation, confirm_payment
from app.models.execution_finance import CashFlowEntry, PaymentEvent
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from test_mvp3_hardening_postgres import mvp3_pg_engine


def _payment_world(engine, *, title: str) -> tuple[int, int]:
    with Session(engine) as db:
        organization = Organization(name=f"{title} tenant")
        user = User(name="Finance manager", email=f"finance-{uuid4().hex}@example.test", is_admin=False)
        db.add_all([organization, user]); db.flush()
        project = Project(name=f"{title} project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        item = CashFlowEntry(
            project_id=project.id, direction="outflow", title=title,
            planned_date=date(2026, 9, 10), planned_amount=Decimal("100.00"),
            actual_amount=Decimal("0.00"), currency="RUB", status="approved",
        )
        db.add(item); db.commit()
        return item.id, user.id


def _confirm(engine, item_id: int, user_id: int, *, amount: str, key: str):
    try:
        with Session(engine) as db:
            return confirm_payment(
                item_id,
                PaymentConfirmation(
                    actual_amount=amount,
                    actual_date="2026-09-11",
                    idempotency_key=key,
                ),
                db,
                db.get(User, user_id),
            )
    except HTTPException as error:
        return error.status_code


def test_postgres_concurrent_identical_payment_confirmation_creates_one_event(mvp3_pg_engine):
    item_id, user_id = _payment_world(mvp3_pg_engine, title="Identical payment")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in (
            pool.submit(_confirm, mvp3_pg_engine, item_id, user_id, amount="100.00", key="same-payment-key"),
            pool.submit(_confirm, mvp3_pg_engine, item_id, user_id, amount="100.00", key="same-payment-key"),
        )]

    assert all(isinstance(result, dict) and result["status"] == "paid" for result in results)
    with Session(mvp3_pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(PaymentEvent).where(
            PaymentEvent.cash_flow_entry_id == item_id,
        )) == 1
        item = db.get(CashFlowEntry, item_id)
        assert item.actual_amount == Decimal("100.00") and item.status == "paid"


def test_postgres_concurrent_payload_conflict_has_one_winner_and_one_409(mvp3_pg_engine):
    item_id, user_id = _payment_world(mvp3_pg_engine, title="Conflicting payment")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in (
            pool.submit(_confirm, mvp3_pg_engine, item_id, user_id, amount="100.00", key="conflict-payment-key"),
            pool.submit(_confirm, mvp3_pg_engine, item_id, user_id, amount="101.00", key="conflict-payment-key"),
        )]

    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 1
    with Session(mvp3_pg_engine) as db:
        events = list(db.scalars(select(PaymentEvent).where(
            PaymentEvent.cash_flow_entry_id == item_id,
        )))
        assert len(events) == 1
        item = db.get(CashFlowEntry, item_id)
        assert item.actual_amount == events[0].amount and item.status == "paid"
