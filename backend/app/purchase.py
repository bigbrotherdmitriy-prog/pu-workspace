from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


# ---------------------------------------------------------------------------
# Explicit physical material receipt service (inert review candidate).
#
# Concurrency limits: the SELECT ... FOR UPDATE row lock is meaningful only on
# PostgreSQL (and similar engines). SQLite ignores it, so synthetic SQLite
# tests do not prove production concurrency safety. The unique constraint on
# (project_id, operation_key) plus the savepoint recovery path is the real
# backstop for concurrent replays.
# ---------------------------------------------------------------------------

_QTY_MAX = Decimal("999999999999999.999")
_QTY_STR_MAX = 64
_OP_KEY_MAX = 160
_UNIT_MAX = 40
_DOCREF_MAX = 500


def _positive_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=422, detail=f"invalid {field}")
    if value <= 0:
        raise HTTPException(status_code=422, detail=f"invalid {field}")
    return value


def _clean_str(value, field: str, max_len: int, *, optional: bool = False):
    if value is None:
        if optional:
            return None
        raise HTTPException(status_code=422, detail=f"invalid {field}")
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"invalid {field}")
    trimmed = value.strip()
    if not trimmed:
        if optional:
            return None
        raise HTTPException(status_code=422, detail=f"invalid {field}")
    if len(trimmed) > max_len:
        raise HTTPException(status_code=422, detail=f"invalid {field}")
    return trimmed


def _parse_qty(value) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise HTTPException(status_code=422, detail="invalid qty")
    if isinstance(value, Decimal):
        qty = value
    elif isinstance(value, int):
        qty = Decimal(value)
    elif isinstance(value, str):
        if len(value) > _QTY_STR_MAX:
            raise HTTPException(status_code=422, detail="invalid qty")
        try:
            qty = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            raise HTTPException(status_code=422, detail="invalid qty")
    else:
        raise HTTPException(status_code=422, detail="invalid qty")

    if not qty.is_finite():
        raise HTTPException(status_code=422, detail="invalid qty")
    # Bound exponent/size before any arithmetic to avoid huge expansions.
    if qty.adjusted() > 15:
        raise HTTPException(status_code=422, detail="invalid qty")
    if qty <= 0:
        raise HTTPException(status_code=422, detail="invalid qty")
    if qty > _QTY_MAX:
        raise HTTPException(status_code=422, detail="invalid qty")
    # At most three meaningful fractional digits, no rounding.
    if qty != qty.quantize(Decimal("0.001")):
        raise HTTPException(status_code=422, detail="invalid qty")
    return qty


def _receipt_to_dict(receipt: MaterialReceipt) -> dict:
    return {
        "id": receipt.id,
        "project_id": receipt.project_id,
        "purchase_item_id": receipt.purchase_item_id,
        "operation_key": receipt.operation_key,
        "qty": str(_to_decimal(receipt.qty)),
        "unit": receipt.unit,
        "source_document_ref": receipt.source_document_ref,
    }


def _find_receipt(db: Session, project_id: int, operation_key: str):
    return db.execute(
        select(MaterialReceipt).where(
            MaterialReceipt.project_id == project_id,
            MaterialReceipt.operation_key == operation_key,
        )
    ).scalar_one_or_none()


def _payload_matches(
    receipt: MaterialReceipt,
    item_id: int,
    qty: Decimal,
    unit: str,
    docref: str | None,
) -> bool:
    return (
        receipt.purchase_item_id == item_id
        and _to_decimal(receipt.qty) == qty
        and receipt.unit == unit
        and receipt.source_document_ref == docref
    )


def create_material_receipt(
    db: Session,
    project_id: int,
    item_id: int,
    *,
    operation_key,
    qty,
    unit,
    source_document_ref=None,
) -> dict:
    """Record an explicit physical material receipt (idempotent by key).

    Never infers receipts from ordered/paid/status/work quantities. Does not
    commit or roll back; the caller owns the outer transaction. Row locks are
    meaningful on PostgreSQL only; SQLite synthetic tests are not proof of
    production concurrency.
    """
    project_id = _positive_int(project_id, "project_id")
    item_id = _positive_int(item_id, "item_id")
    op_key = _clean_str(operation_key, "operation_key", _OP_KEY_MAX)
    unit_clean = _clean_str(unit, "unit", _UNIT_MAX)
    docref = _clean_str(
        source_document_ref, "source_document_ref", _DOCREF_MAX, optional=True,
    )
    qty_dec = _parse_qty(qty)

    item = db.execute(
        select(PurchaseItem)
        .where(
            PurchaseItem.id == item_id,
            PurchaseItem.project_id == project_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")

    item_unit = (item.unit or "").strip()
    if item_unit != unit_clean:
        raise HTTPException(status_code=422, detail="unit mismatch")

    with db.no_autoflush:
        existing = _find_receipt(db, project_id, op_key)
    if existing is not None:
        if _payload_matches(existing, item_id, qty_dec, unit_clean, docref):
            return _receipt_to_dict(existing)
        raise HTTPException(status_code=409, detail="operation_key conflict")

    receipt = MaterialReceipt(
        project_id=project_id,
        purchase_item_id=item_id,
        operation_key=op_key,
        qty=qty_dec,
        unit=unit_clean,
        source_document_ref=docref,
    )
    try:
        with db.begin_nested():
            db.add(receipt)
            db.flush()
    except IntegrityError:
        with db.no_autoflush:
            recovered = _find_receipt(db, project_id, op_key)
        if recovered is None:
            raise
        if _payload_matches(recovered, item_id, qty_dec, unit_clean, docref):
            return _receipt_to_dict(recovered)
        raise HTTPException(status_code=409, detail="operation_key conflict")

    return _receipt_to_dict(receipt)
