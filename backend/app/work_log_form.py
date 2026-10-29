"""Общий журнал работ по форме РД-11-05-2007 (печатная форма, Word)."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app import work_log as wl
from app.acceptance_act_document import _para, _table
from app.docx_kit import build_docx, page_break

BLANK = "________________"


def _t(value) -> str:
    return (str(value).strip() if value is not None else "") or BLANK


def _d(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return value or ""


def render(db: Session, project_id: int, header: dict | None, date_from=None, date_to=None) -> bytes:
    h = header or {}
    entries = list(reversed(wl.list_entries(db, project_id, date_from, date_to, limit=100000)))
    crews = wl.list_crews(db, project_id)
    p: list[str] = []

    # Титульный лист
    p.append(_para("ОБЩИЙ ЖУРНАЛ РАБОТ", bold=True, align="center"))
    p.append(_para(f"№ {_t(h.get('journal_no'))}", align="center"))
    p.append(_para(""))
    p.append(_para("Объект капитального строительства", bold=True))
    p.append(_para(_t(h.get("object_name"))))
    p.append(_para(f"Адрес: {_t(h.get('address'))}"))
    p.append(_para(""))
    p.append(_para(f"Застройщик или технический заказчик: {_t(h.get('customer'))}"))
    p.append(_para(f"Лицо, осуществляющее строительство: {_t(h.get('contractor'))}"))
    p.append(_para(f"Лицо, осуществляющее подготовку проектной документации: {_t(h.get('designer'))}"))
    p.append(_para(""))
    p.append(_para(f"Начало работ: {_t(h.get('start_date'))}    Окончание работ: {_t(h.get('finish_date'))}"))
    p.append(_para("Журнал начат: " + _t(h.get("journal_start")) + "    Журнал закончен: " + _t(h.get("journal_end"))))
    p.append(page_break())

    # Раздел 1. Список ИТР
    p.append(_para("Раздел 1. Список инженерно-технического персонала лица, осуществляющего строительство", bold=True))
    people = [["№ п/п", "Фамилия, инициалы, должность", "Номер и дата документа о назначении", "Период работы на объекте"]]
    n = 0
    for c in crews:
        if c.get("foreman"):
            n += 1
            people.append([str(n), f"{c['foreman']} (бригадир, {c['name']})", "", ""])
    if n == 0:
        people.append(["1", "", "", ""])
    p.append(_table(people))
    p.append(_para(""))

    # Раздел 2. Перечень специальных журналов и актов
    p.append(_para("Раздел 2. Перечень специальных журналов, актов и протоколов, ведущихся при строительстве", bold=True))
    p.append(_table([["№ п/п", "Наименование документа", "Ответственное лицо", "Место хранения"], ["1", "", "", ""]]))
    p.append(page_break())

    # Раздел 3. Сведения о выполнении работ
    p.append(_para("Раздел 3. Сведения о выполнении работ в процессе строительства, реконструкции, капитального ремонта", bold=True))
    rows = [["Дата (смена)", "Наименование работ, выполненных в процессе строительства, с указанием места их выполнения (оси, этажи, отметки), объёмы, число рабочих и часов",
             "Условия производства работ (погода, температура)", "Подпись лица, осуществляющего строительство (Ф.И.О.)"]]
    for e in entries:
        parts = [e["work_name"]]
        if e.get("qty"):
            parts.append(f"{e['qty']} {e.get('unit') or ''}".strip())
        if e.get("location"):
            parts.append(f"место: {e['location']}")
        if e.get("workers"):
            parts.append(f"рабочих: {e['workers']}")
        if e.get("hours"):
            parts.append(f"часов: {e['hours']}")
        crew = next((c["name"] for c in crews if c["id"] == e.get("crew_id")), None)
        if crew:
            parts.append(f"бригада: {crew}")
        if e.get("note"):
            parts.append(e["note"])
        rows.append([_d(e["work_date"]), "; ".join(x for x in parts if x), e.get("weather") or "", ""])
    if len(rows) == 1:
        rows.append(["", "", "", ""])
    p.append(_table(rows))
    p.append(_para(""))

    # Раздел 4–5: рамки для заполнения вручную
    p.append(_para("Раздел 4. Сведения о строительном контроле застройщика или технического заказчика", bold=True))
    p.append(_table([["Дата", "Замечания и предписания", "Срок устранения", "Подпись"], ["", "", "", ""]]))
    p.append(_para(""))
    p.append(_para("Раздел 5. Сведения о строительном контроле лица, осуществляющего строительство", bold=True))
    p.append(_table([["Дата", "Результаты контроля, замечания", "Срок устранения", "Подпись"], ["", "", "", ""]]))
    return build_docx(p, landscape=True)
