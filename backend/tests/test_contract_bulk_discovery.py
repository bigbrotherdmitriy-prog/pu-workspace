import pytest

from app.api.contract_discovery import (
    CONTRACT_DISCOVERY_BATCH_SIZE,
    ContractDiscoveryRequest,
    _contract_discovery_batches,
    _contract_parties,
    _party_chain_parent,
    _referenced_existing_contract,
    _short_contract_title,
    discover_contract_fields,
    router,
)
from app.models.organization_contract import Contract
import pytest


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("legal_form", ["Общество с ограниченной ответственностью", "ООО"])
def test_supply_counterparty_uses_role_not_order(reverse, legal_form):
    supplier = f'{legal_form} «Сириус», именуемое в дальнейшем «Поставщик», в лице директора'
    buyer = ('Общество с ограниченной ответственностью «ДИСИАЙ СОЛЮШНС», '
             'в лице генерального директора, действующего на основании Устава, '
             'именуемое в дальнейшем «Покупатель»')
    parties = [supplier, buyer]
    if reverse:
        parties.reverse()
    text = 'Договор поставки (с условием подряда). ' + ', с одной стороны, и '.join(parties)
    result = discover_contract_fields('Договор.docx', text)
    assert result['counterparty'] == f'{legal_form} «Сириус»'
    assert any('по роли поставщика' in item and 'Сириус' in item for item in result['evidence'])


@pytest.mark.parametrize('text', [
    'ООО «Сириус», и ООО «Покупатель», заключили договор поставки.',
    'ООО «Наша компания», именуемое в дальнейшем «Покупатель», договор поставки.',
    'ООО «Первый», именуемое «Поставщик», и ООО «Второй», именуемое «Поставщик», договор поставки.',
    'ООО «Первый», в лице директора, и ООО «Второй», именуемое «Покупатель», договор поставки.',
])
def test_ambiguous_supply_counterparty_remains_unset(text):
    result = discover_contract_fields('Договор.docx', text)
    assert result['counterparty'] is None
    assert any('подтвердите контрагента вручную' in item for item in result['evidence'])


def test_supply_counterparty_does_not_select_bank_at_end():
    result = discover_contract_fields('Договор.docx',
        'Договор поставки. ООО «Сириус», именуемое «Поставщик», '
        'ООО «Наша компания», именуемое «Покупатель», '
        'Реквизиты сторон. АО «Банк», реквизиты банка.')
    assert result['counterparty'] == 'ООО «Сириус»'


def test_non_supply_multiple_parties_are_not_selected_by_order():
    result = discover_contract_fields('Договор.docx',
        'Договор подряда. ООО «Первый», именуемое «Заказчик», '
        'ООО «Второй», именуемое «Подрядчик», заключили договор.')
    assert result['counterparty'] is None


def test_bulk_contract_discovery_route_is_available():
    assert "/projects/{project_id}/contracts/discover-bulk" in {route.path for route in router.routes}
    assert ContractDiscoveryRequest(document_ids=[1, 2]).document_ids == [1, 2]


def test_scanned_contract_with_structured_number_and_legal_body_is_not_rejected():
    result = discover_contract_fields(
        "Б-УЗП130-02-2026.pdf",
        "ООО «Альфа», именуемое Заказчик, и ООО «Бета», именуемое Подрядчик, "
        "заключили настоящий договор. Предмет договора. Цена договора.",
    )
    assert result["is_contract"] is True
    assert result["number"] == "Б-УЗП130-02-2026"
    assert "структурированный номер договора найден в имени файла" in result["evidence"]


def test_operational_form_filename_is_not_mistaken_for_contract():
    result = discover_contract_fields("ОС-15 №6 от 31.07.2026.pdf", "Акт о приеме-передаче оборудования")
    assert result["is_contract"] is False


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
    assert result["confidence"] < 0.5
    assert result["is_contract"] is False
    assert "исключён" in result["evidence"][-1]


def test_contract_content_overrides_estimate_signal_in_combined_pdf_filename():
    result = discover_contract_fields(
        "Договор+сметыs откорр.pdf",
        "Договор № 02/2111-26 от 27.03.2026 г. Заказчик и Подрядчик заключили настоящий "
        "договор. 1. Предмет Договора. Подрядчик выполняет строительно-монтажные работы. "
        "2. Цена договора, порядок расчётов и оплаты.",
    )
    assert result["is_contract"] is True
    assert result["number"] == "02/2111-26"
    assert "содержимое подтверждает самостоятельный договор вопреки имени файла" in result["evidence"]


def test_attachment_name_still_blocks_without_own_contract_number():
    result = discover_contract_fields(
        "Приложение и смета.pdf",
        "Предмет договора. Цена договора. Заказчик и Подрядчик. Смета к договору.",
    )
    assert result["is_contract"] is False
    assert "файл похож на приложение, а не на самостоятельный договор" in result["evidence"]


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


def test_finds_existing_prime_contract_referenced_by_ocr_for_hierarchy():
    prime = Contract(id=4, project_id=1, number="ГК-08-194/25", title="Генподряд", contract_kind="prime_reference")
    wrong_existing = Contract(id=16, project_id=1, number="ом", title="Ошибочная карточка", contract_kind="prime_reference")
    parent = _referenced_existing_contract(
        "Работы выполняются во исполнение государственного контракта № ГК-08-194/25.",
        [prime, wrong_existing], excluded_id=16,
    )
    assert parent is prime


def test_builds_contract_chain_when_parent_contractor_becomes_child_customer():
    prime = Contract(id=4, project_id=1, number="ГК-08-194/25", title="Генподряд", contract_kind="prime_reference")
    prime_text = (
        'ФКУ «Налог-Сервис» ФНС России, именуемое в дальнейшем «Заказчик», с одной стороны, '
        'и ООО «БУЛАТ», именуемое в дальнейшем «Подрядчик», заключили контракт.'
    )
    subcontract_text = (
        'Общество с ограниченной ответственностью «БУЛАТ», именуемое в дальнейшем «Заказчик», '
        'и ООО «ДИСИАЙ СОЛЮШНС», именуемое в дальнейшем «Подрядчик», заключили договор.'
    )
    assert _contract_parties(prime_text) == {"заказчик": "налогсервис", "подрядчик": "булат"}
    assert _contract_parties(subcontract_text) == {"заказчик": "булат", "подрядчик": "дисиайсолюшнс"}
    assert _party_chain_parent(subcontract_text, [(prime, prime_text)]) is prime


def test_uses_exact_number_and_short_subject_as_contract_name():
    result = discover_contract_fields(
        "scan.pdf",
        "Договор № Б-УЗП/130-02-2026. Заказчик и Подрядчик заключили договор. "
        "1. Предмет Договора 1.1. Подрядчик по условиям настоящего Договора принимает на себя "
        "обязательство по выполнению работ по модернизации системы бесперебойного электропитания "
        "в ЦОД г. Дубна и г. Городец, а Заказчик обязуется принять и оплатить Работы. 1.2. Срок работ.",
    )
    assert result["number"] == "Б-УЗП/130-02-2026"
    assert result["title"] == "Модернизации системы бесперебойного электропитания в ЦОД г. Дубна и г. Городец"


def test_short_subject_falls_back_to_filename_when_clause_is_missing():
    assert _short_contract_title("Реквизиты сторон.", "Договор П-17") == "Договор П-17"


@pytest.mark.parametrize("name,content", [
    ("Письмо о допуске.docx", "Просим оформить пропуск по договору № Д-100/25. Заказчик ООО Альфа."),
    ("Акт выполненных работ.pdf", "Акт к договору № Д-100/25. Заказчик и Подрядчик подписали настоящий акт."),
    ("Счет №12.pdf", "Оплата по договору № Д-100/25. Покупатель оплачивает поставщику."),
    ("Протокол совещания.docx", "Обсудили договор № Д-100/25, заказчика и подрядчика."),
    ("Приложение 2.xlsx", "Приложение к договору № Д-100/25. Предмет договора. Цена договора."),
])
def test_reference_to_contract_inside_another_document_is_rejected(name, content):
    result = discover_contract_fields(name, content)
    assert result["is_contract"] is False
    assert result["evidence"][-1].startswith("исключён:")


def test_two_generic_roles_are_not_enough():
    result = discover_contract_fields(
        "Служебная записка.txt",
        "Заказчик передал замечания. Подрядчик должен прислать ответ завтра.",
    )
    assert result["is_contract"] is False


def test_own_heading_number_and_date_are_independent_signals():
    result = discover_contract_fields(
        "scan.pdf",
        "Договор подряда № Д-77/26 от 22.09.2026. Подрядчик выполняет работы.",
    )
    assert result["is_contract"] is True
    assert "собственный заголовок договора найден" in result["evidence"]
    assert "дата найдена рядом с заголовком" in result["evidence"]


def test_unnumbered_full_legal_contract_is_candidate():
    result = discover_contract_fields(
        "Скан документа.pdf",
        "Договор оказания услуг. ООО «Альфа», именуемое Заказчик, и ООО «Бета», "
        "именуемое Исполнитель, заключили настоящий договор. Предмет договора. "
        "Порядок расчетов. Ответственность сторон.",
    )
    assert result["is_contract"] is True


@pytest.mark.parametrize("count,expected_sizes", [
    (1, [1]), (199, [199]), (200, [200]), (201, [200, 1]),
    (638, [200, 200, 200, 38]),
])
def test_contract_discovery_batch_boundaries(count, expected_sizes):
    pins = [{"document_id": index, "version_id": index} for index in range(1, count + 1)]
    batches = _contract_discovery_batches(pins)
    assert [len(batch) for batch in batches] == expected_sizes
    assert all(len(batch) <= CONTRACT_DISCOVERY_BATCH_SIZE for batch in batches)
    assert [item for batch in batches for item in batch] == pins
