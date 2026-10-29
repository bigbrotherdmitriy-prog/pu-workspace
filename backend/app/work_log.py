"""Общий журнал работ: факт выполнения, который прораб вносит с телефона."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rd import RdSpecItem
from app.models.work_log import WorkLogCrew, WorkLogEntry, WorkLogLabor

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
    if "hours" in data:
        raw = data.get("hours")
        if raw is None or str(raw).strip() == "":
            result["hours"] = None
        else:
            try:
                hours = Decimal(str(raw).strip().replace(",", "."))
            except InvalidOperation as exc:
                raise ValueError("Некорректное число часов") from exc
            if not Decimal("0") <= hours <= Decimal("24"):
                raise ValueError("Часов за день может быть от 0 до 24")
            result["hours"] = hours
    for field in _TEXT_FIELDS:
        if field in data:
            value = data.get(field)
            result[field] = (str(value).strip() or None) if value is not None else None
    return result


def _crew_size(crew: WorkLogCrew) -> int:
    lines = [ln for ln in (crew.members or "").splitlines() if ln.strip()]
    return len(lines) + (1 if (crew.foreman or "").strip() else 0)


def _check_links(db: Session, project_id: int, data: dict) -> dict:
    result: dict = {}
    if data.get("crew_id") is not None:
        crew = db.get(WorkLogCrew, int(data["crew_id"]))
        if crew is None or crew.project_id != project_id:
            raise LookupError("Бригада не найдена")
        result["crew_id"] = crew.id
        if data.get("workers") in (None, ""):
            size = _crew_size(crew)
            if size:
                result["workers"] = size
    if data.get("rd_spec_item_id") is not None:
        item = db.get(RdSpecItem, int(data["rd_spec_item_id"]))
        if item is None or item.project_id != project_id:
            raise LookupError("Позиция РД не найдена")
        result["rd_spec_item_id"] = item.id
    if data.get("estimate_line_id") is not None:
        from app.models.estimates import EstimateLine

        line = db.get(EstimateLine, int(data["estimate_line_id"]))
        if line is None or line.project_id != project_id:
            raise LookupError("Позиция сметы не найдена")
        result["estimate_line_id"] = line.id
    if data.get("schedule_item_id") is not None:
        from app.models.execution_finance import ScheduleItem

        sched = db.get(ScheduleItem, int(data["schedule_item_id"]))
        if sched is None or getattr(sched, "project_id", project_id) != project_id:
            raise LookupError("Этап ГПР не найден")
        result["schedule_item_id"] = sched.id
    return result


def _parse_labor(raw) -> list[dict]:
    rows: list[dict] = []
    for item in raw or []:
        name = (item.get("person_name") or "").strip() if isinstance(item, dict) else ""
        if not name:
            continue
        try:
            hours = Decimal(str(item.get("hours")).strip().replace(",", "."))
        except (InvalidOperation, AttributeError) as exc:
            raise ValueError(f"Некорректные часы у {name}") from exc
        if not Decimal("0") <= hours <= Decimal("24"):
            raise ValueError(f"Часов у {name} может быть от 0 до 24")
        rate_raw = item.get("hourly_rate")
        rate = None
        if rate_raw not in (None, ""):
            try:
                rate = Decimal(str(rate_raw).strip().replace(",", ".").replace(" ", ""))
            except InvalidOperation as exc:
                raise ValueError(f"Некорректная ставка у {name}") from exc
            if rate < 0:
                raise ValueError("Ставка не может быть отрицательной")
        rows.append({"person_name": name[:300], "hours": hours, "hourly_rate": rate})
    return rows


def _labor_of(db: Session, entry_id: int) -> list[dict]:
    rows = db.execute(
        select(WorkLogLabor).where(WorkLogLabor.entry_id == entry_id).order_by(WorkLogLabor.id)
    ).scalars().all()
    return [
        {
            "person_name": r.person_name,
            "hours": f"{Decimal(r.hours).quantize(Decimal('0.01')):f}",
            "hourly_rate": f"{Decimal(r.hourly_rate).quantize(Decimal('0.01')):f}" if r.hourly_rate is not None else None,
        }
        for r in rows
    ]


def _replace_labor(db: Session, project_id: int, entry: WorkLogEntry, rows: list[dict]) -> None:
    for old in db.execute(select(WorkLogLabor).where(WorkLogLabor.entry_id == entry.id)).scalars().all():
        db.delete(old)
    db.flush()
    for row in rows:
        db.add(WorkLogLabor(project_id=project_id, entry_id=entry.id, **row))
    if rows and entry.workers is None:
        entry.workers = len(rows)
    db.flush()


def _to_dict(e: WorkLogEntry, labor: list[dict] | None = None) -> dict:
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
        "hours": f"{Decimal(e.hours).quantize(Decimal('0.01')):f}" if e.hours is not None else None,
        "crew_id": e.crew_id,
        "note": e.note,
        "rd_spec_item_id": e.rd_spec_item_id,
        "schedule_item_id": e.schedule_item_id,
        "estimate_line_id": e.estimate_line_id,
        "author_user_id": e.author_user_id,
        "labor": labor if labor is not None else [],
    }


def add_entry(db: Session, project_id: int, user_id: int | None, data: dict) -> dict:
    values = _clean(data, partial=False)
    values.update(_check_links(db, project_id, data))
    labor = _parse_labor(data.get("labor"))
    entry = WorkLogEntry(project_id=project_id, author_user_id=user_id, **values)
    db.add(entry)
    db.flush()
    _replace_labor(db, project_id, entry, labor)
    return _to_dict(entry, _labor_of(db, entry.id))


def _get(db: Session, project_id: int, entry_id: int) -> WorkLogEntry:
    entry = db.get(WorkLogEntry, entry_id)
    if entry is None or entry.project_id != project_id:
        raise LookupError("Запись не найдена")
    return entry


def update_entry(db: Session, project_id: int, entry_id: int, data: dict) -> dict:
    entry = _get(db, project_id, entry_id)
    values = _clean(data, partial=True)
    values.update(_check_links(db, project_id, data))
    labor = _parse_labor(data.get("labor")) if "labor" in data else None
    for key, value in values.items():
        setattr(entry, key, value)
    db.flush()
    if labor is not None:
        _replace_labor(db, project_id, entry, labor)
    return _to_dict(entry, _labor_of(db, entry.id))


def delete_entry(db: Session, project_id: int, entry_id: int) -> None:
    entry = _get(db, project_id, entry_id)
    for lab in db.execute(select(WorkLogLabor).where(WorkLogLabor.entry_id == entry.id)).scalars().all():
        db.delete(lab)
    db.delete(entry)
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
        result.append(_to_dict(e, _labor_of(db, e.id)))
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
    labor_by_entry: dict[int, Decimal] = {}
    for lab in db.execute(select(WorkLogLabor).where(WorkLogLabor.project_id == project_id)).scalars().all():
        labor_by_entry[lab.entry_id] = labor_by_entry.get(lab.entry_id, Decimal("0")) + lab.hours
    hours_total = sum(
        labor_by_entry[e.id] if e.id in labor_by_entry else (e.hours or Decimal("0")) * (e.workers or 1)
        for e in rows
    )
    return {
        "entries": len(rows),
        "days": len({e.work_date for e in rows}),
        "man_hours": f"{Decimal(hours_total).quantize(Decimal('0.01')):f}",
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
    from app.models.estimates import Estimate, EstimateLine

    est_lines = db.execute(
        select(EstimateLine).join(Estimate, Estimate.id == EstimateLine.estimate_id)
        .where(EstimateLine.project_id == project_id, Estimate.status == "active")
        .order_by(EstimateLine.estimate_id, EstimateLine.sort_order, EstimateLine.id).limit(500)
    ).scalars().all()
    return {
        "works": works,
        "objects": objects,
        "rd_items": [{"id": i.id, "name": i.name, "unit": i.unit} for i in items],
        "estimate_lines": [
            {"id": l.id, "position": l.position, "code": l.code, "name": l.name, "unit": l.unit,
             "qty": f"{l.qty:f}" if l.qty is not None else None}
            for l in est_lines
        ],
    }


def to_xlsx(db: Session, project_id: int, date_from=None, date_to=None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Журнал работ"
    crews = {c.id: c.name for c in db.execute(select(WorkLogCrew).where(WorkLogCrew.project_id == project_id)).scalars().all()}
    ws.append(["Дата", "Объект", "Наименование работ", "Ед. изм.", "Объём", "Место выполнения", "Погода", "Бригада", "Рабочих", "Часов", "Примечание"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for e in reversed(list_entries(db, project_id, date_from, date_to, limit=100000)):
        d = date.fromisoformat(e["work_date"])
        ws.append([
            d.strftime("%d.%m.%Y"), e["object_name"] or "", e["work_name"], e["unit"] or "",
            float(e["qty"]) if e["qty"] is not None else None,
            e["location"] or "", e["weather"] or "", crews.get(e["crew_id"], ""), e["workers"],
            float(e["hours"]) if e["hours"] is not None else None, e["note"] or "",
        ])
    for col, width in zip("ABCDEFGHIJK", (12, 20, 50, 10, 12, 28, 16, 22, 10, 8, 40)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    sheet = wb.create_sheet("Табель")
    sheet.append(["Дата", "Работник", "Часов", "Ставка, ₽/ч", "Работа"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for e in reversed(list_entries(db, project_id, date_from, date_to, limit=100000)):
        d = date.fromisoformat(e["work_date"])
        for lab in e["labor"]:
            sheet.append([
                d.strftime("%d.%m.%Y"), lab["person_name"], float(lab["hours"]),
                float(lab["hourly_rate"]) if lab["hourly_rate"] is not None else None, e["work_name"],
            ])
    for col, width in zip("ABCDE", (12, 32, 10, 14, 50)):
        sheet.column_dimensions[col].width = width
    sheet.freeze_panes = "A2"
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


def _crew_dict(c: WorkLogCrew) -> dict:
    return {
        "id": c.id, "name": c.name, "foreman": c.foreman, "members": c.members or "",
        "members_count": _crew_size(c), "note": c.note,
    }


def list_crews(db: Session, project_id: int) -> list[dict]:
    rows = db.execute(
        select(WorkLogCrew).where(WorkLogCrew.project_id == project_id).order_by(WorkLogCrew.name, WorkLogCrew.id)
    ).scalars().all()
    return [_crew_dict(c) for c in rows]


def _crew_values(data: dict, *, partial: bool) -> dict:
    result: dict = {}
    if "name" in data or not partial:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("Укажите название бригады")
        result["name"] = name[:300]
    for field in ("foreman", "members", "note"):
        if field in data:
            value = data.get(field)
            result[field] = (str(value).strip() or None) if value is not None else None
    return result


def add_crew(db: Session, project_id: int, data: dict) -> dict:
    crew = WorkLogCrew(project_id=project_id, **_crew_values(data, partial=False))
    db.add(crew)
    db.flush()
    return _crew_dict(crew)


def _get_crew(db: Session, project_id: int, crew_id: int) -> WorkLogCrew:
    crew = db.get(WorkLogCrew, crew_id)
    if crew is None or crew.project_id != project_id:
        raise LookupError("Бригада не найдена")
    return crew


def update_crew(db: Session, project_id: int, crew_id: int, data: dict) -> dict:
    crew = _get_crew(db, project_id, crew_id)
    for key, value in _crew_values(data, partial=True).items():
        setattr(crew, key, value)
    db.flush()
    return _crew_dict(crew)


def delete_crew(db: Session, project_id: int, crew_id: int) -> None:
    crew = _get_crew(db, project_id, crew_id)
    for entry in db.execute(select(WorkLogEntry).where(WorkLogEntry.crew_id == crew.id)).scalars().all():
        entry.crew_id = None
    db.delete(crew)
    db.flush()
