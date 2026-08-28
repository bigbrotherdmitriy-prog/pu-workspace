from app.api.organizations_contracts import ContractCreate, _contract_document_score, _contract_source_text, router
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
