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
    retry_invoice_extraction_ai,
    update_cost_category,
    update_invoice_extraction,
)
from app.invoice_extraction import InvoiceFields, extract_invoice_fields
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.execution_finance import BudgetLine, CashFlowEntry, CostCategory, InvoiceExtractionProposal, ScheduleBaseline, ScheduleItem
from app.models.organization_contract import Contract, Organization
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


def _control_chain(db, user, project):
    contract = Contract(project_id=project.id, number="INV-1", title="Invoice contract", status="active")
    db.add(contract); db.flush()
    baseline = ScheduleBaseline(
        project_id=project.id, contract_id=contract.id, created_by_user_id=user.id,
        name="Invoice GPR", version=1, status="approved",
    )
    db.add(baseline); db.flush()
    stage = ScheduleItem(project_id=project.id, baseline_id=baseline.id, title="Invoice stage")
    budget = BudgetLine(
        project_id=project.id, contract_id=contract.id, category="Прямые",
        description="Invoice budget", planned_amount=Decimal("500000"),
        forecast_amount=Decimal("500000"), currency="RUB", status="approved",
    )
    db.add_all([stage, budget]); db.flush()
    return InvoiceExtractionConfirm(
        contract_id=contract.id, schedule_item_id=stage.id, budget_line_id=budget.id,
    )


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


def test_temporary_invoice_fallback_can_retry_same_version_with_llm(
    db_session, user_factory, monkeypatch,
):
    user, project, document, version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    fallback = InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="125 400,50 руб.",
        currency="RUB", counterparty=None, counterparty_evidence_quote=None,
        payment_purpose=None, payment_purpose_evidence_quote=None,
        suggested_category_name=None, category_evidence_quote=None,
        planned_date=date(2026, 9, 20), confidence=0.35, extraction_method="regex",
        fallback_reason="temporarily_unavailable",
    )
    llm = InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="125 400,50 руб.",
        currency="RUB", counterparty="ООО Бетон", counterparty_evidence_quote="ООО Бетон",
        payment_purpose="строительные материалы",
        payment_purpose_evidence_quote="строительные материалы",
        suggested_category_name="Прямые", category_evidence_quote="материалы",
        planned_date=date(2026, 9, 20), confidence=0.92, extraction_method="llm",
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: fallback)
    proposed = create_invoice_extraction(
        document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user,
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: llm)

    retried = retry_invoice_extraction_ai(proposed["id"], db_session, user)

    assert retried["id"] == proposed["id"]
    assert retried["source_document_version_id"] == version.id
    assert retried["source_document_sha256"] == proposed["source_document_sha256"]
    assert retried["extraction_method"] == "llm"
    assert retried["fallback_reason"] is None
    assert retried["counterparty"] == "ООО Бетон"
    assert retried["payment_purpose"] == "строительные материалы"
    assert db_session.query(InvoiceExtractionProposal).count() == 1
    assert db_session.query(CashFlowEntry).count() == 0
    audit = db_session.query(AuditLog).filter_by(action="invoice_extraction_ai_retried").one()
    assert "outcome=success" in audit.details


def test_failed_invoice_ai_retry_preserves_existing_fallback_fields(
    db_session, user_factory, monkeypatch,
):
    user, project, document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    original = InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="125 400,50 руб.",
        currency="RUB", counterparty=None, counterparty_evidence_quote=None,
        payment_purpose=None, payment_purpose_evidence_quote=None,
        suggested_category_name=None, category_evidence_quote=None,
        planned_date=date(2026, 9, 20), confidence=0.35, extraction_method="regex",
        fallback_reason="temporarily_unavailable",
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: original)
    proposed = create_invoice_extraction(
        document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user,
    )
    second_fallback = InvoiceFields(
        amount=Decimal("999.00"), amount_evidence_quote="999 руб.", currency="RUB",
        counterparty=None, counterparty_evidence_quote=None, payment_purpose=None,
        payment_purpose_evidence_quote=None, suggested_category_name=None,
        category_evidence_quote=None, planned_date=None, confidence=0.1,
        extraction_method="regex", fallback_reason="temporarily_unavailable",
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: second_fallback)

    retried = retry_invoice_extraction_ai(proposed["id"], db_session, user)

    assert retried["extraction_method"] == "regex"
    assert retried["fallback_reason"] == "temporarily_unavailable"
    assert retried["amount"] == Decimal("125400.50")
    assert retried["amount_evidence_quote"] == "125 400,50 руб."


def test_invoice_ai_retry_rejects_changed_source_version_before_provider_call(
    db_session, user_factory, monkeypatch,
):
    user, project, document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    fallback = InvoiceFields(
        amount=Decimal("125400.50"), amount_evidence_quote="125 400,50 руб.",
        currency="RUB", counterparty=None, counterparty_evidence_quote=None,
        payment_purpose=None, payment_purpose_evidence_quote=None,
        suggested_category_name=None, category_evidence_quote=None,
        planned_date=None, confidence=0.35, extraction_method="regex",
        fallback_reason="temporarily_unavailable",
    )
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: fallback)
    proposed = create_invoice_extraction(
        document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user,
    )
    db_session.add(DocumentVersion(document_id=document.id, version_number=2, content="changed invoice"))
    document.current_version = 2
    db_session.commit()
    monkeypatch.setattr(
        finance_api, "extract_invoice_fields",
        lambda *_args, **_kwargs: pytest.fail("provider must not be called for a changed source"),
    )

    with pytest.raises(HTTPException) as error:
        retry_invoice_extraction_ai(proposed["id"], db_session, user)

    assert error.value.status_code == 409
    assert "SOURCE_VERSION_MISMATCH" in error.value.detail


def test_invoice_ai_retry_is_not_offered_for_permanent_fallback(
    db_session, user_factory, monkeypatch,
):
    user, project, document, version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    proposal = InvoiceExtractionProposal(
        project_id=project.id, source_document_id=document.id,
        source_document_version_id=version.id,
        source_document_sha256=finance_api.hashlib.sha256(version.content.encode()).hexdigest(),
        amount=Decimal("125400.50"), currency="RUB", confidence=0.35,
        extraction_method="regex", fallback_reason="policy_blocked",
        target_kind="cash_flow", status="proposed",
    )
    db_session.add(proposal)
    db_session.commit()

    with pytest.raises(HTTPException) as error:
        retry_invoice_extraction_ai(proposal.id, db_session, user)

    assert error.value.status_code == 409
    assert "временной недоступности" in error.value.detail


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

    controls = _control_chain(db_session, user, project)
    confirmed = confirm_invoice_extraction(proposed["id"], controls, db_session, user)
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
    confirm_invoice_extraction(first["id"], _control_chain(db_session, user, project), db_session, user)

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


def test_get_invoice_extraction_returns_proposal_for_push_card(db_session, user_factory, monkeypatch):
    user, project, document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: InvoiceFields(
        amount=Decimal("120788.73"), amount_evidence_quote="120 788,73", currency="RUB",
        counterparty="ООО ЭЛСИСТЕМ", counterparty_evidence_quote="ООО ЭЛСИСТЕМ",
        payment_purpose="комплектующие", payment_purpose_evidence_quote="комплектующие",
        suggested_category_name="Прямые", category_evidence_quote="комплектующие",
        planned_date=date(2026, 9, 19), confidence=0.9, extraction_method="llm",
    ))
    proposed = create_invoice_extraction(document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user)

    loaded = finance_api.get_invoice_extraction(proposed["id"], db_session, user)

    assert loaded["id"] == proposed["id"]
    assert loaded["counterparty"] == "ООО ЭЛСИСТЕМ"
    assert loaded["status"] == "proposed"
    with pytest.raises(finance_api.HTTPException):
        finance_api.get_invoice_extraction(999999, db_session, user)


def test_additional_cost_invoice_gets_a_contract_without_asking(db_session, user_factory, monkeypatch):
    user, project, document, _version = _world(db_session, user_factory)
    assert finance_api._infer_invoice_contract_id(db_session, project.id, None) is None
    _control_chain(db_session, user, project)
    other = Contract(project_id=project.id, number="SUB-2", title="Sub", counterparty="ООО «Бетон»", status="active")
    db_session.add(other); db_session.flush()
    only_contract = db_session.query(Contract).filter_by(number="INV-1").one()
    # same counterparty wins, quotes and legal form ignored
    assert finance_api._infer_invoice_contract_id(db_session, project.id, 'ООО "Бетон"') == other.id
    # no counterparty match and no plan rows: two contracts -> cannot guess
    assert finance_api._infer_invoice_contract_id(db_session, project.id, "ИП Иванов") is None
    db_session.delete(other); db_session.flush()
    assert finance_api._infer_invoice_contract_id(db_session, project.id, "ИП Иванов") == only_contract.id


def test_own_company_as_supplier_is_an_outgoing_invoice_not_an_expense(db_session, user_factory, monkeypatch):
    user, project, document, _version = _world(db_session, user_factory)
    org = db_session.get(finance_api.Organization, project.organization_id)
    org.inn = "7716888076"
    db_session.flush()
    item = InvoiceExtractionProposal(project_id=project.id, counterparty='ООО "ДИСИАЙ СОЛЮШНС"')
    text = ('Счет на оплату № 1324477402 от 02 сентября 2026 г. Поставщик: ООО "ДИСИАЙ СОЛЮШНС", ИНН 7716888076, '
            'КПП 771601001 Покупатель: ООО "БУЛАТ", ИНН 7700000000')
    finance_api._mark_own_outgoing_invoice(db_session, item, text)
    assert item.fallback_reason == finance_api.OWN_OUTGOING_INVOICE
    assert item.counterparty == 'ООО "БУЛАТ"'

    other = InvoiceExtractionProposal(project_id=project.id, counterparty="ООО Бетон")
    finance_api._mark_own_outgoing_invoice(db_session, other, "Поставщик: ООО Бетон, ИНН 5000000000")
    assert other.fallback_reason is None


def test_document_to_our_customer_becomes_an_expected_receipt(db_session, user_factory, monkeypatch):
    """Owner 21.10.2026: our act / invoice to the customer is money in, booked on the plan
    DDS receipt line; «Получено» settles it and the plan amount never changes."""
    from app.api.budget_from_dds import RecordFactRequest, record_plan_fact
    from app.money_calendar import money_calendar

    user, project, document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    customer = Contract(project_id=project.id, number="C-1", title="Заказчик", counterparty="ООО «БУЛАТ»",
                        status="active", contract_kind="customer")
    db_session.add(customer); db_session.flush()
    early = CashFlowEntry(project_id=project.id, contract_id=customer.id, direction="inflow", title="Этапы Городец",
                          planned_date=date(2099, 9, 30), planned_amount=Decimal("100.00"), actual_amount=Decimal("0"),
                          currency="RUB", status="proposed", source_name="ДДС!K4")
    late = CashFlowEntry(project_id=project.id, contract_id=customer.id, direction="inflow", title="Этапы Городец",
                         planned_date=date(2099, 10, 31), planned_amount=Decimal("40.00"), actual_amount=Decimal("0"),
                         currency="RUB", status="proposed", source_name="ДДС!L4")
    db_session.add_all([early, late]); db_session.flush()
    monkeypatch.setattr(finance_api, "extract_invoice_fields", lambda *_args, **_kwargs: InvoiceFields(
        amount=Decimal("40"), amount_evidence_quote="40,00", currency="RUB",
        counterparty="ООО «БУЛАТ»", counterparty_evidence_quote="ООО «БУЛАТ»",
        payment_purpose="Акт № 17, 10-й этап", payment_purpose_evidence_quote="Акт № 17",
        suggested_category_name=None, category_evidence_quote=None,
        planned_date=date(2099, 10, 20), confidence=0.9, extraction_method="llm",
    ))
    proposed = create_invoice_extraction(document.id, InvoiceExtractionCreate(project_id=project.id), db_session, user)
    item = db_session.get(InvoiceExtractionProposal, proposed["id"])
    assert item.fallback_reason == finance_api.OWN_OUTGOING_INVOICE

    confirmed = confirm_invoice_extraction(proposed["id"], InvoiceExtractionConfirm(), db_session, user)
    assert confirmed["status"] == "confirmed"
    doc_row = db_session.get(CashFlowEntry, item.created_cash_flow_id)
    assert doc_row.direction == "inflow"
    assert doc_row.invoiced_amount == Decimal("40.00")
    assert doc_row.planned_amount == Decimal("0")
    assert doc_row.plan_entry_id == late.id  # the line whose remainder equals the amount
    assert late.planned_amount == Decimal("40.00")

    monkeypatch.setattr("app.api.budget_from_dds.require_project_role", lambda *_args, **_kwargs: None)
    record_plan_fact(project.id, late.id, RecordFactRequest(amount=Decimal("40"), actual_date=date(2099, 10, 25),
                                                            basis_entry_id=doc_row.id), db_session, user)
    db_session.refresh(doc_row)
    assert doc_row.status == "received"
    assert doc_row.actual_date == date(2099, 10, 25)
    receive = [i for i in money_calendar(db_session, project.id, today=date(2099, 9, 1), horizon_days=36500)["items"] if i["kind"] == "receive"]
    assert [i["amount"] for i in receive] == ["100.00"]


def test_repeated_invoice_is_not_booked_twice(db_session, user_factory, monkeypatch):
    user, project, document, _version = _world(db_session, user_factory)
    monkeypatch.setattr(finance_api, "require_project_role", lambda *_args, **_kwargs: None)
    versions = [_version]
    for number in (2, 3):
        extra = DocumentVersion(document_id=document.id, version_number=number, content="Счёт")
        db_session.add(extra); db_session.flush(); versions.append(extra)
    first = InvoiceExtractionProposal(project_id=project.id, source_document_id=document.id,
                                      source_document_version_id=versions[0].id, source_document_sha256="a" * 64,
                                      amount=Decimal("100.00"), counterparty='АО "ЛИНДЕКС"', payment_purpose="Оплата по счёту № 2018",
                                      planned_date=date(2026, 2, 2), status="confirmed", target_kind="cash_flow", extraction_method="llm", confidence=0.9)
    again = InvoiceExtractionProposal(project_id=project.id, source_document_id=document.id,
                                      source_document_version_id=versions[1].id, source_document_sha256="b" * 64,
                                      amount=Decimal("100.00"), counterparty="АО «ЛИНДЕКС»", payment_purpose="Счет на оплату № 2018 от 02.02.2026",
                                      planned_date=date(2026, 2, 5), status="proposed", target_kind="cash_flow", extraction_method="llm", confidence=0.9)
    other = InvoiceExtractionProposal(project_id=project.id, source_document_id=document.id,
                                      source_document_version_id=versions[2].id, source_document_sha256="c" * 64,
                                      amount=Decimal("100.00"), counterparty="АО «ЛИНДЕКС»", payment_purpose="Счет на оплату № 4015",
                                      planned_date=date(2026, 2, 5), status="proposed", target_kind="cash_flow", extraction_method="llm", confidence=0.9)
    db_session.add_all([first, again, other]); db_session.flush()
    with pytest.raises(HTTPException) as refused:
        finance_api._refuse_repeated_invoice(db_session, again)
    assert refused.value.status_code == 409
    assert "Повторный счёт" in refused.value.detail
    finance_api._refuse_repeated_invoice(db_session, other)  # another number — a different invoice
    view = finance_api._proposal_view(db_session, again)
    assert view["duplicate_of"] == document.name
    assert finance_api._proposal_view(db_session, other)["duplicate_of"] is None


def test_marketplace_orders_with_different_numbers_are_not_repeats():
    assert finance_api._invoice_number("Оплата по заказу 0173415668-0025 от 14.10.2026 заклепки") == "0173415668-0025"
    assert finance_api._invoice_number("Оплата по заказу 0173415668-0026 от 14.10.2026 Заклепки") == "0173415668-0026"
    assert finance_api._invoice_number("Оплата счёта № 11 от 13 февраля 2026 г.") == "11"
    assert finance_api._invoice_number("Счет на оплату № 2018 от 02.02.2026") == "2018"
    assert finance_api._invoice_number("Оплата по счету 92 от 19 февраля") == "92"


def test_invoice_date_skips_the_government_decree_in_the_vat_invoice_header():
    text = ("Приложение №1 к постановлению Правительства Российский Федерации от 26 декабря 2011 г. № 1137 "
            "(в ред. Постановления правительства РФ от 16.08.2024 №1096) Счет-фактура См00036665 от 01 апреля 2026 г.")
    assert invoice_extraction._date_hint(text) == date(2026, 4, 1)
    upd = ("(в Пр Российской рации от 16 августа 2024 г, № 1096) Универсальный Счет-фактура № 1702019220-МСК "
           "от 17 февраля 2026 г.")
    assert invoice_extraction._date_hint(upd) == date(2026, 2, 17)
