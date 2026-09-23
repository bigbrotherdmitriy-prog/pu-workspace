"""Opt-in real PostgreSQL gate for the MVP4 payment ledger."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from datetime import date
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.execution_finance import (
    ActCreate, CashFlowPlanMutationRequest, InvoiceExtractionConfirm,
    PaymentConfirmation, StatusUpdate, confirm_invoice_extraction,
    confirm_payment, create_act, mutate_cash_flow_plan, update_status,
)
from app.api.organizations_contracts import (
    ContractBudgetProposalUpdate, confirm_contract_budget_proposal,
    create_contract_budget_proposal, update_contract_budget_proposal,
)
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import (
    AcceptanceAct, BudgetLine, CashFlowEntry, CashFlowPlanMutation, CostCategory,
    InvoiceExtractionProposal, PaymentEvent, ScheduleBaseline, ScheduleItem,
)
from app.models.organization_contract import Contract, Organization
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


def _mutate_plan(engine, item_id: int, user_id: int, *, key: str):
    with Session(engine) as db:
        return mutate_cash_flow_plan(
            item_id,
            CashFlowPlanMutationRequest(
                operation="move",
                planned_date="2026-10-10",
                planned_amount="100.00",
                expected_record_version=1,
                idempotency_key=key,
            ),
            db,
            db.get(User, user_id),
        )


def test_postgres_concurrent_identical_plan_mutation_replays_one_receipt(mvp3_pg_engine):
    item_id, user_id = _payment_world(mvp3_pg_engine, title="Concurrent plan move")
    with Session(mvp3_pg_engine) as db:
        item = db.get(CashFlowEntry, item_id)
        item.status = "proposed"
        db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in (
            pool.submit(_mutate_plan, mvp3_pg_engine, item_id, user_id, key="same-plan-move-key"),
            pool.submit(_mutate_plan, mvp3_pg_engine, item_id, user_id, key="same-plan-move-key"),
        )]

    assert results[0]["mutation_id"] == results[1]["mutation_id"]
    assert {result["replayed"] for result in results} == {False, True}
    with Session(mvp3_pg_engine) as db:
        assert db.scalar(select(func.count()).select_from(CashFlowPlanMutation).where(
            CashFlowPlanMutation.cash_flow_entry_id == item_id,
            CashFlowPlanMutation.idempotency_key == "same-plan-move-key",
        )) == 1
        item = db.get(CashFlowEntry, item_id)
        assert item.planned_date == date(2026, 10, 10)
        assert item.record_version == 2


def test_postgres_canonical_contract_schedule_budget_invoice_payment_and_act_chain(mvp3_pg_engine):
    with Session(mvp3_pg_engine) as db:
        suffix = uuid4().hex
        organization = Organization(name=f"Canonical chain {suffix}")
        manager = User(name="Canonical manager", email=f"canonical-{suffix}@example.test", is_admin=False)
        db.add_all([organization, manager]); db.flush()
        project = Project(name="Canonical chain", organization_id=organization.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=manager.id, role="manager"))
        contract = Contract(
            project_id=project.id, number="CHAIN-PG", title="Canonical contract",
            contract_kind="supply", amount=Decimal("1000"), status="active",
        )
        category = CostCategory(
            organization_id=organization.id, name="Прямые", normalized_name=f"direct-{suffix}", is_active=True,
        )
        db.add_all([contract, category]); db.flush()

        budget_proposal = create_contract_budget_proposal(project.id, contract.id, db, manager)
        update_contract_budget_proposal(
            budget_proposal["id"],
            ContractBudgetProposalUpdate(selected_cost_category_id=category.id),
            db, manager,
        )
        confirmed_budget = confirm_contract_budget_proposal(budget_proposal["id"], db, manager)
        budget = db.get(BudgetLine, confirmed_budget["created_budget_line_id"])
        baseline = ScheduleBaseline(
            project_id=project.id, contract_id=contract.id, created_by_user_id=manager.id,
            name="Canonical GPR", version=1, status="approved",
        )
        db.add(baseline); db.flush()
        stage = ScheduleItem(project_id=project.id, baseline_id=baseline.id, title="Canonical stage")
        document = Document(
            project_id=project.id, name="invoice.pdf", source="local_upload",
            status="analyzed", current_version=1,
        )
        db.add_all([stage, document]); db.flush()
        content = "Поставка материалов. Итого 300 руб. Оплатить 25.09.2026."
        version = DocumentVersion(document_id=document.id, version_number=1, content=content)
        db.add(version); db.flush()
        invoice = InvoiceExtractionProposal(
            project_id=project.id, source_document_id=document.id,
            source_document_version_id=version.id,
            source_document_sha256=hashlib.sha256(content.encode()).hexdigest(),
            amount=Decimal("300"), currency="RUB", payment_purpose="Поставка материалов",
            planned_date=date(2026, 9, 25), selected_cost_category_id=category.id,
            confidence=0.9, extraction_method="llm", target_kind="cash_flow", status="proposed",
        )
        db.add(invoice); db.flush()

        confirmed_invoice = confirm_invoice_extraction(
            invoice.id,
            InvoiceExtractionConfirm(
                contract_id=contract.id, schedule_item_id=stage.id, budget_line_id=budget.id,
            ),
            db, manager,
        )
        cash_flow = db.get(CashFlowEntry, confirmed_invoice["created_cash_flow_id"])
        update_status("cash-flow", cash_flow.id, StatusUpdate(status="approved"), db, manager)
        payment = confirm_payment(
            cash_flow.id,
            PaymentConfirmation(
                actual_amount="300", actual_date="2026-09-25", idempotency_key="canonical-payment-key",
            ),
            db, manager,
        )
        act_result = create_act(
            ActCreate(
                project_id=project.id, contract_id=contract.id, budget_line_id=budget.id,
                number="ACT-PG", title="Accepted canonical work", act_date="2026-09-26",
                amount="250", currency="RUB",
            ),
            db, manager,
        )
        update_status("acts", act_result["id"], StatusUpdate(status="approved"), db, manager)
        signed = update_status("acts", act_result["id"], StatusUpdate(status="signed"), db, manager)

        db.refresh(budget)
        assert (cash_flow.contract_id, cash_flow.schedule_item_id, cash_flow.budget_line_id) == (
            contract.id, stage.id, budget.id,
        )
        assert payment["status"] == "paid"
        assert db.scalar(select(func.count()).select_from(PaymentEvent).where(
            PaymentEvent.cash_flow_entry_id == cash_flow.id,
        )) == 1
        assert budget.committed_amount == Decimal("300.00")
        assert budget.actual_amount == Decimal("250.00")
        assert signed["budget_actual_amount"] == Decimal("250.00")


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
