from __future__ import annotations

import re
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_version import DocumentVersion
from app.models.execution_finance import CashFlowEntry
from app.models.invoice_line import InvoiceLine
from app.models.purchase import PurchaseItem
from app.specification import specification_from_text

_STOP_WORDS = {"для", "и", "с", "по", "на", "шт", "комплект"}
_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+(?:-[0-9a-zа-яё]+)*", re.IGNORECASE)


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    result: set[str] = set()
    for match in _TOKEN_RE.finditer(str(text).lower()):
        token = match.group(0)
        if len(token) < 2:
            continue
        if token in _STOP_WORDS:
            continue
        result.add(token)
    return result


def _norm_unit(unit: str | None) -> str:
    if not unit:
        return ""
    value = str(unit).strip().lower().replace(".", "")
    return value


def similarity(a: str | None, b: str | None, unit_a: str | None = None,
               unit_b: str | None = None) -> float:
    tokens_a = _tokens(a)
    tokens_b = _tokens(b)
    union = tokens_a | tokens_b
    if not union:
        base = 0.0
    else:
        base = len(tokens_a & tokens_b) / len(union)
    ua = _norm_unit(unit_a)
    ub = _norm_unit(unit_b)
    if ua and ub and ua == ub:
        base = min(1.0, base + 0.1)
    return base


def _get_entry(db: Session, project_id: int, entry_id: int) -> CashFlowEntry:
    entry = db.execute(
        select(CashFlowEntry).where(
            CashFlowEntry.id == entry_id,
            CashFlowEntry.project_id == project_id,
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return entry


def _document_text(db: Session, entry: CashFlowEntry) -> str:
    if entry.source_document_id is None:
        raise HTTPException(status_code=422, detail="У счёта нет исходного документа")
    version = db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == entry.source_document_id)
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    if version is None or not version.content:
        raise HTTPException(status_code=422, detail="У счёта нет исходного документа")
    return version.content


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


def extract_lines(db: Session, project_id: int, entry_id: int) -> dict:
    entry = _get_entry(db, project_id, entry_id)
    text = _document_text(db, entry)
    items, _ = specification_from_text(text)

    existing = db.execute(
        select(InvoiceLine).where(
            InvoiceLine.project_id == project_id,
            InvoiceLine.cash_flow_entry_id == entry_id,
        )
    ).scalars().all()
    confirmed_names: set[str] = set()
    for line in existing:
        if line.match_status == "confirmed":
            confirmed_names.add(line.name)
        else:
            db.delete(line)
    db.flush()

    created = 0
    for idx, item in enumerate(items or []):
        name = (item or {}).get("name")
        if not name:
            continue
        if name in confirmed_names:
            continue
        db.add(InvoiceLine(
            project_id=project_id,
            cash_flow_entry_id=entry_id,
            sort_order=idx,
            name=name,
            unit=item.get("unit"),
            qty=_to_decimal(item.get("qty")),
            match_status="none",
        ))
        created += 1
    db.flush()

    suggested = suggest_matches(db, project_id, entry_id)
    db.commit()
    return {"lines": created, "suggested": suggested}


def suggest_matches(db: Session, project_id: int, entry_id: int) -> int:
    lines = db.execute(
        select(InvoiceLine).where(
            InvoiceLine.project_id == project_id,
            InvoiceLine.cash_flow_entry_id == entry_id,
            InvoiceLine.match_status.in_(("none", "suggested")),
        )
    ).scalars().all()
    if not lines:
        return 0

    items = db.execute(
        select(PurchaseItem).where(PurchaseItem.project_id == project_id)
    ).scalars().all()

    suggested = 0
    for line in lines:
        best_item: PurchaseItem | None = None
        best_score = 0.0
        for item in items:
            score = similarity(line.name, item.name, line.unit, item.unit)
            if score > best_score:
                best_score = score
                best_item = item
        if best_item is not None and best_score >= 0.5:
            line.purchase_item_id = best_item.id
            line.match_status = "suggested"
            line.match_score = Decimal(str(round(best_score * 100, 2)))
            suggested += 1
        else:
            line.purchase_item_id = None
            line.match_status = "none"
            line.match_score = None
    db.flush()
    return suggested


def recompute_ordered(db: Session, project_id: int, purchase_item_id: int | None) -> None:
    if purchase_item_id is None:
        return
    item = db.execute(
        select(PurchaseItem).where(
            PurchaseItem.id == purchase_item_id,
            PurchaseItem.project_id == project_id,
        )
    ).scalar_one_or_none()
    if item is None:
        return
    lines = db.execute(
        select(InvoiceLine.qty).where(
            InvoiceLine.project_id == project_id,
            InvoiceLine.purchase_item_id == purchase_item_id,
            InvoiceLine.match_status == "confirmed",
        )
    ).all()
    total = Decimal("0")
    for (qty,) in lines:
        value = _to_decimal(qty)
        if value is not None:
            total += value
    item.qty_ordered = total
    if item.status == "need" and total > 0:
        item.status = "ordered"
    elif item.status == "ordered" and total == 0:
        item.status = "need"
    db.flush()


def set_match(
    db: Session,
    project_id: int,
    line_id: int,
    purchase_item_id: int | None,
) -> dict:
    line = db.execute(
        select(InvoiceLine).where(
            InvoiceLine.id == line_id,
            InvoiceLine.project_id == project_id,
        )
    ).scalar_one_or_none()
    if line is None:
        raise HTTPException(status_code=404, detail="line not found")

    old_item_id = line.purchase_item_id

    if purchase_item_id is None:
        line.purchase_item_id = None
        line.match_status = "none"
    else:
        item = db.execute(
            select(PurchaseItem).where(
                PurchaseItem.id == purchase_item_id,
                PurchaseItem.project_id == project_id,
            )
        ).scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        line.purchase_item_id = purchase_item_id
        line.match_status = "confirmed"

    db.flush()
    recompute_ordered(db, project_id, old_item_id)
    recompute_ordered(db, project_id, purchase_item_id)
    db.commit()

    return {
        "id": line.id,
        "cash_flow_entry_id": line.cash_flow_entry_id,
        "name": line.name,
        "unit": line.unit,
        "qty": str(line.qty) if line.qty is not None else None,
        "match_status": line.match_status,
        "match_score": str(line.match_score) if line.match_score is not None else None,
        "purchase_item_id": line.purchase_item_id,
    }


def list_lines(
    db: Session,
    project_id: int,
    entry_id: int | None = None,
) -> list[dict]:
    stmt = (
        select(InvoiceLine, CashFlowEntry)
        .join(CashFlowEntry, CashFlowEntry.id == InvoiceLine.cash_flow_entry_id)
        .where(InvoiceLine.project_id == project_id)
    )
    if entry_id is not None:
        stmt = stmt.where(InvoiceLine.cash_flow_entry_id == entry_id)
    stmt = stmt.order_by(InvoiceLine.cash_flow_entry_id, InvoiceLine.sort_order, InvoiceLine.id)
    rows = db.execute(stmt).all()

    purchase_names: dict[int, str] = {}
    purchase_ids = {line.purchase_item_id for line, _ in rows if line.purchase_item_id}
    if purchase_ids:
        items = db.execute(
            select(PurchaseItem.id, PurchaseItem.name).where(
                PurchaseItem.id.in_(purchase_ids)
            )
        ).all()
        purchase_names = {pid: pname for pid, pname in items}

    result: list[dict] = []
    for line, entry in rows:
        result.append({
            "id": line.id,
            "cash_flow_entry_id": line.cash_flow_entry_id,
            "invoice_title": entry.title,
            "counterparty": entry.counterparty,
            "name": line.name,
            "unit": line.unit,
            "qty": str(line.qty) if line.qty is not None else None,
            "match_status": line.match_status,
            "match_score": str(line.match_score) if line.match_score is not None else None,
            "purchase_item_id": line.purchase_item_id,
            "purchase_item_name": purchase_names.get(line.purchase_item_id)
            if line.purchase_item_id else None,
        })
    return result
