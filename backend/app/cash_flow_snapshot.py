"""Read-only, exact-money DDS projections from one bounded SQL statement."""

from datetime import date, timedelta
import hashlib
import json

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.finance_money import strict_currency, to_minor_units
from app.models.execution_finance import CashFlowEntry
from app.models.project import Project

CONTRACT_VERSION = "dds-snapshot-v1"
MAX_ROWS = 10000
STATUSES = frozenset({"proposed", "approved", "paid", "received", "cancelled"})
DEFAULT_STATUSES = ("approved", "paid", "received")


class SnapshotError(ValueError):
    def __init__(self, code, message, status_code=409):
        super().__init__(message)
        self.code, self.status_code = code, status_code


def validate_scope(date_from, date_to, statuses):
    if date_to < date_from or (date_to - date_from).days >= 366:
        raise SnapshotError("invalid_period", "Период должен включать от 1 до 366 дней", 422)
    if not statuses or any(status not in STATUSES for status in statuses):
        raise SnapshotError("unsupported_status", "Неизвестный или пустой статус ДДС", 422)


def _money(value):
    # Integers only, including negative net and totals beyond a single NUMERIC(18,2).
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    return {"amount_minor": str(value), "amount": f"{sign}{absolute // 100}.{absolute % 100:02d}"}


def _zero():
    return {"plan": {"inflow": 0, "outflow": 0}, "fact": {"inflow": 0, "outflow": 0}}


def _wire(totals):
    return {component: {**{direction: _money(value) for direction, value in values.items()},
                        "net": _money(values["inflow"] - values["outflow"])}
            for component, values in totals.items()}


def build_snapshot(*, project_id, currency, revision, rows, date_from, date_to,
                   statuses=DEFAULT_STATUSES, contract_id=None, include_review_rows=False):
    validate_scope(date_from, date_to, statuses)
    if len(rows) > MAX_ROWS:
        raise SnapshotError("row_limit_exceeded", "Слишком много строк ДДС; сократите период или выберите договор", 413)
    try:
        strict_currency(currency)
    except ValueError as exc:
        raise SnapshotError("invalid_project_currency", str(exc)) from exc
    scope = {"project_id": project_id, "contract_id": contract_id,
             "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
             "currency": currency, "requested_statuses": sorted(set(statuses)),
             "include_review_rows": include_review_rows}
    months, calendar, categories, objects = {}, {}, {}, {}
    day = date_from
    while True:
        calendar[day.isoformat()] = _zero()
        months.setdefault(day.strftime("%Y-%m"), _zero())
        if day == date_to:
            break
        day += timedelta(days=1)
    total, details, excluded, source = _zero(), [], [], []
    for row in sorted(rows, key=lambda r: (r["planned_date"], r["actual_date"] or date.min, r["id"])):
        status, direction = row["status"], row["direction"]
        if status not in STATUSES:
            raise SnapshotError("unsupported_status", f"Неизвестный статус строки ДДС №{row['id']}")
        if direction not in {"inflow", "outflow"} or (status == "paid" and direction != "outflow") or (status == "received" and direction != "inflow"):
            raise SnapshotError("invalid_status_direction", f"Несогласованное направление строки ДДС №{row['id']}")
        if row["currency"] != currency:
            raise SnapshotError("currency_mismatch", f"Валюта строки ДДС №{row['id']} не совпадает с валютой проекта {currency}")
        try:
            planned, actual = to_minor_units(row["planned_amount"]), to_minor_units(row["actual_amount"])
        except ValueError as exc:
            raise SnapshotError("invalid_money", f"Некорректная сумма строки ДДС №{row['id']}") from exc
        if status in {"paid", "received"} and (not row["actual_date"] or actual <= 0):
            raise SnapshotError("invalid_actual", f"У строки ДДС №{row['id']} отсутствует положительный факт или дата")
        reason = ("awaiting_confirmation" if status == "proposed" else "cancelled" if status == "cancelled"
                  else "status_filtered" if status not in statuses else None)
        values = _zero()
        canonical = {key: value.isoformat() if isinstance(value, date) else value
                     for key, value in row.items() if key not in {"planned_amount", "actual_amount"}}
        canonical.update(planned_amount=_money(planned), actual_amount=_money(actual), exclusion_reason=reason)
        source.append(canonical)
        if reason:
            excluded.append({"id": row["id"], "status": status, "reason": reason})
            if include_review_rows and status in statuses:
                details.append({**canonical, "totals": _wire(values)})
            continue
        category = row.get("category") or "Без статьи"
        object_name = row.get("object_name") or "Без объекта"
        category_values = categories.setdefault(category, _zero())
        object_values = objects.setdefault(object_name, _zero())
        for component, amount, component_date, enabled in (
            ("plan", planned, row["planned_date"], True),
            ("fact", actual, row["actual_date"], status in {"paid", "received"}),
        ):
            if enabled and component_date and date_from <= component_date <= date_to:
                for accumulator in (values, total, category_values, object_values,
                                    months[component_date.strftime("%Y-%m")], calendar[component_date.isoformat()]):
                    accumulator[component][direction] += amount
        details.append({**canonical, "totals": _wire(values)})
    hash_input = {"contract_version": CONTRACT_VERSION, "scope": scope,
                  "snapshot_revision": revision, "source": source}
    digest = hashlib.sha256(json.dumps(hash_input, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"contract_version": CONTRACT_VERSION, "scope": scope,
            "snapshot_revision": revision, "snapshot_hash": "sha256:" + digest,
            "source_row_count": len(rows), "row_limit": MAX_ROWS,
            "months": [{"month": key, "totals": _wire(value)} for key, value in months.items()],
            "calendar": [{"date": key, "totals": _wire(value)} for key, value in calendar.items()],
            "details": details,
            "summary": {"totals": _wire(total),
                        "categories": [{"category": key, "totals": _wire(categories[key])} for key in sorted(categories)],
                        "objects": [{"object_name": key, "totals": _wire(objects[key])} for key in sorted(objects)]},
            "excluded": excluded}


def read_snapshot(db: Session, *, project_id, date_from, date_to, statuses=DEFAULT_STATUSES,
                  contract_id=None, include_review_rows=False):
    validate_scope(date_from, date_to, statuses)
    cash = CashFlowEntry.__table__
    conditions = [cash.c.project_id == Project.id,
                  or_(cash.c.planned_date.between(date_from, date_to),
                      cash.c.actual_date.between(date_from, date_to))]
    if contract_id is not None:
        conditions.append(cash.c.contract_id == contract_id)
    # One MVCC statement reads revision, currency and rows together, even under
    # READ COMMITTED. No ORM identity-map cache, count query or per-view SELECT.
    query = (select(Project.currency.label("project_currency"), Project.cash_flow_revision,
                    *cash.c).select_from(Project).outerjoin(cash, and_(*conditions))
             .where(Project.id == project_id).order_by(cash.c.id).limit(MAX_ROWS + 1))
    result = db.execute(query).mappings().all()
    if not result:
        raise SnapshotError("project_not_found", "Проект не найден", 404)
    rows = tuple({key: row[key] for key in cash.c.keys()} for row in result if row["id"] is not None)
    return build_snapshot(project_id=project_id, currency=result[0]["project_currency"],
                          revision=result[0]["cash_flow_revision"], rows=rows,
                          date_from=date_from, date_to=date_to, statuses=statuses,
                          contract_id=contract_id, include_review_rows=include_review_rows)
