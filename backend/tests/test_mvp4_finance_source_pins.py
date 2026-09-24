import hashlib
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

import app.api.contract_package as package_api
import app.api.execution_finance as finance_api
import app.api.organizations_contracts as contracts_api
from app.api.execution_finance import (
    ActCreate,
    InvoiceExtractionConfirm,
    InvoiceExtractionCreate,
    PaymentConfirmation,
    StatusUpdate,
    StructuredImportRequest,
    confirm_invoice_extraction,
    confirm_payment,
    create_act,
    create_invoice_extraction,
    overview,
    structured_import,
    update_status,
)
from app.invoice_extraction import InvoiceFields
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import AcceptanceAct, BudgetLine, CashFlowEntry
from app.models.organization_contract import Contract, Organization
from app.models.project import Project


def _world(db, user_factory, *, content="Оплата 10.10.2026: 1 000 руб."):
    user = user_factory(is_admin=True)
    organization = Organization(name="Finance source pin organization")
    db.add(organization)
    db.flush()
    project = Project(name="Finance source pin project", organization_id=organization.id)
    db.add(project)
    db.flush()
    document = Document(
        project_id=project.id,
        name="source.pdf",
        source="local_upload",
        status="analyzed",
        current_version=1,
        summary="Оплата 01.01.2027 9 999 руб.",
        notes="Оплата 02.02.2027 8 888 руб.",
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(document_id=document.id, version_number=1, content=content)
    db.add(version)
    db.flush()
    return user, project, document, version


def _digest(version):
    return hashlib.sha256((version.content or "").encode("utf-8")).hexdigest()


def _change_document(db, document):
    version = DocumentVersion(
        document_id=document.id,
        version_number=document.current_version + 1,
        content="changed source",
    )
    db.add(version)
    db.flush()
    document.current_version = version.version_number
    db.flush()
    return version


def test_invoice_budget_confirmation_preserves_source_pin(db_session, user_factory, monkeypatch):
    user, project, document, version = _world(db_session, user_factory, content="Материалы 125 400 руб.")
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: InvoiceFields(
        amount=Decimal("125400"), amount_evidence_quote="125 400 руб.", currency="RUB",
        counterparty="ООО Тест", counterparty_evidence_quote="ООО Тест",
        payment_purpose="Материалы", payment_purpose_evidence_quote="Материалы",
        suggested_category_name="Прямые", category_evidence_quote="Материалы",
        planned_date=date(2026, 9, 21), confidence=0.95, extraction_method="llm",
    ))
    proposed = create_invoice_extraction(
        document.id, InvoiceExtractionCreate(project_id=project.id, target_kind="budget"),
        db_session, user,
    )

    confirmed = confirm_invoice_extraction(
        proposed["id"], InvoiceExtractionConfirm(), db_session, user,
    )
    row = db_session.get(BudgetLine, confirmed["created_budget_line_id"])

    assert (row.source_document_id, row.source_document_version_id, row.source_document_sha256) == (
        document.id, version.id, _digest(version),
    )


def test_structured_budget_import_preserves_source_pin(db_session, user_factory, monkeypatch):
    user, project, document, version = _world(
        db_session, user_factory,
        content="Статья;Описание;Сумма\nПрямые;Кабель;1000\n",
    )
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    result = structured_import(
        document.id,
        StructuredImportRequest(
            project_id=project.id, kind="budget", source_rows=[2],
            row_overrides={2: {"title": "Кабель после проверки", "amount": "1250.50", "category": "Материалы"}},
        ),
        db_session,
        user,
    )
    row = db_session.get(BudgetLine, result["created_ids"][0])

    assert (row.source_document_id, row.source_document_version_id, row.source_document_sha256) == (
        document.id, version.id, _digest(version),
    )
    assert row.description == "Кабель после проверки"
    assert row.planned_amount == Decimal("1250.50")
    assert row.category == "Материалы"


def test_stale_budget_source_blocks_approval(db_session, user_factory, monkeypatch):
    user, project, document, version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    row = BudgetLine(
        project_id=project.id, category="Прямые", description="Кабель",
        planned_amount=100, forecast_amount=100,
        source_document_id=document.id, source_document_version_id=version.id,
        source_document_sha256=_digest(version),
    )
    db_session.add(row)
    db_session.flush()
    _change_document(db_session, document)

    with pytest.raises(HTTPException) as error:
        update_status("budget", row.id, StatusUpdate(status="approved"), db_session, user)

    assert error.value.status_code == 409
    assert "SOURCE_VERSION_MISMATCH" in error.value.detail


def test_direct_contract_analysis_uses_only_current_version_content_for_pin_and_payment(
    db_session, user_factory, monkeypatch,
):
    user, project, document, version = _world(db_session, user_factory)
    contract = Contract(
        project_id=project.id, number="C-1", title="Поставка",
        contract_kind="supply", source_document_id=document.id,
    )
    db_session.add(contract)
    db_session.flush()
    monkeypatch.setattr(contracts_api, "require_project_role", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(contracts_api, "create_tasks_from_files", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(contracts_api, "create_governance_items", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(contracts_api, "remember_contract_organizations", lambda *_args, **_kwargs: [])

    result = contracts_api.analyze_contract(project.id, contract.id, db_session, user)
    rows = db_session.query(CashFlowEntry).filter_by(contract_id=contract.id).all()

    assert result["created"]["payment_schedule"] == 1
    assert len(rows) == 1
    assert rows[0].planned_amount == Decimal("1000.00")
    assert rows[0].planned_date == date(2026, 10, 10)
    assert rows[0].source_document_version_id == version.id
    assert rows[0].source_document_sha256 == _digest(version)


def test_contract_package_analysis_preserves_current_version_pin(
    db_session, user_factory, monkeypatch,
):
    user, project, document, version = _world(db_session, user_factory)
    contract = Contract(
        project_id=project.id, number="C-2", title="Поставка",
        contract_kind="supply", source_document_id=document.id,
    )
    db_session.add(contract)
    db_session.flush()
    monkeypatch.setattr(package_api, "require_project_role", lambda *_args, **_kwargs: None)

    result = package_api.analyze_contract_package(project.id, contract.id, db_session, user)
    row = db_session.query(CashFlowEntry).filter_by(contract_id=contract.id).one()

    assert result["financial_entries"] == 1
    assert row.source_document_id == document.id
    assert row.source_document_version_id == version.id
    assert row.source_document_sha256 == _digest(version)


def test_stale_contract_payment_source_blocks_confirmation(db_session, user_factory, monkeypatch):
    user, project, document, version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    row = CashFlowEntry(
        project_id=project.id, direction="outflow", title="Платёж по договору",
        planned_date=date(2026, 10, 10), planned_amount=1000, actual_amount=0,
        source_document_id=document.id, source_document_version_id=version.id,
        source_document_sha256=_digest(version), status="proposed",
    )
    db_session.add(row)
    db_session.flush()
    _change_document(db_session, document)

    with pytest.raises(HTTPException) as error:
        confirm_payment(row.id, PaymentConfirmation(actual_amount=1000), db_session, user)

    assert error.value.status_code == 409
    assert "SOURCE_VERSION_MISMATCH" in error.value.detail


def test_acceptance_act_creation_and_overview_preserve_source_pin(
    db_session, user_factory, monkeypatch,
):
    user, project, document, version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    created = create_act(ActCreate(
        project_id=project.id, document_id=document.id, number="A-1",
        title="Акт выполненных работ", amount=1000,
    ), db_session, user)
    row = db_session.get(AcceptanceAct, created["id"])
    result = overview(project.id, db_session, user)
    payload = next(item for item in result["acts"] if item["id"] == row.id)

    assert row.source_document_version_id == version.id
    assert row.source_document_sha256 == _digest(version)
    assert payload["source_document_version_id"] == version.id
    assert payload["source_document_sha256"] == _digest(version)


def test_acceptance_act_rejects_document_from_another_project(
    db_session, user_factory, monkeypatch,
):
    user, project, _document, _version = _world(db_session, user_factory)
    _other_user, other_project, other_document, _other_version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        create_act(ActCreate(
            project_id=project.id, document_id=other_document.id, number="A-2",
            title="Чужой акт", amount=100,
        ), db_session, user)

    assert other_project.id != project.id
    assert error.value.status_code == 422


def test_stale_acceptance_act_source_blocks_status_change(
    db_session, user_factory, monkeypatch,
):
    user, project, document, version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    row = AcceptanceAct(
        project_id=project.id, document_id=document.id, number="A-3", title="Акт",
        amount=100, source_document_version_id=version.id,
        source_document_sha256=_digest(version),
    )
    db_session.add(row)
    db_session.flush()
    _change_document(db_session, document)

    with pytest.raises(HTTPException) as error:
        update_status("acts", row.id, StatusUpdate(status="approved"), db_session, user)

    assert error.value.status_code == 409
    assert "SOURCE_VERSION_MISMATCH" in error.value.detail


def test_manual_finance_rows_without_document_remain_valid(
    db_session, user_factory, monkeypatch,
):
    user, project, _document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    budget = BudgetLine(
        project_id=project.id, category="Ручная", description="Ручной бюджет",
        planned_amount=100, forecast_amount=100,
    )
    act = AcceptanceAct(
        project_id=project.id, number="MANUAL", title="Ручной акт", amount=100,
    )
    db_session.add_all([budget, act])
    db_session.flush()

    assert update_status("budget", budget.id, StatusUpdate(status="approved"), db_session, user)["status"] == "approved"
    assert update_status("acts", act.id, StatusUpdate(status="approved"), db_session, user)["status"] == "approved"


def test_legacy_document_link_without_pin_fails_closed_with_explicit_error(
    db_session, user_factory, monkeypatch,
):
    user, project, document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    payment = CashFlowEntry(
        project_id=project.id, direction="outflow", title="Legacy payment",
        planned_date=date(2026, 10, 10), planned_amount=100, actual_amount=0,
        source_document_id=document.id,
    )
    act = AcceptanceAct(
        project_id=project.id, document_id=document.id, number="LEGACY", title="Legacy act", amount=100,
    )
    db_session.add_all([payment, act])
    db_session.flush()

    with pytest.raises(HTTPException) as payment_error:
        confirm_payment(payment.id, PaymentConfirmation(actual_amount=100), db_session, user)
    with pytest.raises(HTTPException) as act_error:
        update_status("acts", act.id, StatusUpdate(status="approved"), db_session, user)

    assert payment_error.value.status_code == act_error.value.status_code == 409
    assert "SOURCE_PIN_MISSING" in payment_error.value.detail
    assert "SOURCE_PIN_MISSING" in act_error.value.detail
