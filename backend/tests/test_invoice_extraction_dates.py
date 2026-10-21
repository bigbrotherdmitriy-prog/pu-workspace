"""Tests for invoice date and INN extraction."""
from __future__ import annotations

from datetime import date

from app.invoice_extraction import (
    _date_hint,
    _planned_date_hint,
    buyer_name,
    supplier_inn,
)


def test_planned_date_hint_text_month():
    assert _planned_date_hint("Счет на оплату № 1324477402 от 02 сентября 2026 г.") == date(2026, 9, 2)


def test_planned_date_hint_deadline_wins():
    text = "Счет № 150 от «09» сентября 2026 г. Оплатить до 15 сентября 2026"
    assert _planned_date_hint(text) == date(2026, 9, 15)


def test_date_hint_may_and_first_by_position():
    assert _date_hint("от 1 мая 2026 года") == date(2026, 5, 1)
    assert _date_hint("27.01.2026 ... 3 марта 2026") == date(2026, 1, 27)


def test_supplier_inn_only_after_supplier_words():
    assert supplier_inn('Поставщик: ООО "ДИСИАЙ СОЛЮШНС", ИНН 7716888076, КПП ...') == "7716888076"
    assert supplier_inn("Покупатель: ООО Х, ИНН 7700000000") is None


def test_buyer_name():
    assert buyer_name('Покупатель: ООО "БУЛАТ", ИНН 7700000000') == 'ООО "БУЛАТ"'


def test_buyer_name_stops_at_the_next_label():
    from app.invoice_extraction import buyer_name
    assert buyer_name("Покупатель: ИП ШМЕЛЕВ НИКИТА ВИКТОРОВИЧ Получатель № Товары (работы, услуги)") == "ИП ШМЕЛЕВ НИКИТА ВИКТОРОВИЧ"
