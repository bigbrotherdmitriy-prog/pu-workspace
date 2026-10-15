from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.contract_terms_ai import extract_contract_terms_ai
from app.api.organizations_contracts import (
    _contract_financial_terms,
    _merge_contract_terms_with_ai,
)


SAMPLE = (
    "2.1.1. Общая цена выполняемых Подрядчиком Работ составляет "
    "677 750 000 (Шестьсот семьдесят семь миллионов семьсот пятьдесят тысяч) рублей 00 копеек, "
    "в тч. НДС 22% — 122 217 213 (...) рублей 11 копеек (далее — Цена Договора)\n"
    "1.2. Срок выполнения Работ не позднее «30» ноября 2026 года.\n"
)


def test_regex_extracts_total_price_vat_and_deadline_from_real_contract():
    terms = _contract_financial_terms(SAMPLE)
    assert terms["amount"] == Decimal("677750000")
    assert terms["vat_mode"] == "rate"
    assert terms["vat_rate"] == Decimal("22")
    assert terms["retention_percent"] is None
    assert terms["performed_to"] == date(2026, 11, 30)


def test_regex_retention_requires_удерж_not_vat():
    vat_line = (
        "Цена договора составляет 1 000 000 руб., в т.ч. НДС 22% — 180 327,87 руб."
    )
    terms = _contract_financial_terms(vat_line)
    assert terms["retention_percent"] is None

    retention_line = "Гарантийное удержание составляет 5% от цены договора."
    terms = _contract_financial_terms(retention_line)
    assert terms["retention_percent"] == Decimal("5")


def test_ai_field_dropped_when_evidence_not_in_text():
    fake = {
        "amount": "1000000",
        "amount_evidence": "Цитата, которой нет в договоре",
        "vat_rate": "",
        "vat_mode": "",
        "vat_evidence": "",
        "advance_amount": "",
        "advance_amount_evidence": "",
        "advance_percent": "",
        "advance_percent_evidence": "",
        "retention_percent": "",
        "retention_evidence": "",
        "signed_at": "",
        "signed_at_evidence": "",
        "performed_from": "",
        "performed_from_evidence": "",
        "performed_to": "",
        "performed_to_evidence": "",
        "warranty_until": "",
        "warranty_until_evidence": "",
    }
    with patch("app.contract_terms_ai.gemini_configured", return_value=True), \
         patch("app.contract_terms_ai._call_model", return_value=fake):
        result = extract_contract_terms_ai(SAMPLE)
    assert result["amount"] is None


def test_ai_field_accepted_when_evidence_is_in_text():
    fake = {
        "amount": "677750000.00",
        "amount_evidence": "Общая цена выполняемых Подрядчиком Работ составляет 677 750 000",
        "vat_rate": "22",
        "vat_mode": "rate",
        "vat_evidence": "в тч. НДС 22%",
        "advance_amount": "",
        "advance_amount_evidence": "",
        "advance_percent": "",
        "advance_percent_evidence": "",
        "retention_percent": "",
        "retention_evidence": "",
        "signed_at": "",
        "signed_at_evidence": "",
        "performed_from": "",
        "performed_from_evidence": "",
        "performed_to": "2026-11-30",
        "performed_to_evidence": "не позднее «30» ноября 2026 года",
        "warranty_until": "",
        "warranty_until_evidence": "",
    }
    with patch("app.contract_terms_ai.gemini_configured", return_value=True), \
         patch("app.contract_terms_ai._call_model", return_value=fake):
        result = extract_contract_terms_ai(SAMPLE)
    assert result["amount"] == Decimal("677750000.00")
    assert result["vat_mode"] == "rate"
    assert result["vat_rate"] == Decimal("22")
    assert result["performed_to"] == date(2026, 11, 30)


def _merge_patch(ai_result):
    return patch(
        "app.api.organizations_contracts.extract_contract_terms_ai",
        return_value=ai_result,
    )


def test_ai_value_wins_over_regex_when_both_present():
    regex_terms = _contract_financial_terms(SAMPLE)
    assert regex_terms["amount"] == Decimal("677750000")
    ai_terms = dict(regex_terms)
    ai_terms["amount"] = Decimal("700000000")
    ai_terms["amount_evidence"] = "Общая цена выполняемых Подрядчиком Работ составляет 700 000 000"
    with _merge_patch(ai_terms):
        merged = _merge_contract_terms_with_ai(SAMPLE, regex_terms)
    assert merged["amount"] == Decimal("700000000")
    assert merged["amount_evidence"] == "Общая цена выполняемых Подрядчиком Работ составляет 700 000 000"
    assert merged["extraction_conflicts"] == [{
        "field": "amount", "ai": "700000000", "regex": "677750000",
    }]


def test_ai_none_keeps_regex_value():
    regex_terms = _contract_financial_terms(SAMPLE)
    ai_terms = dict(regex_terms)
    ai_terms["advance_amount"] = None
    ai_terms["amount"] = None
    with _merge_patch(ai_terms):
        merged = _merge_contract_terms_with_ai(SAMPLE, regex_terms)
    assert merged["amount"] == Decimal("677750000")
    assert merged["vat_rate"] == Decimal("22")
    assert merged["extraction_conflicts"] == []


def test_ai_exception_returns_regex_result_unchanged():
    regex_terms = _contract_financial_terms(SAMPLE)
    with patch(
        "app.api.organizations_contracts.extract_contract_terms_ai",
        side_effect=RuntimeError("boom"),
    ):
        merged = _merge_contract_terms_with_ai(SAMPLE, regex_terms)
    assert merged == regex_terms


def test_conflict_detected_for_dates_and_reported_in_extraction_conflicts():
    regex_terms = _contract_financial_terms(SAMPLE)
    ai_terms = dict(regex_terms)
    ai_terms["performed_to"] = date(2026, 12, 31)
    ai_terms["performed_to_evidence"] = "не позднее «31» декабря 2026 года"
    with _merge_patch(ai_terms):
        merged = _merge_contract_terms_with_ai(SAMPLE, regex_terms)
    assert merged["performed_to"] == date(2026, 12, 31)
    conflicts = merged["extraction_conflicts"]
    assert any(
        item["field"] == "performed_to"
        and item["ai"] == "2026-12-31"
        and item["regex"] == "2026-11-30"
        for item in conflicts
    )


def test_merge_does_not_break_financial_apply_loop():
    regex_terms = _contract_financial_terms(SAMPLE)
    ai_terms = dict(regex_terms)
    ai_terms["amount"] = Decimal("700000000")
    ai_terms["amount_evidence"] = "Общая цена выполняемых Подрядчиком Работ составляет 700 000 000"
    with _merge_patch(ai_terms):
        merged = _merge_contract_terms_with_ai(SAMPLE, regex_terms)
    assert "extraction_conflicts" in merged
    calls: list[str] = []

    class _Row:
        amount = None
        advance_amount = None
        retention_percent = None
        signed_at = None
        warranty_until = None
        vat_mode = None
        vat_rate = None
        performed_from = None
        performed_to = None

    from app.api.organizations_contracts import _apply_contract_financial_terms
    row = _Row()
    result = _apply_contract_financial_terms(row, merged)
    assert row.amount == Decimal("700000000")
    assert set(result.keys()) >= {"applied", "mismatches", "rejected", "terms", "extraction_conflicts"}
    assert calls == []


def test_owner_supply_contract_takes_price_from_specification():
    from app.api.organizations_contracts import _contract_financial_terms
    text = (
        "ДОГОВОР ПОСТАВКИ № 52 г. Москва «02» февраля 2026 г. Цена Договора определяется "
        "Спецификацией (Приложение № 1). За просрочку Поставщик уплачивает неустойку 500 рублей.\n"
        "=== Приложение: Спецификация.docx ===\n"
        "Приложение № 1 к договору № 52 от 2 февраля 2026 г.\n"
        "1 Дверь металлическая 2 шт 145 000,00 290 000,00\n"
        "Строка таблицы 2\nСтрока таблицы 3\nСтрока таблицы 4\nСтрока таблицы 5\nСтрока таблицы 6\n"
        "Итого: 373 620,00\nВ том числе НДС 22%: 67 374,10\nВсего к оплате: 373 620,00\n"
        "1. Условия и срок оплаты: Аванс 80 % (восемьдесят процентов) от суммы настоящей спецификации.\n"
    )
    terms = _contract_financial_terms(text, source_name="52 дог спец.pdf")
    assert terms["amount"] == Decimal("373620.00")
    assert terms["vat_rate"] == Decimal("22")
    assert terms["signed_at"] is not None and terms["signed_at"].isoformat() == "2026-02-02"


def test_vat_rate_derived_from_vat_sum_and_default_22():
    from app.api.organizations_contracts import _contract_financial_terms
    derived = _contract_financial_terms("Цена договора 290 000,00 руб. В том числе НДС: 52 295,08 руб.")
    assert derived["vat_rate"] == Decimal("22")
    default = _contract_financial_terms("Цена договора составляет 100 000 рублей.")
    assert default["vat_mode"] is None and default["vat_default_22"] is True
    exempt = _contract_financial_terms(
        "Исполнитель — самозанятый, применяет налог на профессиональный доход. Цена 55 000 рублей.")
    assert exempt["vat_mode"] == "none"


def test_signed_date_from_file_name():
    from app.api.organizations_contracts import _contract_financial_terms
    terms = _contract_financial_terms("Цена договора 248 000 рублей.", source_name="договор 0421 от 21.04.2026.pdf")
    assert terms["signed_at"].isoformat() == "2026-04-21"


def _contract_row(**values):
    from types import SimpleNamespace
    base = dict(id=None, amount=None, advance_amount=None, retention_percent=None, signed_at=None,
                warranty_until=None, vat_mode=None, vat_rate=None, performed_from=None, performed_to=None)
    base.update(values)
    return SimpleNamespace(**base)


def test_zero_amounts_are_filled_from_contract():
    from app.api.organizations_contracts import _apply_contract_financial_terms
    row = _contract_row(amount=Decimal("0"), advance_amount=Decimal("0"))
    result = _apply_contract_financial_terms(row, {"amount": Decimal("1000000"), "advance_amount": Decimal("300000")})
    assert row.amount == Decimal("1000000") and row.advance_amount == Decimal("300000")
    assert {"amount", "advance_amount"} <= set(result["applied"]) and not result["mismatches"]


def test_system_set_value_is_replaced_but_hand_entered_is_kept(db_session):
    from app.api.organizations_contracts import _apply_contract_financial_terms
    from app.models.audit_log import AuditLog
    auto = _contract_row(id=9101, amount=Decimal("500"))
    result = _apply_contract_financial_terms(auto, {"amount": Decimal("373620")}, db_session)
    assert auto.amount == Decimal("373620") and result["replaced"][0]["previous"] == "500"
    db_session.add(AuditLog(action="contract_updated", entity_type="contract", entity_id=9102,
                            details="fields=amount,title; user=1; version=2"))
    db_session.flush()
    manual = _contract_row(id=9102, amount=Decimal("500"))
    result = _apply_contract_financial_terms(manual, {"amount": Decimal("373620")}, db_session)
    assert manual.amount == Decimal("500") and result["mismatches"][0]["field"] == "amount"
