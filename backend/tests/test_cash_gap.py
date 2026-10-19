from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.cash_gap import plan_cash_gaps


def _row(**kw):
    defaults = dict(
        status="proposed",
        direction="outflow",
        title="",
        planned_date=date(2026, 1, 1),
        planned_amount=Decimal("0"),
        actual_date=None,
        actual_amount=Decimal("0"),
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _base_rows():
    return [
        _row(title="Январь расход", planned_date=date(2026, 1, 15), planned_amount=Decimal("1600000")),
        _row(title="Февраль расход", planned_date=date(2026, 2, 10), planned_amount=Decimal("38900000")),
        _row(direction="inflow", title="Март приход", planned_date=date(2026, 3, 1), planned_amount=Decimal("46000000")),
        _row(title="Март расход", planned_date=date(2026, 3, 5), planned_amount=Decimal("28200000")),
    ]


def test_gaps_and_worst():
    result = plan_cash_gaps(_base_rows())
    gaps = result["gaps"]
    # March stays negative too: −40.5 + 46 − 28.2 = −22.7 млн.
    assert [g["month"] for g in gaps] == ["2026-01", "2026-02", "2026-03"]
    worst = result["worst"]
    assert worst is not None
    assert worst["month"] == "2026-02"
    assert worst["cumulative"] == "-40500000.00"
    assert result["first_inflow_month"] == "2026-03"
    explanation = result["explanation"]
    assert "феврал" in explanation
    assert "−40,5 млн ₽" in explanation
    assert "март 2026" in explanation


def test_cancelled_ignored():
    rows = _base_rows() + [
        _row(status="cancelled", title="Отмена", planned_date=date(2026, 2, 1), planned_amount=Decimal("1000000")),
    ]
    result = plan_cash_gaps(rows)
    feb = [m for m in result["months"] if m["month"] == "2026-02"][0]
    assert feb["outflow"] == "38900000.00"


def test_zero_planned_without_payment_skipped():
    rows = [_row(title="Счёт", planned_date=date(2026, 2, 1), planned_amount=Decimal("0"))]
    result = plan_cash_gaps(rows)
    assert result["months"] == []
    assert result["worst"] is None


def test_paid_uses_actual():
    rows = [
        _row(
            status="paid",
            title="Оплата",
            planned_date=date(2026, 2, 1),
            planned_amount=Decimal("1000000"),
            actual_date=date(2026, 3, 15),
            actual_amount=Decimal("900000"),
        ),
    ]
    result = plan_cash_gaps(rows)
    by_month = {m["month"]: m for m in result["months"]}
    assert "2026-02" not in by_month
    assert by_month["2026-03"]["outflow"] == "900000.00"
