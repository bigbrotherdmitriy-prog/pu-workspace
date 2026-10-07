from datetime import date
from decimal import Decimal

from app.api.organizations_contracts import ContractCreate, ContractDelete, ContractLinkUpdate, _apply_contract_financial_terms, _contract_dependencies, _contract_document_score, _contract_financial_terms, _contract_source_text, _payment_schedule_candidates, router
from app.models.contract_document_link import ContractDocumentLink
from app.models.document import Document
from app.models.organization_contract import Contract, Organization
from app.models.project import Project


def test_contract_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/organizations" in paths
    assert "/projects/{project_id}/contracts" in paths
    assert "/projects/{project_id}/contracts/{contract_id}" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/initialize-control" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/analyze" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/source-candidates" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/deletion-preview" in paths
    delete_route = next(route for route in router.routes if route.path == "/projects/{project_id}/contracts/{contract_id}" and "DELETE" in route.methods)
    assert delete_route


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


def test_contract_update_accepts_commercial_fields_and_delete_requires_confirmation():
    update = ContractLinkUpdate(
        expected_record_version=1,
        number="СП-02", title="Монтаж и ПНР", counterparty="ООО Исполнитель",
        amount=Decimal("1500000"), advance_amount=Decimal("300000"),
        retention_percent=Decimal("5"), signed_at="2026-08-31", status="active",
    )
    assert update.number == "СП-02"
    assert update.amount == Decimal("1500000")
    assert update.signed_at.isoformat() == "2026-08-31"
    assert ContractDelete(confirmation="СП-02", expected_record_version=1).confirmation == "СП-02"


def test_contract_can_be_archived_without_deleting_its_links():
    update = ContractLinkUpdate(expected_record_version=1, status="archived")
    assert update.status == "archived"


def test_physical_contract_delete_is_blocked_by_document_and_tree_links(db_session):
    organization = Organization(name="Synthetic owner")
    db_session.add(organization); db_session.flush()
    project = Project(name="Synthetic project", organization_id=organization.id)
    db_session.add(project); db_session.flush()
    parent = Contract(project_id=project.id, number="ГК-1", title="Головной", status="active")
    db_session.add(parent); db_session.flush()
    child = Contract(project_id=project.id, number="СП-1", title="Дочерний", status="active", parent_contract_id=parent.id)
    document = Document(project_id=project.id, name="Договор.pdf", source="synthetic", status="ready")
    db_session.add_all([child, document]); db_session.flush()
    db_session.add(ContractDocumentLink(project_id=project.id, contract_id=parent.id, document_id=document.id))
    db_session.flush()

    assert _contract_dependencies(db_session, project.id, parent.id) == {
        "child_contracts": 1,
        "documents": 1,
    }


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


def test_payment_schedule_does_not_join_building_code_date_to_specification_price():
    collapsed_pdf = (
        "Оплата производится после подписания договора. "
        "Стены должны соответствовать СНиП 31.01.2003. "
        "Приложение № 1: дверь 2 шт 145 000,00 руб., сумма 290 000,00 руб."
    )

    assert _payment_schedule_candidates(collapsed_pdf) == []


def test_payment_schedule_rejects_a_standard_reference_even_in_payment_clause():
    assert _payment_schedule_candidates(
        "Оплата 290 000,00 руб. после проверки по ГОСТ 31.01.2003."
    ) == []


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


def test_extracts_and_applies_contract_price_advance_and_retention():
    terms = _contract_financial_terms(
        "Цена настоящего договора составляет 10 000 000,00 руб.\n"
        "Заказчик выплачивает аванс в размере 20%.\n"
        "Гарантийное удержание составляет 5%."
    )
    assert terms["amount"] == Decimal("10000000.00")
    assert terms["advance_amount"] == Decimal("2000000.00")
    assert terms["retention_percent"] == Decimal("5")
    contract = Contract(project_id=1, number="1", title="Работы", status="active")
    check = _apply_contract_financial_terms(contract, terms)
    assert set(check["applied"]) == {"amount", "advance_amount", "retention_percent"}


def test_financial_check_reports_mismatch_without_overwriting_user_value():
    contract = Contract(project_id=1, number="1", title="Работы", status="active", amount=Decimal("9000000"))
    check = _apply_contract_financial_terms(contract, _contract_financial_terms(
        "Стоимость работ составляет 10 000 000 руб."
    ))
    assert contract.amount == Decimal("9000000")
    assert check["mismatches"][0]["field"] == "amount"


def test_extracts_signed_date_vat_period_and_warranty_with_evidence():
    """ADR V6-10 / MVP-4 step 8б: dates and VAT are extracted with a citation,
    not just amount/advance/retention."""
    terms = _contract_financial_terms(
        "Договор подписан 01.02.2026.\n"
        "В том числе НДС 20%.\n"
        "Срок выполнения работ с 01.03.2026 по 30.06.2026.\n"
        "Гарантийный срок действует до 31.12.2027."
    )
    assert terms["signed_at"] == date(2026, 2, 1)
    assert terms["signed_at_evidence"] and "01.02.2026" in terms["signed_at_evidence"]
    assert terms["vat_mode"] == "rate"
    assert terms["vat_rate"] == Decimal("20")
    assert terms["performed_from"] == date(2026, 3, 1)
    assert terms["performed_to"] == date(2026, 6, 30)
    assert terms["warranty_until"] == date(2027, 12, 31)

    contract = Contract(project_id=1, number="1", title="Работы", status="active", vat_mode="unspecified")
    check = _apply_contract_financial_terms(contract, terms)
    assert contract.signed_at == date(2026, 2, 1)
    assert (contract.vat_mode, contract.vat_rate) == ("rate", Decimal("20"))
    assert (contract.performed_from, contract.performed_to) == (date(2026, 3, 1), date(2026, 6, 30))
    assert contract.warranty_until == date(2027, 12, 31)
    assert set(check["applied"]) >= {"signed_at", "vat_mode", "performed_from", "performed_to", "warranty_until"}


def test_vat_none_phrasing_is_recognised_without_a_rate():
    terms = _contract_financial_terms("Работы выполняются без НДС.")
    assert terms["vat_mode"] == "none"
    assert terms["vat_rate"] is None


def test_an_unrelated_percent_on_a_line_that_merely_mentions_vat_is_not_a_false_rate():
    """Live incident: a line that only contains the word 'ндс' plus some
    unrelated percent elsewhere on the same line (not immediately after an
    explicit VAT phrase) must not be read as the VAT rate."""
    terms = _contract_financial_terms(
        "Стороны допускают изменение сроков не более чем на 2% от общей "
        "продолжительности работ; вопросы НДС регулируются законодательством РФ."
    )
    assert terms["vat_mode"] is None
    assert terms["vat_rate"] is None


def test_self_contradictory_period_is_rejected_not_silently_applied():
    """A document where the start is literally after the end in the text
    itself must not be written into the contract -- it needs a human look,
    per the plan's 'подтверждение выхода за период с записью в аудит'."""
    contract = Contract(project_id=1, number="1", title="Работы", status="active")
    terms = _contract_financial_terms(
        "Срок выполнения работ с 30.06.2026 по 01.03.2026."
    )
    check = _apply_contract_financial_terms(contract, terms)
    assert contract.performed_from is None and contract.performed_to is None
    assert "performed_from" not in check["applied"]
    assert check["rejected"][0]["field"] == "performed_from"


def test_period_mismatch_against_an_existing_value_is_flagged_not_overwritten():
    contract = Contract(
        project_id=1, number="1", title="Работы", status="active",
        performed_from=date(2026, 1, 1), performed_to=date(2026, 12, 31),
    )
    check = _apply_contract_financial_terms(contract, _contract_financial_terms(
        "Срок выполнения работ с 01.03.2026 по 30.06.2026."
    ))
    assert (contract.performed_from, contract.performed_to) == (date(2026, 1, 1), date(2026, 12, 31))
    assert check["mismatches"][0]["field"] == "performed_from"
