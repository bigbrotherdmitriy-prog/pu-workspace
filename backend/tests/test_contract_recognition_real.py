"""Real contract preambles from the owner's Дубна/Городец run (15.10.2026)."""
from app.api.contract_discovery import (
    _discovered_counterparty, _normalized_party, _spaced_contract_number, discover_contract_fields,
)

MAIN = ("Договор № 6 - FIL /730-02- 2026 г. Москва «7» редокс. 2026 г. Общество с ограниченной ответственностью «БУЛАТ», "
        "именуемое в дальнейшем «Заказчик», в лице генерального директора Комарова Александра Германовича, действующего на "
        "основании Устава, с одной стороны, и ООО «ДИСИАЙ СОЛЮШНС», именуемый в дальнейшем «Подрядчик», в лице генерального "
        "директора Рамзес Леонид Вячеславовича, в дальнейшем именуемые «Стороны», заключили настоящий договор. "
        "1. Предмет Договора 1.1. работы в Филиале ФКУ «Налог-Сервис» ФНС России по ЦОД в г. Дубна")
SUB = ("Договор № 02/2111-26 г. Мос ква «27» марта 2026 г. Общество с ограниченной ответственностью «ДИСИАЙ СОЛЮШНС», "
       "именуемое в дальнейшем «Заказчик», в лице генерального директора Рамзес Леонид Вячеславовича, действующего на основании "
       "Устава, с одной стороны, и Индивидуального предпринимателя Демиденко Юри я Сергеевича, именуемый в дальнейшем «Подрядчик», "
       ", действующего на основании Устава, в дальнейшем именуемые «Стороны», заключили настоящий Договор")
SELF = ("ДОГОВОР № 01/ 21 11 -2 6 возмездного оказания услуг с физическим лицом, являющимся плательщиком налога на профессиональный "
        "доход (самозанятым физическим лицом) г. Москва « 20 » февраля 202 6 г. Общество с ограниченной ответственностью « Дисиай "
        "Солюшнс », именуемое в дальнейшем «Заказчик», в лице Генерального директора Рамзеса Леонида Вячеславовича , действующего на "
        "основании Устава, с одной стороны, и Граждан ин РФ , Кобельков Александр Александрович , (самозанятый гражданин РФ), "
        "именуем ый в дальнейшем «Исполнитель», с другой стороны, вместе именуемые «Стороны»")


def test_spaced_numbers():
    assert _spaced_contract_number(MAIN) == "6-FIL/730-02-2026"
    assert _spaced_contract_number(SELF) == "01/2111-26"
    assert _spaced_contract_number(SUB) == "02/2111-26"


def test_main_contract_counterparty_is_the_customer_not_the_site():
    name, evidence = _discovered_counterparty(MAIN, "customer", ("ООО ДИСИАЙ СОЛЮШНС",))
    assert name == "ООО «БУЛАТ»"
    assert "заказчика" in evidence


def test_subcontract_entrepreneur_in_nominative():
    name, _ = _discovered_counterparty(SUB, "downstream_subcontract")
    assert name == "ИП Демиденко Юрий Сергеевич"


def test_self_employed_person_is_the_counterparty():
    name, _ = _discovered_counterparty(SELF, "downstream_subcontract")
    assert name == "Кобельков Александр Александрович (самозанятый)"


def test_party_normalization():
    assert _normalized_party("Общества с ограниченной ответственностью «БУЛАТ»") == "ООО «БУЛАТ»"


def test_full_fields_take_number_from_text_not_filename():
    fields = discover_contract_fields("Б-УЗП130-02-2026.pdf", MAIN)
    assert fields["number"] == "6-FIL/730-02-2026"
    assert discover_contract_fields("Договор с Кобельковым Дубна ЦОД.docx", SELF)["number"] == "01/2111-26"


def test_without_knowing_our_company_the_customer_is_not_guessed():
    assert _discovered_counterparty(MAIN, "customer")[0] is None


def test_start_and_finish_dates_from_numbered_clauses_with_ocr_spaces():
    from datetime import date
    from app.api.organizations_contracts import _contract_financial_terms
    text = ("1.4. Срок выполнения Работ по Договору: 1.4.1. Начало Работ: «27» марта 2026 года при условии передачи "
            "проекта с печатью «В производство работ». 1.4.1. Ок ончание Работ: «31» декабря 2026 года. 2. Цена Договора")
    terms = _contract_financial_terms(text)
    assert terms["performed_from"] == date(2026, 3, 27)
    assert terms["performed_to"] == date(2026, 12, 31)


def test_own_company_as_customer_picks_the_provider_even_before_kind_is_known():
    own = ("ООО ДИСИАЙ СОЛЮШНС",)
    assert discover_contract_fields("sub.pdf", SUB, own)["counterparty"] == "ИП Демиденко Юрий Сергеевич"
    assert discover_contract_fields("Кобельков.docx", SELF, own)["counterparty"] == "Кобельков Александр Александрович (самозанятый)"


def test_price_with_equals_sign_spaced_vat_and_dash_kopecks_advance():
    from decimal import Decimal
    from app.api.organizations_contracts import _contract_financial_terms
    text = ("2.1. Стоимость всех поручаемых «Подрядчику» по настоящему Договору работ определяется утвержденным локальным "
            "сметным расчетом № 5 от 24 . 02 .20 2 6 г. (Приложение 1) в сумме 1000000 = ( Один миллион ) руб. 00 копеек, "
            "в т.ч. НДС 2 2 % - 180327-87 = ( С то восемьдесят тысяч ) руб . 87 копее к .\n"
            "3.1. Аванс в размере 300000-00 ( Триста тысяч) руб. перечисляется в течение 5 дней.")
    terms = _contract_financial_terms(text)
    assert terms["amount"] == Decimal("1000000")
    assert terms["vat_mode"] == "rate" and terms["vat_rate"] == Decimal("22")
    assert terms["advance_amount"] == Decimal("300000.00")
    assert terms["advance_percent"] is None
