import hashlib
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api.execution_finance import (
    CashFlowControlLinks,
    InvoiceExtractionConfirm,
    InvoiceProposalCreate,
    StatusUpdate,
    confirm_invoice_extraction,
    create_invoice_proposal,
    link_cash_flow_controls,
    update_status,
)
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import (
    BudgetLine,
    CashFlowEntry,
    CostCategory,
    InvoiceExtractionProposal,
    ScheduleBaseline,
    ScheduleItem,
)
from app.models.organization_contract import Contract, Organization
from app.models.project import Project
from app.models.project_member import ProjectMember


def _world(db, user_factory):
    user = user_factory(is_admin=True)
    organization = Organization(name="Canonical finance organization")
    db.add(organization); db.flush()
    project = Project(name="Canonical finance project", organization_id=organization.id)
    db.add(project); db.flush()
    contract = Contract(
        project_id=project.id, number="CHAIN-1", title="Canonical contract",
        contract_kind="supply", status="active",
    )
    db.add(contract); db.flush()
    baseline = ScheduleBaseline(
        project_id=project.id, contract_id=contract.id, created_by_user_id=user.id,
        name="Canonical GPR", version=1, status="approved",
    )
    db.add(baseline); db.flush()
    stage = ScheduleItem(
        project_id=project.id, baseline_id=baseline.id, title="Canonical stage",
        planned_start=date(2026, 9, 1), planned_finish=date(2026, 9, 30),
    )
    category = CostCategory(
        organization_id=organization.id, name="Прямые", normalized_name="прямые", is_active=True,
    )
    budget = BudgetLine(
        project_id=project.id, contract_id=contract.id, cost_category_id=None,
        category="Прямые", description="Canonical budget", planned_amount=Decimal("1000"),
        committed_amount=Decimal("0"), actual_amount=Decimal("0"),
        forecast_amount=Decimal("1000"), currency="RUB", status="approved",
    )
    document = Document(
        project_id=project.id, name="invoice.pdf", source="local_upload",
        status="analyzed", current_version=1,
    )
    db.add_all([stage, category, budget, document]); db.flush()
    version = DocumentVersion(
        document_id=document.id, version_number=1,
        content="Поставка материалов. Итого 300 руб. Оплатить 25.09.2026.",
    )
    db.add(version); db.flush()
    budget.cost_category_id = category.id
    return user, project, contract, stage, budget, category, document, version


def _invoice_payload(project, contract, stage, budget, **changes):
    data = {
        "project_id": project.id, "contract_id": contract.id,
        "schedule_item_id": stage.id, "budget_line_id": budget.id,
        "direction": "outflow", "title": "Invoice payment",
        "planned_date": date(2026, 9, 25), "planned_amount": Decimal("300"),
        "currency": "RUB",
    }
    data.update(changes)
    return InvoiceProposalCreate(**data)


def test_regular_invoice_uses_canonical_contract_schedule_budget_chain(db_session, user_factory):
    user, project, contract, stage, budget, _category, _document, _version = _world(db_session, user_factory)

    result = create_invoice_proposal(
        _invoice_payload(project, contract, stage, budget), db_session, user,
    )
    row = db_session.get(CashFlowEntry, result["id"])

    assert (row.contract_id, row.schedule_item_id, row.budget_line_id) == (
        contract.id, stage.id, budget.id,
    )


def test_canonical_chain_rejects_schedule_and_budget_from_another_contract(db_session, user_factory):
    user, project, contract, stage, budget, _category, _document, _version = _world(db_session, user_factory)
    other = Contract(project_id=project.id, number="CHAIN-2", title="Other", status="active")
    db_session.add(other); db_session.flush()

    with pytest.raises(HTTPException, match="Этап ГПР не связан"):
        create_invoice_proposal(
            _invoice_payload(project, other, stage, budget), db_session, user,
        )


def test_ai_invoice_requires_full_chain_and_stores_it(db_session, user_factory):
    user, project, contract, stage, budget, category, document, version = _world(db_session, user_factory)
    proposal = InvoiceExtractionProposal(
        project_id=project.id, source_document_id=document.id,
        source_document_version_id=version.id,
        source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest(),
        amount=Decimal("300"), currency="RUB", payment_purpose="Поставка материалов",
        planned_date=date(2026, 9, 25), selected_cost_category_id=category.id,
        confidence=0.9, extraction_method="llm", target_kind="cash_flow", status="proposed",
    )
    db_session.add(proposal); db_session.flush()

    with pytest.raises(HTTPException, match="обязательны договор, этап ГПР и строка бюджета"):
        confirm_invoice_extraction(
            proposal.id, InvoiceExtractionConfirm(contract_id=contract.id), db_session, user,
        )

    result = confirm_invoice_extraction(
        proposal.id,
        InvoiceExtractionConfirm(
            contract_id=contract.id, schedule_item_id=stage.id, budget_line_id=budget.id,
        ),
        db_session, user,
    )
    row = db_session.get(CashFlowEntry, result["created_cash_flow_id"])
    assert (row.contract_id, row.schedule_item_id, row.budget_line_id) == (
        contract.id, stage.id, budget.id,
    )


def test_existing_document_cash_flow_is_linked_by_explicit_manager_action(db_session, user_factory):
    user, project, contract, stage, budget, _category, document, version = _world(db_session, user_factory)
    row = CashFlowEntry(
        project_id=project.id, contract_id=contract.id,
        source_document_id=document.id, source_document_version_id=version.id,
        source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest(),
        direction="outflow", title="Imported payable", planned_date=date(2026, 9, 25),
        planned_amount=Decimal("300"), actual_amount=Decimal("0"),
        currency="RUB", status="proposed",
    )
    db_session.add(row); db_session.flush()

    result = link_cash_flow_controls(
        row.id,
        CashFlowControlLinks(
            contract_id=contract.id, schedule_item_id=stage.id, budget_line_id=budget.id,
        ),
        db_session, user,
    )

    assert (result["contract_id"], result["schedule_item_id"], result["budget_line_id"]) == (
        contract.id, stage.id, budget.id,
    )
    audit = db_session.query(AuditLog).filter_by(
        action="cash_flow_controls_linked", entity_id=row.id,
    ).one()
    assert "human_confirmation=true" in audit.details


def test_editor_cannot_link_existing_document_cash_flow_controls(db_session, user_factory):
    _manager, project, contract, stage, budget, _category, document, version = _world(db_session, user_factory)
    editor = user_factory(is_admin=False)
    db_session.add(ProjectMember(project_id=project.id, user_id=editor.id, role="editor"))
    row = CashFlowEntry(
        project_id=project.id, contract_id=contract.id,
        source_document_id=document.id, source_document_version_id=version.id,
        source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest(),
        direction="outflow", title="Editor cannot link", planned_date=date(2026, 9, 25),
        planned_amount=Decimal("300"), actual_amount=Decimal("0"),
        currency="RUB", status="proposed",
    )
    db_session.add(row); db_session.flush()

    with pytest.raises(HTTPException) as denied:
        link_cash_flow_controls(
            row.id,
            CashFlowControlLinks(
                contract_id=contract.id, schedule_item_id=stage.id, budget_line_id=budget.id,
            ),
            db_session, editor,
        )

    assert denied.value.status_code == 403


def test_document_cash_flow_cannot_be_approved_before_controls_are_linked(db_session, user_factory):
    user, project, contract, _stage, _budget, _category, document, version = _world(db_session, user_factory)
    row = CashFlowEntry(
        project_id=project.id, contract_id=contract.id,
        source_document_id=document.id, source_document_version_id=version.id,
        source_document_sha256=hashlib.sha256(version.content.encode()).hexdigest(),
        direction="outflow", title="Unlinked payable", planned_date=date(2026, 9, 25),
        planned_amount=Decimal("300"), actual_amount=Decimal("0"),
        currency="RUB", status="proposed",
    )
    db_session.add(row); db_session.flush()

    with pytest.raises(HTTPException, match="обязательны договор, этап ГПР и строка бюджета"):
        update_status("cash-flow", row.id, StatusUpdate(status="approved"), db_session, user)
    assert row.status == "proposed"


@pytest.mark.parametrize("initial_status", ["proposed", "approved"])
def test_cancel_cash_flow_preserves_history_and_releases_budget(db_session, user_factory, initial_status):
    user, project, contract, _stage, budget, _category, document, version = _world(db_session, user_factory)
    row = CashFlowEntry(
        project_id=project.id, contract_id=contract.id, budget_line_id=budget.id,
        source_document_id=document.id, source_document_version_id=version.id,
        direction="outflow", title="Cancel test invoice", planned_date=date(2026, 9, 25),
        planned_amount=Decimal("300"), actual_amount=Decimal("0"),
        currency="RUB", status=initial_status,
    )
    budget.committed_amount = Decimal("300")
    db_session.add(row); db_session.flush()
    row_id = row.id

    result = update_status("cash-flow", row_id, StatusUpdate(status="cancelled"), db_session, user)

    assert result["status"] == "cancelled"
    db_session.expire_all()
    saved = db_session.get(CashFlowEntry, row_id)
    assert saved.status == "cancelled"
    assert saved.source_document_id == document.id
    assert budget.committed_amount == Decimal("0")
    assert db_session.query(AuditLog).filter(AuditLog.entity_id == row_id).count() >= 1


@pytest.mark.parametrize("initial_status", ["paid", "received"])
def test_cancel_cash_flow_refuses_settled_payment(db_session, user_factory, initial_status):
    user, project, contract, _stage, _budget, _category, _document, _version = _world(db_session, user_factory)
    row = CashFlowEntry(
        project_id=project.id, contract_id=contract.id,
        direction="outflow" if initial_status == "paid" else "inflow",
        title="Settled payment", planned_date=date(2026, 9, 25),
        planned_amount=Decimal("300"), actual_amount=Decimal("300"),
        actual_date=date(2026, 9, 25), currency="RUB", status=initial_status,
    )
    db_session.add(row); db_session.flush()
    with pytest.raises(HTTPException) as denied:
        update_status("cash-flow", row.id, StatusUpdate(status="cancelled"), db_session, user)
    assert denied.value.status_code == 409
    assert row.status == initial_status


def test_control_links_cannot_be_changed_after_approval(db_session, user_factory):
    user, project, contract, stage, budget, _category, _document, _version = _world(db_session, user_factory)
    row = CashFlowEntry(
        project_id=project.id, contract_id=contract.id,
        schedule_item_id=stage.id, budget_line_id=budget.id,
        direction="outflow", title="Approved payable", planned_date=date(2026, 9, 25),
        planned_amount=Decimal("300"), actual_amount=Decimal("0"),
        currency="RUB", status="approved",
    )
    db_session.add(row); db_session.flush()

    with pytest.raises(HTTPException, match="только у предложения"):
        link_cash_flow_controls(
            row.id,
            CashFlowControlLinks(
                contract_id=contract.id, schedule_item_id=stage.id, budget_line_id=budget.id,
            ),
            db_session, user,
        )
