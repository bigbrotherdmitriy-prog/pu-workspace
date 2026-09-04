from datetime import date
import pytest
from app.api.telegram import _parse_ru_date


def test_parse_telegram_due_date():
    assert _parse_ru_date("31.08.2026") == date(2026, 8, 31)


def test_reject_invalid_telegram_due_date():
    with pytest.raises(ValueError):
        _parse_ru_date("2026-08-31")
