"""Кабельный журнал: заготовка по подтверждённым позициям РД.

Трассы (откуда, куда), даты и фактическая длина вносятся человеком; из РД берутся
марка, сечение и проектная длина, так что вручную переписывать спецификацию не нужно.
"""
from __future__ import annotations

import re
from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rd import RdSpecItem

_CABLE_START = re.compile(r"^\s*(кабел|провод|шнур)", re.IGNORECASE)
_CABLE_MARK = re.compile(
    r"\b(ВВГ\w*|КВВГ\w*|АВВГ\w*|ПВС|ШВВП|NYM|NYY|UTP|FTP|S/FTP|U/UTP|КСВВ\w*|КПСЭнг\w*|МКЭШ\w*|ВБШв\w*|АВБШв\w*|ППГ\w*|ПуГВ|ПУГВ|ПВ[123])\b",
    re.IGNORECASE,
)
_LENGTH_UNITS = {"м", "м.п", "м.п.", "п.м", "п.м.", "км", "м.пог", "м.пог."}
_NOT_CABLE = re.compile(r"лоток|короб|труб|муфт|наконечник|клемм|держател|скоб|хомут|гильз|втулк|изолент|стойк", re.IGNORECASE)


def is_cable(name: str | None, type_mark: str | None, unit: str | None) -> bool:
    text = f"{name or ''} {type_mark or ''}"
    if _NOT_CABLE.search(name or "") and not _CABLE_START.search(name or ""):
        return False
    if _CABLE_START.search(name or ""):
        return True
    unit_norm = (unit or "").strip().lower()
    return bool(_CABLE_MARK.search(text)) and (unit_norm in _LENGTH_UNITS or not unit_norm)


def cable_rows(db: Session, project_id: int) -> list[dict]:
    items = db.execute(
        select(RdSpecItem).where(RdSpecItem.project_id == project_id, RdSpecItem.status == "confirmed")
        .order_by(RdSpecItem.volume_id, RdSpecItem.sort_order, RdSpecItem.id)
    ).scalars().all()
    rows = []
    for it in items:
        if not is_cable(it.name, it.type_mark, it.unit):
            continue
        rows.append({
            "pos": it.pos or "",
            "name": it.name,
            "type_mark": it.type_mark or "",
            "unit": it.unit or "м",
            "qty": f"{it.qty:f}" if it.qty is not None else None,
        })
    return rows


def to_xlsx(db: Session, project_id: int) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Кабельный журнал"
    head = ["№ п/п", "Поз. по РД", "Кабель, марка, сечение", "Тип/марка по спецификации", "Ед. изм.", "Длина по проекту",
            "Откуда (начало трассы)", "Куда (конец трассы)", "Дата прокладки", "Фактическая длина", "Примечание"]
    ws.append(head)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for i, r in enumerate(cable_rows(db, project_id), start=1):
        ws.append([i, r["pos"], r["name"], r["type_mark"], r["unit"], float(r["qty"]) if r["qty"] is not None else None,
                   "", "", "", None, ""])
    for col, width in zip("ABCDEFGHIJK", (7, 10, 52, 26, 8, 14, 28, 28, 14, 14, 30)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
