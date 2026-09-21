"""Opt-in real PostgreSQL gate for the MVP4 payment ledger."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.execution_finance import PaymentConfirmation, StatusUpdate, confirm_payment, update_status
from app.models.execution_finance import AcceptanceAct, BudgetLine, CashFlowEntry, PaymentEvent
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


def _act_projection_world(engine) -> tuple[int, list[int], int]:
    with Session(engine) as db:
        organization = Organization(name=f"Concurrent act tenant {uuid4().hex}")
        user = User(name="Act manager", email=f"act-{uuid4().hex}@example.test", is_admin=False)
        db.add_all([organization, user]); db.flush()
        project = Project(name="Concurrent act project", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="manager"))
        budget = BudgetLine(
            project_id=project.id, category="Works", description="Concurrent works",
            planned_amount=Decimal("100.00"), committed_amount=Decimal("0.00"),
            actual_amount=Decimal("0.00"), forecast_amount=Decimal("100.00"),
            currency="RUB", status="approved",
        )
        db.add(budget); db.flush()
        acts = [AcceptanceAct(
            project_id=project.id, budget_line_id=budget.id, number=f"A-{index}",
            title=f"Concurrent act {index}", amount=amount, currency="RUB", status="approved",
        ) for index, amount in enumerate((Decimal("40.00"), Decimal("35.00")), 1)]
        db.add_all(acts); db.commit()
        return budget.id, [row.id for row in acts], user.id


def _sign_act(engine, act_id: int, user_id: int):
    with Session(engine) as db:
        return update_status("acts", act_id, StatusUpdate(status="signed"), db, db.get(User, user_id))


def test_postgres_concurrent_act_signatures_project_exact_sum(mvp3_pg_engine):
    budget_id, act_ids, user_id = _act_projection_world(mvp3_pg_engine)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in (
            pool.submit(_sign_act, mvp3_pg_engine, act_ids[0], user_id),
            pool.submit(_sign_act, mvp3_pg_engine, act_ids[1], user_id),
        )]

    assert {result["status"] for result in results} == {"signed"}
    with Session(mvp3_pg_engine) as db:
        budget = db.get(BudgetLine, budget_id)
        assert budget.actual_amount == Decimal("75.00")
        assert db.scalar(select(func.count()).select_from(AcceptanceAct).where(
            AcceptanceAct.budget_line_id == budget_id,
            AcceptanceAct.status == "signed",
        )) == 2
