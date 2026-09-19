from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

import app.api.execution_finance as finance_api
import app.invoice_extraction as invoice_extraction
from app.api.execution_finance import (
    CostCategoryCreate,
    CostCategoryUpdate,
    InvoiceExtractionConfirm,
    InvoiceExtractionCreate,
    InvoiceExtractionUpdate,
    confirm_invoice_extraction,
    create_cost_category,
    create_invoice_extraction,
    update_cost_category,
    update_invoice_extraction,
)
from app.invoice_extraction import InvoiceFields, extract_invoice_fields
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, CostCategory, InvoiceExtractionProposal
from app.models.organization_contract import Organization
from app.models.project import Project


def _world(db_session, user_factory, content: str = "Счёт за материалы. Итого 125 400,50 руб."):
    organization = Organization(name="Invoice test organization")
    user = user_factory(is_admin=True)
    db_session.add(organization)
    db_session.flush()
    project = Project(name="Invoice test", organization_id=organization.id)
    db_session.add(project)
    db_session.flush()
    document = Document(
        project_id=project.id, name="invoice.pdf", source="local_upload",
        status="analyzed", current_version=1,
    )
    db_session.add(document)
    db_session.flush()
    version = DocumentVersion(document_id=document.id, version_number=1, content=content)
    db_session.add(version)
    db_session.flush()
    return user, project, document, version


def test_invoice_llm_fields_require_verbatim_evidence_and_reject_materials_as_salary(monkeypatch):
    text = "Поставщик ООО Бетон. Назначение: строительные материалы. Итого 125400 руб."
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda *_: "external_allowed")
    monkeypatch.setattr(invoice_extraction, "gemini_configured", lambda: True)
    monkeypatch.setattr(invoice_extraction, "extract_invoice_fields_with_gemini", lambda *_: {
        "amount": 125400, "amount_evidence_quote": "Итого 125400 руб.", "currency": "RUB",
        "counterparty": "ООО Бетон", "counterparty_evidence_quote": "ООО Бетон",
        "payment_purpose": "строительные материалы",
        "payment_purpose_evidence_quote": "строительные материалы",
        "suggested_category_name": "Зарплата",
        "category_evidence_quote": "строительные материалы", "confidence": "high",
    })

    result = extract_invoice_fields(object(), 1, text, "invoice.pdf", ["Прямые", "Зарплата"])

    assert result.amount == Decimal("125400.00")
    assert result.counterparty == "ООО Бетон"
    assert result.payment_purpose == "строительные материалы"
    assert result.suggested_category_name is None
    assert result.category_evidence_quote is None


def test_fabricated_invoice_field_is_dropped_without_losing_other_fields(monkeypatch):
    text = "Назначение: аренда крана. Итого 80 000 руб."
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda *_: "external_allowed")
    monkeypatch.setattr(invoice_extraction, "gemini_configured", lambda: True)
    monkeypatch.setattr(invoice_extraction, "extract_invoice_fields_with_gemini", lambda *_: {
        "amount": 80000, "amount_evidence_quote": "Итого 80 000 руб.", "currency": "RUB",
        "counterparty": "Вымышленный поставщик", "counterparty_evidence_quote": "нет такой цитаты",
        "payment_purpose": "аренда крана", "payment_purpose_evidence_quote": "аренда крана",
        "suggested_category_name": "Аренда", "category_evidence_quote": "аренда крана",
        "confidence": "high",
    })

    result = extract_invoice_fields(object(), 1, text, "invoice.pdf", ["Аренда"])

    assert result.counterparty is None and result.counterparty_evidence_quote is None
    assert result.suggested_category_name == "Аренда"


def test_invoice_regex_fallback_never_assigns_category(monkeypatch):
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda *_: "external_allowed")
    monkeypatch.setattr(invoice_extraction, "gemini_configured", lambda: False)

    result = extract_invoice_fields(
        object(), 1, "Счёт от 19.09.2026. Материалы 45 000 руб.", "invoice.pdf",
        ["Прямые", "Зарплата"],
    )

    assert result.amount == Decimal("45000.00")
    assert result.suggested_category_name is None
    assert result.extraction_method == "regex"
    assert result.fallback_reason == "not_configured"


def test_invoice_llm_prefers_explicit_payment_deadline_over_invoice_date(monkeypatch):
    text = (
        "СЧЕТ № PU-LIVE-20260919-01\n"
        "Дата счета: 19.09.2026\n"
        "Срок оплаты: 30.09.2026\n"
        "Итого к оплате: 12 345,67 руб."
    )
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda *_: "external_allowed")
    monkeypatch.setattr(invoice_extraction, "gemini_configured", lambda: True)
    monkeypatch.setattr(invoice_extraction, "extract_invoice_fields_with_gemini", lambda *_: {
        "amount": 12345.67, "amount_evidence_quote": "12 345,67 руб.", "currency": "RUB",
        "counterparty": None, "counterparty_evidence_quote": None,
        "payment_purpose": None, "payment_purpose_evidence_quote": None,
        "suggested_category_name": None, "category_evidence_quote": None,
        "confidence": "medium",
    })

    result = extract_invoice_fields(object(), 1, text, "invoice.pdf", [])

    assert result.planned_date == date(2026, 9, 30)


def test_invoice_regex_fallback_prefers_explicit_payment_deadline(monkeypatch):
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda *_: "external_allowed")
    monkeypatch.setattr(invoice_extraction, "gemini_configured", lambda: False)

    result = extract_invoice_fields(
        object(), 1,
        "Дата счета: 19.09.2026. Срок оплаты: 30.09.2026. Итого 12 345,67 руб.",
        "invoice.pdf", [],
    )

    assert result.planned_date == date(2026, 9, 30)


def test_invoice_without_explicit_payment_deadline_keeps_first_valid_date(monkeypatch):
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda *_: "external_allowed")
    monkeypatch.setattr(invoice_extraction, "gemini_configured", lambda: False)

    result = extract_invoice_fields(
        object(), 1, "Дата счета: 19.09.2026. Итого 12 345,67 руб.", "invoice.pdf", [],
    )

    assert result.planned_date == date(2026, 9, 19)


@pytest.mark.parametrize("deadline", [
    "Оплатить до 30.09.2026",
    "Дата платежа: 30.09.2026",
    "К оплате не позднее 30.09.2026",
])
def test_invoice_recognizes_supported_payment_deadline_labels(monkeypatch, deadline):
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda *_: "external_allowed")
    monkeypatch.setattr(invoice_extraction, "gemini_configured", lambda: False)

    result = extract_invoice_fields(
        object(), 1, f"Дата счета: 19.09.2026. {deadline}. Итого 12 345,67 руб.",
        "invoice.pdf", [],
    )

    assert result.planned_date == date(2026, 9, 30)


def test_invoice_does_not_treat_unrelated_deadline_as_payment_deadline(monkeypatch):
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda *_: "external_allowed")
    monkeypatch.setattr(invoice_extraction, "gemini_configured", lambda: False)

    result = extract_invoice_fields(
        object(), 1,
        "Дата счета: 19.09.2026. Срок поставки: 30.09.2026. Итого 12 345,67 руб.",
        "invoice.pdf", [],
    )

    assert result.planned_date == date(2026, 9, 19)


def test_proposal_requires_manager_confirmation_before_creating_cash_flow(
    db_session, user_factory, monkeypatch,
):
    user, project, document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="125 400,50 руб.", currency="RUB",
        counterparty="ООО Бетон", counterparty_evidence_quote="ООО Бетон",
        payment_purpose="строительные материалы", payment_purpose_evidence_quote="материалы",
        suggested_category_name="Прямые", category_evidence_quote="материалы",
        planned_date=date(2026, 9, 19), confidence=0.92, extraction_method="llm",
    ))

    proposed = create_invoice_extraction(
        document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user,
    )

    assert proposed["status"] == "proposed"
    assert db_session.query(CashFlowEntry).count() == 0
    assert db_session.query(BudgetLine).count() == 0
    category = db_session.get(CostCategory, proposed["selected_cost_category_id"])
    assert category.name == "Прямые"

    reviewed = update_invoice_extraction(
        proposed["id"], InvoiceExtractionUpdate(
            selected_cost_category_id=category.id,
            amount="125400.50", counterparty="ООО Бетон",
            payment_purpose="Оплата строительных материалов",
            planned_date="2026-09-20", target_kind="cash_flow",
        ), db_session, user,
    )
    assert reviewed["status"] == "proposed"
    assert db_session.query(CashFlowEntry).count() == 0

    confirmed = confirm_invoice_extraction(
        proposed["id"], InvoiceExtractionConfirm(), db_session, user,
    )
    entry = db_session.get(CashFlowEntry, confirmed["created_cash_flow_id"])
    assert confirmed["status"] == "confirmed"
    assert entry.project_id == project.id
    assert entry.cost_category_id == category.id
    assert entry.category == "Прямые"  # transition-period dual write
    assert entry.status == "proposed"


def test_confirmation_fails_closed_without_human_category(db_session, user_factory, monkeypatch):
    user, project, document, version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    proposal = InvoiceExtractionProposal(
        project_id=project.id, source_document_id=document.id,
        source_document_version_id=version.id,
        source_document_sha256=finance_api.hashlib.sha256(version.content.encode()).hexdigest(),
        amount=Decimal("100.00"), payment_purpose="Материалы", planned_date=date(2026, 9, 20),
        currency="RUB", confidence=0.5, extraction_method="regex", status="proposed",
    )
    db_session.add(proposal)
    db_session.flush()

    with pytest.raises(HTTPException) as error:
        confirm_invoice_extraction(proposal.id, InvoiceExtractionConfirm(), db_session, user)

    assert error.value.status_code == 422
    assert db_session.query(CashFlowEntry).count() == 0


def test_cost_category_catalog_is_editable_and_scoped_to_project_organization(
    db_session, user_factory, monkeypatch,
):
    user, project, _document, _version = _world(db_session, user_factory)
    other_organization = Organization(name="Other invoice organization")
    db_session.add(other_organization)
    db_session.flush()
    other_project = Project(name="Other invoice project", organization_id=other_organization.id)
    db_session.add(other_project)
    db_session.flush()
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)

    created = create_cost_category(
        CostCategoryCreate(project_id=project.id, name="Страхование"), db_session, user,
    )
    updated = update_cost_category(
        created["id"], CostCategoryUpdate(project_id=project.id, name="Страховые расходы"),
        db_session, user,
    )

    assert updated["name"] == "Страховые расходы"
    with pytest.raises(HTTPException) as error:
        update_cost_category(
            created["id"], CostCategoryUpdate(project_id=other_project.id, is_active=False),
            db_session, user,
        )
    assert error.value.status_code == 404


def test_repeated_counterparty_does_not_auto_apply_prior_category(
    db_session, user_factory, monkeypatch,
):
    user, project, first_document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    suggested = InvoiceFields(
        amount=Decimal("100.00"), amount_evidence_quote="100 руб.", currency="RUB",
        counterparty="ООО Повтор", counterparty_evidence_quote="ООО Повтор",
        payment_purpose="материалы", payment_purpose_evidence_quote="материалы",
        suggested_category_name="Прямые", category_evidence_quote="материалы",
        planned_date=date(2026, 9, 20), confidence=0.9, extraction_method="llm",
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: suggested)
    first = create_invoice_extraction(
        first_document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user,
    )
    confirm_invoice_extraction(first["id"], InvoiceExtractionConfirm(), db_session, user)

    second_document = Document(
        project_id=project.id, name="invoice-repeat.pdf", source="local_upload",
        status="analyzed", current_version=1,
    )
    db_session.add(second_document)
    db_session.flush()
    db_session.add(DocumentVersion(
        document_id=second_document.id, version_number=1,
        content="ООО Повтор. Назначение требует ручной классификации. Итого 200 руб.",
    ))
    db_session.flush()
    no_category = InvoiceFields(
        amount=Decimal("200.00"), amount_evidence_quote="200 руб.", currency="RUB",
        counterparty="ООО Повтор", counterparty_evidence_quote="ООО Повтор",
        payment_purpose="требует ручной классификации",
        payment_purpose_evidence_quote="требует ручной классификации",
        suggested_category_name=None, category_evidence_quote=None,
        planned_date=date(2026, 9, 21), confidence=0.5, extraction_method="llm",
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: no_category)

    second = create_invoice_extraction(
        second_document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user,
    )

    assert second["selected_cost_category_id"] is None
    assert second["status"] == "proposed"
    assert db_session.query(CashFlowEntry).filter_by(project_id=project.id).count() == 1
