from app.api.organizations_contracts import ContractCreate, _contract_source_text, router
from app.models.document import Document


def test_contract_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/organizations" in paths
    assert "/projects/{project_id}/contracts" in paths
    assert "/projects/{project_id}/contracts/{contract_id}" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/initialize-control" in paths
    assert "/projects/{project_id}/contracts/{contract_id}/analyze" in paths


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
