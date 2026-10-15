from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.contract_terms_ai import extract_contract_terms_ai
from app.api.organizations_contracts import _contract_financial_terms


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
