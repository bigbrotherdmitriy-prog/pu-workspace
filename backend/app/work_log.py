"""Общий журнал работ: факт выполнения, который прораб вносит с телефона."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rd import RdSpecItem
from app.models.work_log import WorkLogEntry

_TEXT_FIELDS = ("object_name", "unit", "location", "weather", "note")
_MAX_NAME = 1000


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректная дата") from exc


def _parse_qty(value) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        qty = Decimal(str(value).strip().replace(",", ".").replace(" ", ""))
    except InvalidOperation as exc:
        raise ValueError("Некорректный объём") from exc
    if qty < 0:
        raise ValueError("Объём не может быть отрицательным")
    return qty


def _clean(data: dict, *, partial: bool) -> dict:
    result: dict = {}
    if "work_date" in data or not partial:
        work_date = _parse_date(data.get("work_date"))
        if work_date > date.today() + timedelta(days=1):
            raise ValueError("Дата в будущем: журнал ведётся по факту")
        result["work_date"] = work_date
    if "work_name" in data or not partial:
        name = (data.get("work_name") or "").strip()
        if not name:
            raise ValueError("Укажите, какая работа выполнена")
        if len(name) > _MAX_NAME:
            raise ValueError("Название работы слишком длинное")
        result["work_name"] = name
    if "qty" in data:
        result["qty"] = _parse_qty(data.get("qty"))
    if "workers" in data:
        raw = data.get("workers")
        if raw is None or str(raw).strip() == "":
            result["workers"] = None
        else:
            try:
                workers = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("Число рабочих должно быть целым") from exc
            if not 0 <= workers <= 10000:
                raise ValueError("Некорректное число рабочих")
            result["workers"] = workers
    for field in _TEXT_FIELDS:
        if field in data:
            value = data.get(field)
            result[field] = (str(value).strip() or None) if value is not None else None
    return result


def _check_links(db: Session, project_id: int, data: dict) -> dict:
    result: dict = {}
    if data.get("rd_spec_item_id") is not None:
        item = db.get(RdSpecItem, int(data["rd_spec_item_id"]))
        if item is None or item.project_id != project_id:
            raise LookupError("Позиция РД не найдена")
        result["rd_spec_item_id"] = item.id
    if data.get("schedule_item_id") is not None:
        from app.models.execution_finance import ScheduleItem

        sched = db.get(ScheduleItem, int(data["schedule_item_id"]))
        if sched is None or getattr(sched, "project_id", project_id) != project_id:
            raise LookupError("Этап ГПР не найден")
        result["schedule_item_id"] = sched.id
    return result


def _to_dict(e: WorkLogEntry) -> dict:
    return {
        "id": e.id,
        "project_id": e.project_id,
        "work_date": e.work_date.isoformat(),
        "object_name": e.object_name,
        "work_name": e.work_name,
        "unit": e.unit,
        "qty": f"{Decimal(e.qty).quantize(Decimal('0.001')):f}" if e.qty is not None else None,
        "location": e.location,
        "weather": e.weather,
        "workers": e.workers,
        "note": e.note,
        "rd_spec_item_id": e.rd_spec_item_id,
        "schedule_item_id": e.schedule_item_id,
        "author_user_id": e.author_user_id,
    }


def add_entry(db: Session, project_id: int, user_id: int | None, data: dict) -> dict:
    values = _clean(data, partial=False)
    values.update(_check_links(db, project_id, data))
    entry = WorkLogEntry(project_id=project_id, author_user_id=user_id, **values)
    db.add(entry)
    db.flush()
    return _to_dict(entry)


def _get(db: Session, project_id: int, entry_id: int) -> WorkLogEntry:
    entry = db.get(WorkLogEntry, entry_id)
    if entry is None or entry.project_id != project_id:
        raise LookupError("Запись не найдена")
    return entry


def update_entry(db: Session, project_id: int, entry_id: int, data: dict) -> dict:
    entry = _get(db, project_id, entry_id)
    values = _clean(data, partial=True)
    values.update(_check_links(db, project_id, data))
    for key, value in values.items():
        setattr(entry, key, value)
    db.flush()
    return _to_dict(entry)


def delete_entry(db: Session, project_id: int, entry_id: int) -> None:
    db.delete(_get(db, project_id, entry_id))
    db.flush()


def _filtered(project_id, date_from, date_to, q, object_name):
    stmt = select(WorkLogEntry).where(WorkLogEntry.project_id == project_id)
    if date_from:
        stmt = stmt.where(WorkLogEntry.work_date >= _parse_date(date_from))
    if date_to:
        stmt = stmt.where(WorkLogEntry.work_date <= _parse_date(date_to))
    if object_name:
        stmt = stmt.where(WorkLogEntry.object_name == object_name)
    return stmt.order_by(WorkLogEntry.work_date.desc(), WorkLogEntry.id.desc()), (q or "").strip().lower()


def list_entries(
    db: Session, project_id: int, date_from=None, date_to=None, q=None, object_name=None, limit: int = 500,
) -> list[dict]:
    stmt, needle = _filtered(project_id, date_from, date_to, q, object_name)
    rows = db.execute(stmt).scalars().all()
    result = []
    for e in rows:
        if needle and needle not in " ".join(filter(None, [e.work_name, e.location, e.note])).lower():
            continue
        result.append(_to_dict(e))
        if len(result) >= limit:
            break
    return result


def summary(db: Session, project_id: int, date_from=None, date_to=None) -> dict:
    stmt, _ = _filtered(project_id, date_from, date_to, None, None)
    rows = db.execute(stmt).scalars().all()
    by_work: dict[tuple, dict] = {}
    for e in rows:
        key = (e.work_name, e.unit)
        bucket = by_work.setdefault(key, {"work_name": e.work_name, "unit": e.unit, "qty": Decimal("0"), "entries": 0})
        bucket["qty"] += e.qty or Decimal("0")
        bucket["entries"] += 1
    ordered = sorted(by_work.values(), key=lambda b: b["qty"], reverse=True)[:50]
    return {
        "entries": len(rows),
        "days": len({e.work_date for e in rows}),
        "by_work": [
            {"work_name": b["work_name"], "unit": b["unit"], "qty_total": f"{b['qty'].quantize(Decimal('0.001')):f}", "entries": b["entries"]}
            for b in ordered
        ],
    }


def suggestions(db: Session, project_id: int) -> dict:
    rows = db.execute(
        select(WorkLogEntry).where(WorkLogEntry.project_id == project_id)
        .order_by(WorkLogEntry.id.desc()).limit(300)
    ).scalars().all()
    works: list[dict] = []
    seen: set[str] = set()
    objects: list[str] = []
    for e in rows:
        if e.work_name not in seen and len(works) < 30:
            seen.add(e.work_name)
            works.append({"work_name": e.work_name, "unit": e.unit})
        if e.object_name and e.object_name not in objects:
            objects.append(e.object_name)
    items = db.execute(
        select(RdSpecItem).where(RdSpecItem.project_id == project_id, RdSpecItem.status == "confirmed")
        .order_by(RdSpecItem.id).limit(100)
    ).scalars().all()
    return {
        "works": works,
        "objects": objects,
        "rd_items": [{"id": i.id, "name": i.name, "unit": i.unit} for i in items],
    }


def to_xlsx(db: Session, project_id: int, date_from=None, date_to=None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Журнал работ"
    ws.append(["Дата", "Объект", "Наименование работ", "Ед. изм.", "Объём", "Место выполнения", "Погода", "Рабочих", "Примечание"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for e in reversed(list_entries(db, project_id, date_from, date_to, limit=100000)):
        d = date.fromisoformat(e["work_date"])
        ws.append([
            d.strftime("%d.%m.%Y"), e["object_name"] or "", e["work_name"], e["unit"] or "",
            float(e["qty"]) if e["qty"] is not None else None,
            e["location"] or "", e["weather"] or "", e["workers"], e["note"] or "",
        ])
    for col, width in zip("ABCDEFGHI", (12, 20, 50, 10, 12, 28, 16, 10, 40)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def schedule_overview(db: Session, project_id: int) -> dict:
    """Работы из ГПР (самая свежая версия) с плановыми сроками и фактом из журнала."""
    from app.models.execution_finance import ScheduleBaseline, ScheduleItem

    baselines = db.execute(
        select(ScheduleBaseline).where(ScheduleBaseline.project_id == project_id)
        .order_by(ScheduleBaseline.version.desc())
    ).scalars().all()
    chosen = next((b for b in baselines if b.status == "approved"), baselines[0] if baselines else None)
    if chosen is None:
        return {"baseline": None, "items": []}
    items = db.execute(
        select(ScheduleItem).where(ScheduleItem.project_id == project_id, ScheduleItem.baseline_id == chosen.id)
        .order_by(ScheduleItem.sort_order, ScheduleItem.id)
    ).scalars().all()
    parents = {i.parent_id for i in items if i.parent_id}
    entries = db.execute(
        select(WorkLogEntry).where(WorkLogEntry.project_id == project_id, WorkLogEntry.schedule_item_id.is_not(None))
    ).scalars().all()
    fact: dict[int, dict] = {}
    for e in entries:
        bucket = fact.setdefault(e.schedule_item_id, {"qty": Decimal("0"), "entries": 0, "first": e.work_date, "last": e.work_date, "unit": e.unit})
        bucket["qty"] += e.qty or Decimal("0")
        bucket["entries"] += 1
        bucket["first"] = min(bucket["first"], e.work_date)
        bucket["last"] = max(bucket["last"], e.work_date)
        bucket["unit"] = bucket["unit"] or e.unit
    today = date.today()
    result = []
    for i in items:
        f = fact.get(i.id)
        late = bool(i.planned_finish and i.planned_finish < today and (i.actual_progress or 0) < 100 and not i.actual_finish)
        result.append({
            "id": i.id,
            "title": i.title,
            "is_group": i.id in parents,
            "is_milestone": bool(i.is_milestone),
            "planned_start": i.planned_start.isoformat() if i.planned_start else None,
            "planned_finish": i.planned_finish.isoformat() if i.planned_finish else None,
            "planned_progress": i.planned_progress or 0,
            "actual_progress": i.actual_progress or 0,
            "overdue": late,
            "fact_entries": f["entries"] if f else 0,
            "fact_qty": f"{f['qty'].quantize(Decimal('0.001')):f}" if f else None,
            "fact_unit": f["unit"] if f else None,
            "fact_first": f["first"].isoformat() if f else None,
            "fact_last": f["last"].isoformat() if f else None,
        })
    return {"baseline": {"id": chosen.id, "name": chosen.name, "version": chosen.version, "status": chosen.status}, "items": result}
