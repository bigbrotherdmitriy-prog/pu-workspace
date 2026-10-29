"""Замечания (дефекты) на объекте: описание, фото с телефона, статусы, история, Excel."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from io import BytesIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.defects import Defect, DefectComment, DefectPhoto

STATUSES = ("open", "in_progress", "fixed", "accepted")
STATUS_LABEL = {"open": "Открыто", "in_progress": "В работе", "fixed": "Устранено", "accepted": "Принято"}
PRIORITIES = ("low", "normal", "high")
PRIORITY_LABEL = {"low": "Низкий", "normal": "Обычный", "high": "Высокий"}
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTOS = 12
# Допустимые переходы статуса.
TRANSITIONS = {
    "open": {"in_progress", "fixed"},
    "in_progress": {"open", "fixed"},
    "fixed": {"accepted", "open"},
    "accepted": {"open"},
}


def _date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("Некорректная дата") from exc


def _txt(value, limit: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] or None


def _photo_dict(p: DefectPhoto) -> dict:
    return {"id": p.id, "kind": p.kind, "filename": p.filename, "mime_type": p.mime_type, "size_bytes": p.size_bytes}


def _defect_dict(db: Session, d: Defect, full: bool = False) -> dict:
    photos = db.execute(select(DefectPhoto).where(DefectPhoto.defect_id == d.id).order_by(DefectPhoto.id)).scalars().all()
    out = {
        "id": d.id, "number": d.number, "title": d.title, "description": d.description, "object_name": d.object_name,
        "location": d.location, "priority": d.priority, "status": d.status, "status_label": STATUS_LABEL.get(d.status, d.status),
        "responsible": d.responsible, "due_date": d.due_date.isoformat() if d.due_date else None,
        "overdue": bool(d.due_date and d.status in ("open", "in_progress") and d.due_date < date.today()),
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "closed_at": d.closed_at.isoformat() if d.closed_at else None,
        "photos": [_photo_dict(p) for p in photos],
    }
    if full:
        comments = db.execute(select(DefectComment).where(DefectComment.defect_id == d.id).order_by(DefectComment.id)).scalars().all()
        out["comments"] = [
            {"id": c.id, "text": c.text, "status_change": c.status_change, "created_at": c.created_at.isoformat() if c.created_at else None}
            for c in comments
        ]
    return out


def _next_number(db: Session, project_id: int) -> int:
    return int(db.scalar(select(func.coalesce(func.max(Defect.number), 0)).where(Defect.project_id == project_id)) or 0) + 1


def create_defect(db: Session, project_id: int, user_id: int | None, data: dict) -> dict:
    title = _txt(data.get("title"), 500)
    if not title:
        raise ValueError("Опишите замечание коротко: что не так")
    priority = data.get("priority") or "normal"
    if priority not in PRIORITIES:
        raise ValueError("Приоритет: low, normal или high")
    d = Defect(
        project_id=project_id, number=_next_number(db, project_id), title=title, description=_txt(data.get("description"), 5000),
        object_name=_txt(data.get("object_name"), 300), location=_txt(data.get("location"), 300), priority=priority,
        responsible=_txt(data.get("responsible"), 300), due_date=_date(data.get("due_date")), created_by=user_id,
    )
    db.add(d)
    db.flush()
    return _defect_dict(db, d, full=True)


def _get(db: Session, project_id: int, defect_id: int) -> Defect:
    d = db.get(Defect, defect_id)
    if d is None or d.project_id != project_id:
        raise LookupError("Замечание не найдено")
    return d


def get_defect(db: Session, project_id: int, defect_id: int) -> dict:
    return _defect_dict(db, _get(db, project_id, defect_id), full=True)


def list_defects(db: Session, project_id: int, status: str | None = None, q: str | None = None) -> dict:
    stmt = select(Defect).where(Defect.project_id == project_id)
    if status:
        stmt = stmt.where(Defect.status == status)
    rows = db.execute(stmt.order_by(Defect.number.desc())).scalars().all()
    needle = (q or "").strip().lower()
    items = []
    for d in rows:
        if needle and needle not in " ".join(filter(None, [d.title, d.description, d.location, d.object_name, d.responsible])).lower():
            continue
        items.append(_defect_dict(db, d))
    counts = {s: 0 for s in STATUSES}
    for s, n in db.execute(select(Defect.status, func.count()).where(Defect.project_id == project_id).group_by(Defect.status)).all():
        counts[s] = int(n)
    overdue = sum(1 for d in rows if d.due_date and d.status in ("open", "in_progress") and d.due_date < date.today())
    return {"defects": items, "counts": counts, "overdue": overdue}


def update_defect(db: Session, project_id: int, defect_id: int, data: dict) -> dict:
    d = _get(db, project_id, defect_id)
    if "title" in data:
        title = _txt(data["title"], 500)
        if not title:
            raise ValueError("Название замечания не может быть пустым")
        d.title = title
    for field, limit in (("description", 5000), ("object_name", 300), ("location", 300), ("responsible", 300)):
        if field in data:
            setattr(d, field, _txt(data[field], limit))
    if "priority" in data:
        if data["priority"] not in PRIORITIES:
            raise ValueError("Приоритет: low, normal или high")
        d.priority = data["priority"]
    if "due_date" in data:
        d.due_date = _date(data["due_date"])
    db.flush()
    return _defect_dict(db, d, full=True)


def change_status(db: Session, project_id: int, user_id: int | None, defect_id: int, status: str, comment: str | None) -> dict:
    d = _get(db, project_id, defect_id)
    if status not in STATUSES:
        raise ValueError("Неизвестный статус")
    if status == d.status:
        raise ValueError("Замечание уже в этом статусе")
    if status not in TRANSITIONS.get(d.status, set()):
        raise ValueError(f"Из статуса «{STATUS_LABEL[d.status]}» нельзя перейти в «{STATUS_LABEL[status]}»")
    has_after = db.scalar(select(func.count()).select_from(DefectPhoto).where(DefectPhoto.defect_id == d.id, DefectPhoto.kind == "after")) or 0
    if status == "fixed" and not has_after and not (comment or "").strip():
        raise ValueError("Чтобы отметить замечание устранённым, приложите фото результата или напишите комментарий")
    d.status = status
    d.closed_at = datetime.now(timezone.utc) if status == "accepted" else None
    db.add(DefectComment(project_id=project_id, defect_id=d.id, author_user_id=user_id, text=(comment or "").strip(), status_change=status))
    db.flush()
    return _defect_dict(db, d, full=True)


def add_comment(db: Session, project_id: int, user_id: int | None, defect_id: int, text: str) -> dict:
    d = _get(db, project_id, defect_id)
    t = (text or "").strip()
    if not t:
        raise ValueError("Комментарий пустой")
    db.add(DefectComment(project_id=project_id, defect_id=d.id, author_user_id=user_id, text=t[:5000]))
    db.flush()
    return _defect_dict(db, d, full=True)


def add_photo(db: Session, project_id: int, defect_id: int, kind: str, filename: str, mime: str | None, data: bytes) -> dict:
    d = _get(db, project_id, defect_id)
    if kind not in ("before", "after"):
        raise ValueError("Вид фото: before или after")
    if not data:
        raise ValueError("Пустой файл")
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError("Фото больше 10 МБ")
    mime = (mime or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise ValueError("Допустимы фото JPG, PNG или WEBP")
    count = db.scalar(select(func.count()).select_from(DefectPhoto).where(DefectPhoto.defect_id == d.id)) or 0
    if count >= MAX_PHOTOS:
        raise ValueError(f"К замечанию можно приложить до {MAX_PHOTOS} фото")
    name = re.sub(r"[\\/\x00-\x1f]", "_", (filename or "photo").strip())[:300] or "photo"
    db.add(DefectPhoto(project_id=project_id, defect_id=d.id, kind=kind, filename=name, mime_type=mime, size_bytes=len(data), data=data))
    db.flush()
    return _defect_dict(db, d, full=True)


def get_photo(db: Session, project_id: int, defect_id: int, photo_id: int) -> DefectPhoto:
    _get(db, project_id, defect_id)
    p = db.get(DefectPhoto, photo_id)
    if p is None or p.defect_id != defect_id:
        raise LookupError("Фото не найдено")
    return p


def delete_photo(db: Session, project_id: int, defect_id: int, photo_id: int) -> dict:
    p = get_photo(db, project_id, defect_id, photo_id)
    db.delete(p)
    db.flush()
    return get_defect(db, project_id, defect_id)


def delete_defect(db: Session, project_id: int, defect_id: int) -> None:
    d = _get(db, project_id, defect_id)
    for model in (DefectPhoto, DefectComment):
        for row in db.execute(select(model).where(model.defect_id == d.id)).scalars().all():
            db.delete(row)
    db.delete(d)
    db.flush()


def to_xlsx(db: Session, project_id: int, status: str | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Замечания"
    ws.append(["№", "Замечание", "Описание", "Объект", "Место", "Приоритет", "Статус", "Ответственный", "Срок", "Просрочено", "Создано", "Закрыто", "Фото"])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for d in reversed(list_defects(db, project_id, status)["defects"]):
        ws.append([
            d["number"], d["title"], d["description"] or "", d["object_name"] or "", d["location"] or "", PRIORITY_LABEL.get(d["priority"], d["priority"]),
            d["status_label"], d["responsible"] or "", d["due_date"] or "", "да" if d["overdue"] else "", (d["created_at"] or "")[:10],
            (d["closed_at"] or "")[:10], len(d["photos"]),
        ])
    for col, w in zip("ABCDEFGHIJKLM", (6, 40, 50, 18, 22, 12, 14, 22, 12, 11, 12, 12, 7)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
