from app.api.contract_discovery import ContractDiscoveryRequest, discover_contract_fields, router


def test_bulk_contract_discovery_route_is_available():
    assert "/projects/{project_id}/contracts/discover-bulk" in {route.path for route in router.routes}
    assert ContractDiscoveryRequest(document_ids=[1, 2]).document_ids == [1, 2]


def test_discovers_prime_contract_number_and_kind_from_text():
    result = discover_contract_fields(
        "scan1412.pdf",
        "Государственный контракт № ГК-08-194/25. Предмет договора. Цена договора. "
        "Права и обязанности. Реквизиты сторон. Заказчик и Подрядчик.",
    )
    assert result["number"] == "ГК-08-194/25"
    assert result["contract_kind"] == "prime_reference"
    assert result["confidence"] >= 0.8


def test_discovers_supply_contract_but_keeps_result_reviewable():
    result = discover_contract_fields(
        "Договор поставки П-17.docx",
        "Договор поставки № П-17. Поставщик передаёт оборудование покупателю.",
    )
    assert result["number"] == "П-17"
    assert result["contract_kind"] == "supply"
    assert result["confidence"] < 1
