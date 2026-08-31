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
    assert result["is_contract"] is True


def test_does_not_treat_procurement_appendix_as_standalone_supply_contract():
    result = discover_contract_fields(
        "Приложение №2 к описанию объекта закупки Этапы ГК_v22.xlsx",
        "Перечень поставляемого оборудования и этапы поставки.",
    )
    assert result["contract_kind"] == "supply"
    assert result["confidence"] == 0.35
    assert result["is_contract"] is False
    assert "приложение" in result["evidence"][-1]


def test_rejects_short_ocr_noise_as_contract_number():
    result = discover_contract_fields(
        "Б-УЗП130-02-2026.pdf",
        "Договором ом. Предмет договора. Цена договора. Права и обязанности. Реквизиты сторон.",
    )
    assert result["number"] == "Б-УЗП130-02-2026"


def test_referenced_state_contract_does_not_turn_customer_contract_into_prime():
    result = discover_contract_fields(
        "Б-УЗП130-02-2026.pdf",
        "Договор подряда № Б-УЗП130-02-2026. Заказчик и Подрядчик. Предмет договора. "
        + ("условия выполнения работ " * 140)
        + "Работы связаны с государственным контрактом и генподрядчиком.",
    )
    assert result["number"] == "Б-УЗП130-02-2026"
    assert result["contract_kind"] == "customer"
