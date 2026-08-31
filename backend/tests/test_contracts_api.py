from decimal import Decimal

from app.api.organizations_contracts import ContractCreate, _contract_document_score, _contract_source_text, _payment_schedule_candidates, router
from app.models.document import Document
from app.models.organization_contract import Contract


def test_contract_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/organizations" in paths
    assert "/projects/{project_id}/contracts" in paths
    assert "/projects/{project_id}/contracts/{contract_id}" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/initialize-control" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/analyze" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/source-candidates" in paths


def test_contract_payload_defaults_to_active():
    payload = ContractCreate(number="DCI-01", title="Основной договор")
    assert payload.status == "active"
    assert payload.contract_kind == "customer"


def test_revenue_subcontract_payload_keeps_parent_and_terms():
    payload = ContractCreate(
        number="СП-01", title="Монтаж", contract_kind="revenue_subcontract",
        parent_contract_id=12, amount=Decimal("1250000.00"),
        advance_amount=Decimal("250000.00"), retention_percent=Decimal("5"),
    )
    assert payload.parent_contract_id == 12
    assert payload.amount == Decimal("1250000.00")
    assert payload.retention_percent == Decimal("5")


def test_prime_reference_contract_has_an_explicit_non_financial_role():
    payload = ContractCreate(number="ГК-01", title="Генподрядный договор", contract_kind="prime_reference")
    assert payload.contract_kind == "prime_reference"
    assert payload.parent_contract_id is None


def test_payment_schedule_is_extracted_only_with_explicit_date_and_amount():
    rows = _payment_schedule_candidates(
        "Авансовый платеж до 15.09.2026 — 250 000,00 руб.\n"
        "Окончательная оплата 30.11.2026 составляет 1 000 000 руб.\n"
        "Оплата производится по условиям договора без указанной даты."
    )
    assert [(row["planned_date"].isoformat(), row["amount"]) for row in rows] == [
        ("2026-09-15", Decimal("250000.00")),
        ("2026-11-30", Decimal("1000000")),
    ]


def test_contract_analysis_uses_existing_safe_document_text():
    document = Document(
        project_id=1,
        name="Договор.pdf",
        source="google_drive",
        summary="Подрядчик обязан предоставить список сотрудников до 20.09.2026.",
        notes="Проверить срок по исходному документу.",
    )
    text = _contract_source_text(document)
    assert "обязан предоставить" in text
    assert "Проверить срок" in text


def test_contract_candidate_prefers_requisites_in_extracted_text_over_generic_filename():
    contract = Contract(
        project_id=1,
        number="ГК-08-194/25",
        title="Модернизация бесперебойного электропитания",
        counterparty="Налог-Сервис",
        status="active",
    )
    scan = Document(project_id=1, name="скан1412.pdf", source="google_drive")
    score, reasons = _contract_document_score(
        contract,
        scan,
        "Государственный контракт № ГК-08-194/25. Заказчик Налог-Сервис. "
        "Предмет: модернизация системы бесперебойного электропитания.",
    )
    appendix = Document(project_id=1, name="Приложение №2.docx", source="google_drive")
    appendix_score, _ = _contract_document_score(contract, appendix, "График выполнения работ")
    assert score >= 85
    assert score > appendix_score
    assert "совпадает номер договора" in reasons
