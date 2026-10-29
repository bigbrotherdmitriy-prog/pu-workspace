"""Варианты смягчения кассового разрыва.

Плановый ДДС неизменяем: модуль ничего не пишет в базу, а лишь считает,
какими переносами можно поднять минимальный накопленный остаток.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

SKIP_STATUSES = {"cancelled", "deleted", "rejected"}
MAX_STEPS = 200
MAX_DELAY_DAYS = 30
MAX_PULL_DAYS = 14
MAX_COMBINED_MOVES = 10
ZERO = Decimal("0")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


class _Event:
    def __init__(self, row: Any, today: date):
        self.id = row.id
        self.title = getattr(row, "title", "") or ""
        self.direction = "inflow" if row.direction == "inflow" else "outflow"
        self.amount = Decimal(row.planned_amount)
        self.origin: date = row.planned_date
        self.date: date = row.planned_date
        actual = getattr(row, "actual_amount", None) or ZERO
        done = actual > 0 or getattr(row, "status", None) == "paid" or self.origin < today
        self.movable = not done

    @property
    def signed(self) -> Decimal:
        return self.amount if self.direction == "inflow" else -self.amount

    @property
    def shift(self) -> int:
        return (self.date - self.origin).days

    def copy(self) -> "_Event":
        clone = _Event.__new__(_Event)
        clone.__dict__.update(self.__dict__)
        return clone


def _curve(events: list[_Event], opening: Decimal) -> tuple[Decimal, date | None, int]:
    """(минимум, дата минимума, число дней с отрицательным остатком)."""
    by_day: dict[date, Decimal] = {}
    for ev in events:
        by_day[ev.date] = by_day.get(ev.date, ZERO) + ev.signed
    balance = opening
    best, best_day, negative = opening, None, 0
    for day in sorted(by_day):
        balance += by_day[day]
        if balance < 0:
            negative += 1
        if best_day is None or balance < best:
            best, best_day = balance, day
    return best, best_day, negative


def _targets(ev: _Event, mode: str, min_day: date, today: date) -> list[date]:
    """Даты, куда можно сдвинуть событие: на один день и до предела."""
    if not ev.movable:
        return []
    if mode == "delay" and ev.direction == "outflow" and ev.date <= min_day:
        limit = ev.origin + timedelta(days=MAX_DELAY_DAYS)
        if ev.date < limit:
            return sorted({ev.date + timedelta(days=1), limit})
    if mode == "pull" and ev.direction == "inflow" and ev.date > min_day:
        limit = max(today, ev.origin - timedelta(days=MAX_PULL_DAYS))
        if ev.date > limit:
            return sorted({ev.date - timedelta(days=1), limit})
    return []


def _run(events: list[_Event], modes: tuple[str, ...], today: date, opening: Decimal, cap: int | None):
    work = [e.copy() for e in events]
    for _ in range(MAX_STEPS):
        cur_min, min_day, _neg = _curve(work, opening)
        if cur_min >= 0 or min_day is None:
            break
        moved_count = sum(1 for e in work if e.shift != 0)
        best = None
        for mode in modes:
            for ev in work:
                if cap is not None and ev.shift == 0 and moved_count >= cap:
                    continue
                for target in _targets(ev, mode, min_day, today):
                    old = ev.date
                    ev.date = target
                    new_min, _d, _n = _curve(work, opening)
                    ev.date = old
                    gain = new_min - cur_min
                    if gain <= 0:
                        continue
                    days = abs((target - old).days) or 1
                    key = (gain / Decimal(days), ev.amount, -ev.id)
                    if best is None or key > best[0]:
                        best = (key, ev, target)
        if best is None:
            break
        _key, ev, target = best
        ev.date = target
    return work


def _variant(key: str, title: str, work: list[_Event], before_min: Decimal, opening: Decimal):
    moved = [e for e in work if e.shift != 0]
    if not moved:
        return None
    new_min, new_day, _n = _curve(work, opening)
    moves = [
        {
            "entry_id": e.id,
            "title": e.title,
            "direction": e.direction,
            "amount": _money(e.amount),
            "from": e.origin.isoformat(),
            "to": e.date.isoformat(),
            "days": e.shift,
        }
        for e in sorted(moved, key=lambda x: (x.origin, x.id))
    ]
    return {
        "key": key,
        "title": title,
        "min_balance": _money(new_min),
        "min_date": new_day.isoformat() if new_day else None,
        "improvement": _money(new_min - before_min),
        "moves": moves,
        "moves_count": len(moves),
        "max_shift_days": max(abs(e.shift) for e in moved),
        "resolved": new_min >= 0,
    }


def propose_smoothing(rows: list[Any], today: date, opening_balance: Decimal = ZERO) -> dict:
    events = [
        _Event(r, today)
        for r in rows
        if getattr(r, "status", None) not in SKIP_STATUSES
        and getattr(r, "planned_date", None) is not None
        and (getattr(r, "planned_amount", None) or ZERO) != 0
    ]
    events.sort(key=lambda e: (e.date, e.id))
    before_min, before_day, negative = _curve(events, opening_balance)
    before = {
        "min_balance": _money(before_min),
        "min_date": before_day.isoformat() if before_day else None,
        "negative_days": negative,
    }
    if before_min >= 0:
        return {"before": before, "variants": [], "ok": True}
    specs = (
        ("delay_payments", "Отложить платежи", ("delay",), None),
        ("pull_receipts", "Приблизить поступления", ("pull",), None),
        ("combined", "Минимум изменений вместе", ("delay", "pull"), MAX_COMBINED_MOVES),
    )
    variants = []
    for key, title, modes, cap in specs:
        v = _variant(key, title, _run(events, modes, today, opening_balance, cap), before_min, opening_balance)
        if v is not None:
            variants.append(v)
    return {"before": before, "variants": variants, "ok": False}
