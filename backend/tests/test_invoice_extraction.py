"""Tests for invoice extraction: AI primary, regex fallback."""
from __future__ import annotations

from decimal import Decimal

from app import invoice_extraction


class _DummyDB:
    pass


def _stub_policy(monkeypatch):
    monkeypatch.setattr(invoice_extraction, "policy_mode_for_project", lambda db, pid: "default")
    monkeypatch.setattr(invoice_extraction, "apply_ai_policy_mode", lambda mode, text: text)


def test_ai_without_amount_falls_back_to_regex(monkeypatch):
    _stub_policy(monkeypatch)
    monkeypatch.setattr(invoice_extraction, "ai_configured", lambda: True)

    def fake_ai(text, filename, categories):
        return {
            "amount": None,
            "amount_evidence_quote": None,
            "counterparty": "ООО Ромашка",
            "counterparty_evidence_quote": "ООО Ромашка",
            "currency": "RUB",
            "confidence": "high",
        }

    monkeypatch.setattr(invoice_extraction, "extract_invoice_fields_with_gemini", fake_ai)

    text = "Счёт на оплату 12345 руб. от ООО Ромашка"
    result = invoice_extraction.extract_invoice_fields(
        _DummyDB(), 1, text, "invoice.txt", []
    )

    assert result.amount == Decimal("12345.00")
    assert result.amount_evidence_quote is not None
    assert result.counterparty == "ООО Ромашка"
    assert result.extraction_method == "llm"


def test_deepseek_configured_calls_ai(monkeypatch):
    _stub_policy(monkeypatch)
    monkeypatch.setattr(invoice_extraction, "ai_configured", lambda: True)

    called = {"count": 0}

    def fake_ai(text, filename, categories):
        called["count"] += 1
        return {
            "amount": 1000,
            "amount_evidence_quote": "1000 руб.",
            "counterparty": "ООО Тест",
            "counterparty_evidence_quote": "ООО Тест",
            "currency": "RUB",
            "confidence": "high",
        }

    monkeypatch.setattr(invoice_extraction, "extract_invoice_fields_with_gemini", fake_ai)

    text = "1000 руб. ООО Тест"
    result = invoice_extraction.extract_invoice_fields(
        _DummyDB(), 1, text, "invoice.txt", []
    )

    assert called["count"] == 1
    assert result.extraction_method == "llm"
    assert result.amount == Decimal("1000.00")
