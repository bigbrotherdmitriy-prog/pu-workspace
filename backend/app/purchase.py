from __future__ import annotations

import re
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.execution_finance import CashFlowEntry
from app.models.invoice_line import InvoiceLine
from app.models.purchase import MaterialReceipt, PurchaseItem, PurchaseItemSource
from app.models.rd import RdSpecItem, RdVolume

ALLOWED_STATUSES = {"need", "ordered", "paid", "received"}

_TAIL_RE = re.compile(r"\(\s*[^()]*?\u2013\s*аналогично\s*\)", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_EDGE_RE = re.compile(r"^[\s\.,;:!?\-\(\)\[\]\{\}\"'«»]+|[\s\.,;:!?\-\(\)\[\]\{\}\"'«»]+$")


def _norm(text: str | None) -> str:
    if text is None:
        return ""
    value = str(text).lower()
    value = _TAIL_RE.sub(" ", value)
    value = _WS_RE.sub(" ", value).strip()
    value = _EDGE_RE.sub("", value)
    return value


def group_key(name: str | None, type_mark: str | None, unit: str | None) -> str:
    return "|".join(_norm(part) for part in (name, type_mark, unit))


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def rebuild(db: Session, project_id: int) -> dict:
    specs = db.execute(
        select(RdSpecItem)
        .where(
            RdSpecItem.project_id == project_id,
            RdSpecItem.status == "confirmed",
        )
        .order_by(RdSpecItem.id)
    ).scalars().all()

    groups: dict[str, list[RdSpecItem]] = {}
    for spec in specs:
        key = group_key(spec.name, spec.type_mark, spec.unit)
        groups.setdefault(key, []).append(spec)

    existing = db.execute(
        select(PurchaseItem).where(PurchaseItem.project_id == project_id)
    ).scalars().all()
    by_key = {item.group_key: item for item in existing}

    created = 0
    updated = 0
    kept_ids: set[int] = set()

    for key, specs_in_group in groups.items():
        first = specs_in_group[0]
        total = sum((_to_decimal(s.qty) for s in specs_in_group), Decimal("0"))
        item = by_key.get(key)
        if item is None:
            item = PurchaseItem(
                project_id=project_id,
                group_key=key,
                name=first.name,
                type_mark=first.type_mark,
                manufacturer=first.manufacturer,
                unit=first.unit,
                qty_needed=total,
                status="need",
                qty_ordered=Decimal("0"),
            )
            db.add(item)
            db.flush()
            created += 1
        else:
            item.name = first.name
            item.type_mark = first.type_mark
            item.manufacturer = first.manufacturer
            item.unit = first.unit
            item.qty_needed = total
            updated += 1
        kept_ids.add(item.id)

        # Пересоздаём связи
        old_sources = db.execute(
            select(PurchaseItemSource).where(PurchaseItemSource.item_id == item.id)
        ).scalars().all()
        for src in old_sources:
            db.delete(src)
        db.flush()
        for spec in specs_in_group:
            db.add(PurchaseItemSource(
                item_id=item.id,
                spec_item_id=spec.id,
                qty=spec.qty,
            ))
        db.flush()

    # Удаляем "need" строки, у которых больше нет подтверждённых позиций
    removed = 0
    for item in existing:
        if item.id in kept_ids:
            continue
        if item.status == "need":
            db.delete(item)
            removed += 1
    db.flush()

    db.commit()
    return {
        "items": len(groups),
        "created": created,
        "updated": updated,
        "removed": removed,
    }


def _collect_volumes(db: Session, item_id: int) -> list[str]:
    rows = db.execute(
        select(RdVolume.title, RdVolume.id)
        .join(RdSpecItem, RdSpecItem.volume_id == RdVolume.id)
        .join(
            PurchaseItemSource,
            PurchaseItemSource.spec_item_id == RdSpecItem.id,
        )
        .where(PurchaseItemSource.item_id == item_id)
        .order_by(RdVolume.id)
    ).all()
    titles: list[str] = []
    seen: set[str] = set()
    for title, _id in rows:
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
    return titles


def _item_to_dict(db: Session, item: PurchaseItem) -> dict:
    needed = _to_decimal(item.qty_needed)
    ordered = _to_decimal(item.qty_ordered)
    left = needed - ordered
    if left < 0:
        left = Decimal("0")
    return {
        "id": item.id,
        "name": item.name,
        "type_mark": item.type_mark,
        "manufacturer": item.manufacturer,
        "unit": item.unit,
        "qty_needed": str(needed),
        "qty_ordered": str(ordered),
        "qty_left": str(left),
        "status": item.status,
        "note": item.note,
        "volumes": _collect_volumes(db, item.id),
    }


def list_items(db: Session, project_id: int, status: str | None = None) -> list[dict]:
    stmt = select(PurchaseItem).where(PurchaseItem.project_id == project_id)
    if status:
        stmt = stmt.where(PurchaseItem.status == status)
    stmt = stmt.order_by(PurchaseItem.name, PurchaseItem.id)
    items = db.execute(stmt).scalars().all()
    return [_item_to_dict(db, item) for item in items]


def _get_item(db: Session, project_id: int, item_id: int) -> PurchaseItem:
    item = db.execute(
        select(PurchaseItem).where(
            PurchaseItem.id == item_id,
            PurchaseItem.project_id == project_id,
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    return item


def set_status(
    db: Session,
    project_id: int,
    item_id: int,
    status: str,
    qty_ordered=None,
    note=None,
) -> dict:
    if status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail="invalid status")
    item = _get_item(db, project_id, item_id)
    item.status = status
    if status == "need":
        item.qty_ordered = Decimal("0")
    elif qty_ordered is not None:
        item.qty_ordered = _to_decimal(qty_ordered)
    elif status == "ordered":
        item.qty_ordered = _to_decimal(item.qty_needed)
    if note is not None:
        item.note = note
    db.commit()
    db.refresh(item)
    return _item_to_dict(db, item)


def summary(db: Session, project_id: int) -> dict:
    items = db.execute(
        select(PurchaseItem.status).where(PurchaseItem.project_id == project_id)
    ).all()
    result = {"total": 0, "need": 0, "ordered": 0, "paid": 0, "received": 0}
    for (status,) in items:
        result["total"] += 1
        if status in result:
            result[status] += 1
    return result


def _is_paid(entry: CashFlowEntry) -> bool:
    if entry.status == "paid":
        return True
    invoiced = _to_decimal(entry.invoiced_amount)
    actual = _to_decimal(entry.actual_amount)
    if actual > 0 and invoiced > 0 and actual >= invoiced:
        return True
    return False


def report(db: Session, project_id: int) -> dict:
    items = db.execute(
        select(PurchaseItem)
        .where(PurchaseItem.project_id == project_id)
        .order_by(PurchaseItem.name, PurchaseItem.id)
    ).scalars().all()

    rows = db.execute(
        select(InvoiceLine, CashFlowEntry)
        .join(CashFlowEntry, CashFlowEntry.id == InvoiceLine.cash_flow_entry_id)
        .where(
            InvoiceLine.project_id == project_id,
            InvoiceLine.purchase_item_id.is_not(None),
        )
    ).all()

    by_item: dict[int, list[tuple[InvoiceLine, CashFlowEntry]]] = {}
    for line, entry in rows:
        if line.match_status != "confirmed":
            continue
        by_item.setdefault(line.purchase_item_id, []).append((line, entry))

    result_items: list[dict] = []
    totals_positions = 0
    totals_fully_ordered = 0
    totals_fully_paid = 0
    totals_needed = Decimal("0")
    totals_ordered = Decimal("0")
    totals_paid = Decimal("0")

    for item in items:
        needed = _to_decimal(item.qty_needed)
        ordered = Decimal("0")
        paid = Decimal("0")
        invoices: list[dict] = []
        seen_entries: dict[int, dict] = {}

        for line, entry in by_item.get(item.id, []):
            qty = _to_decimal(line.qty)
            ordered += qty
            entry_paid = _is_paid(entry)
            if entry_paid:
                paid += qty
            if entry.id in seen_entries:
                existing = seen_entries[entry.id]
                existing["qty"] = str(_to_decimal(existing["qty"]) + qty)
            else:
                info = {
                    "entry_id": entry.id,
                    "title": entry.title,
                    "counterparty": entry.counterparty,
                    "qty": str(qty),
                    "paid": entry_paid,
                }
                seen_entries[entry.id] = info
                invoices.append(info)

        left = needed - ordered
        if left < 0:
            left = Decimal("0")
        to_pay = ordered - paid
        if to_pay < 0:
            to_pay = Decimal("0")

        totals_positions += 1
        if needed > 0 and left == 0:
            totals_fully_ordered += 1
        if needed > 0 and paid >= needed:
            totals_fully_paid += 1
        totals_needed += needed
        totals_ordered += ordered
        totals_paid += paid

        result_items.append({
            "id": item.id,
            "name": item.name,
            "type_mark": item.type_mark,
            "unit": item.unit,
            "needed": str(needed),
            "ordered": str(ordered),
            "paid": str(paid),
            "left": str(left),
            "to_pay": str(to_pay),
            "status": item.status,
            "invoices": invoices,
        })

    unmatched = db.execute(
        select(InvoiceLine.id).where(
            InvoiceLine.project_id == project_id,
            InvoiceLine.match_status != "confirmed",
        )
    ).all()

    return {
        "items": result_items,
        "totals": {
            "positions": totals_positions,
            "fully_ordered": totals_fully_ordered,
            "fully_paid": totals_fully_paid,
            "needed_sum": str(totals_needed),
            "ordered_sum": str(totals_ordered),
            "paid_sum": str(totals_paid),
        },
        "unmatched_lines": len(unmatched),
    }


_EXCLUDED_STATUSES = {"cancelled", "deleted", "rejected"}


def invoices_for_matching(db: Session, project_id: int) -> list[dict]:
    entries = db.execute(
        select(CashFlowEntry).where(
            CashFlowEntry.project_id == project_id,
            CashFlowEntry.direction == "outflow",
            CashFlowEntry.source_document_id.is_not(None),
        )
    ).scalars().all()

    entries = [e for e in entries if e.status not in _EXCLUDED_STATUSES]
    entries.sort(
        key=lambda e: (e.planned_date is None, e.planned_date, e.id),
    )
    entries.reverse()

    result: list[dict] = []
    for entry in entries:
        lines = db.execute(
            select(InvoiceLine).where(
                InvoiceLine.project_id == project_id,
                InvoiceLine.cash_flow_entry_id == entry.id,
            )
        ).scalars().all()
        total = len(lines)
        confirmed = sum(1 for ln in lines if ln.match_status == "confirmed")
        suggested = sum(1 for ln in lines if ln.match_status == "suggested")
        amount = entry.invoiced_amount
        if amount is None:
            amount = entry.planned_amount
        result.append({
            "entry_id": entry.id,
            "title": entry.title,
            "counterparty": entry.counterparty,
            "planned_date": entry.planned_date.isoformat() if entry.planned_date else None,
            "amount": str(_to_decimal(amount)),
            "paid": _is_paid(entry),
            "lines": total,
            "confirmed": confirmed,
            "suggested": suggested,
        })
    return result


_RECEIPT_UNIT_MAX = 40
_RECEIPT_QTY_SCALE = 3
_RECEIPT_QTY_THOUSANDTHS = 1000


def _validate_receipt_unit(unit) -> str:
    if unit is None:
        raise ValueError("receipt unit is null")
    if not isinstance(unit, str):
        raise ValueError("receipt unit is not a string")
    if unit == "" or unit.strip() == "":
        raise ValueError("receipt unit is blank")
    if unit != unit.strip():
        raise ValueError("receipt unit is not canonical")
    if len(unit) > _RECEIPT_UNIT_MAX:
        raise ValueError("receipt unit exceeds maximum length")
    return unit


def _receipt_qty_thousandths(value) -> int:
    if value is None:
        raise ValueError("receipt qty is null")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int) and not isinstance(value, bool):
        dec = Decimal(value)
    elif isinstance(value, str):
        try:
            dec = Decimal(value)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("receipt qty is not a decimal") from exc
    else:
        raise ValueError("receipt qty has unsupported type")
    if not dec.is_finite():
        raise ValueError("receipt qty is not finite")
    if dec <= 0:
        raise ValueError("receipt qty is not positive")
    scaled = dec * _RECEIPT_QTY_THOUSANDTHS
    if scaled != scaled.to_integral_value():
        raise ValueError("receipt qty exceeds scale 3")
    return int(scaled)


def _format_thousandths(total: int) -> str:
    sign = "-" if total < 0 else ""
    magnitude = abs(total)
    whole, frac = divmod(magnitude, _RECEIPT_QTY_THOUSANDTHS)
    return f"{sign}{whole}.{frac:03d}"


def received_totals(db: Session, project_id: int, *, row_budget: int) -> dict:
    """Read-only explicit physical arrival totals for a project.

    Counts only explicit MaterialReceipt rows. Does not read ordered/paid/
    invoice sums, available stock or balances, and performs no writes.
    """
    if not isinstance(project_id, int) or isinstance(project_id, bool):
        raise ValueError("project_id must be an int")
    if project_id <= 0:
        raise ValueError("project_id must be positive")
    if not isinstance(row_budget, int) or isinstance(row_budget, bool):
        raise ValueError("row_budget must be an int")
    if row_budget < 1 or row_budget > 10000:
        raise ValueError("row_budget out of range")

    with db.no_autoflush:
        project_exists = db.execute(
            select(func.count()).select_from(PurchaseItem).where(
                PurchaseItem.project_id == project_id
            )
        ).scalar_one()
        if not project_exists:
            raise LookupError("unknown project")

        stmt = (
            select(
                MaterialReceipt.id,
                MaterialReceipt.purchase_item_id,
                MaterialReceipt.qty,
                func.substr(MaterialReceipt.unit, 1, _RECEIPT_UNIT_MAX + 1),
                PurchaseItem.project_id,
            )
            .select_from(MaterialReceipt)
            .outerjoin(PurchaseItem, PurchaseItem.id == MaterialReceipt.purchase_item_id)
            .where(MaterialReceipt.project_id == project_id)
            .order_by(MaterialReceipt.id)
            .limit(row_budget + 1)
        )

        result = db.execute(stmt)
        try:
            rows = result.all()
        finally:
            result.close()

    if len(rows) > row_budget:
        raise ValueError("receipt row budget exceeded")

    groups: dict[tuple[int, str], dict] = {}
    receipt_count = 0

    for _rid, purchase_item_id, qty, unit, item_project_id in rows:
        if purchase_item_id is None:
            raise ValueError("receipt references missing purchase item")
        if item_project_id is None or item_project_id != project_id:
            raise ValueError("receipt references foreign purchase item")
        unit_value = _validate_receipt_unit(unit)
        thousandths = _receipt_qty_thousandths(qty)
        key = (int(purchase_item_id), unit_value)
        bucket = groups.get(key)
        if bucket is None:
            bucket = {"thousandths": 0, "receipt_count": 0}
            groups[key] = bucket
        bucket["thousandths"] += thousandths
        bucket["receipt_count"] += 1
        receipt_count += 1

    materials = [
        {
            "purchase_item_id": item_id,
            "unit": unit_value,
            "qty_received": _format_thousandths(bucket["thousandths"]),
            "receipt_count": bucket["receipt_count"],
        }
        for (item_id, unit_value), bucket in sorted(groups.items())
    ]

    return {
        "project_id": project_id,
        "receipt_count": receipt_count,
        "materials": materials,
    }
