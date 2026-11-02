"""Общий журнал работ по форме РД-11-05-2007 (печатная форма, Word)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import work_log as wl
from app.acceptance_act_document import _para, _table
from app.docx_kit import build_docx, page_break
from app.models.defects import Defect, DefectPhoto
from app.models.estimates import KsAct
from app.models.project import Project
from app.models.work_log import WorkLogEntry, WorkLogLabor

BLANK = "________________"


def _t(value) -> str:
    return (str(value).strip() if value is not None else "") or BLANK


def _d(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return value or ""


def _iso(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректная дата") from exc


def _qty_str(value) -> str | None:
    return f"{Decimal(value).quantize(Decimal('0.001')):f}" if value is not None else None


def _hours_str(value) -> str | None:
    return f"{Decimal(value).quantize(Decimal('0.01')):f}" if value is not None else None


def _check_row_budget(row_budget) -> int:
    """Проверить бюджет строк: положительное целое, не bool.

    Это ограничение ЧИСЛА СТРОК (work facts + defects + photos + acts),
    а не объёма текста в байтах. Текстовый бюджет (длина work_name/note и т.п.)
    остаётся отдельной задачей и здесь не реализуется.
    """
    if isinstance(row_budget, bool) or not isinstance(row_budget, int):
        raise ValueError("row_budget должен быть положительным целым")
    if row_budget <= 0:
        raise ValueError("row_budget должен быть положительным целым")
    return row_budget


def _labor_for_entries(db: Session, entry_ids: list[int]) -> dict[int, list[dict]]:
    """Табель только для переданных id записей (bounded ids), без чтения лишних строк."""
    if not entry_ids:
        return {}
    rows = db.execute(
        select(
            WorkLogLabor.entry_id,
            WorkLogLabor.person_name,
            WorkLogLabor.hours,
            WorkLogLabor.hourly_rate,
        )
        .where(WorkLogLabor.entry_id.in_(entry_ids))
        .order_by(WorkLogLabor.entry_id, WorkLogLabor.id)
    ).all()
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(row.entry_id, []).append(
            {
                "person_name": row.person_name,
                "hours": _hours_str(row.hours),
                "hourly_rate": _hours_str(row.hourly_rate),
            }
        )
    return result


def collect_weekly_report_bounded(
    db: Session, project_id: int, date_from, date_to, *, row_budget: int
) -> dict:
    """Собрать данные недельного отчёта с жёстким бюджетом строк (только чтение).

    Форма результата совпадает с collect_weekly_report (тот же набор ключей).

    Бюджет строк: суммарное число выданных строк
    (work_facts + defects + defect_photos + ks_acts) не превышает row_budget.
    Каждый источник читается через SQL LIMIT на остаток бюджета (+1 для
    обнаружения переполнения); при переполнении поднимается ValueError и
    частичный отчёт НЕ возвращается. Никакого count-then-fetch и никакого
    Python-лимита после выборки всех строк.

    ВАЖНО: это ограничение ЧИСЛА СТРОК, а не объёма текста в байтах.
    Текстовые поля (work_name, note, location и т.п.) могут быть большими;
    отдельный текстовый/байтовый бюджет и защита памяти при сборке PDF
    остаются отдельной задачей. Эта функция не является полным решением
    ресурсных ограничений.

    Работы (work facts) фильтруются по периоду (включительно); дефекты, фото
    и акты КС — текущее состояние проекта на момент сбора. Все чтения в
    no_autoflush; flush/commit/rollback не выполняются. Фото читаются явной
    проекцией колонок (без бинарных данных), с проверкой project_id и у фото,
    и у владеющего дефекта.
    """
    budget = _check_row_budget(row_budget)
    start = _iso(date_from)
    end = _iso(date_to)
    if start > end:
        raise ValueError("Начало периода позже окончания")

    with db.no_autoflush:
        project = db.get(Project, project_id)
        if project is None:
            raise LookupError("Проект не найден")

        remaining = budget

        # Работы: явная проекция колонок, детерминированный порядок, включительный период.
        entry_stmt = (
            select(
                WorkLogEntry.id,
                WorkLogEntry.work_date,
                WorkLogEntry.work_name,
                WorkLogEntry.unit,
                WorkLogEntry.qty,
                WorkLogEntry.location,
                WorkLogEntry.weather,
                WorkLogEntry.workers,
                WorkLogEntry.hours,
                WorkLogEntry.crew_id,
                WorkLogEntry.note,
            )
            .where(
                WorkLogEntry.project_id == project_id,
                WorkLogEntry.work_date >= date.fromisoformat(start),
                WorkLogEntry.work_date <= date.fromisoformat(end),
            )
            .order_by(WorkLogEntry.work_date.desc(), WorkLogEntry.id.desc())
            .limit(remaining + 1)
        )
        entry_rows = db.execute(entry_stmt).all()
        if len(entry_rows) > remaining:
            raise ValueError("Превышен бюджет строк отчёта")
        remaining -= len(entry_rows)

        entry_ids = [r.id for r in entry_rows]
        labor_by_entry = _labor_for_entries(db, entry_ids)
        work_facts = [
            {
                "id": r.id,
                "work_date": r.work_date.isoformat(),
                "work_name": r.work_name,
                "unit": r.unit,
                "qty": _qty_str(r.qty),
                "location": r.location,
                "weather": r.weather,
                "workers": r.workers,
                "hours": _hours_str(r.hours),
                "crew_id": r.crew_id,
                "note": r.note,
                "labor": labor_by_entry.get(r.id, []),
            }
            for r in entry_rows
        ]

        # Дефекты: проекция колонок, детерминированный порядок.
        defect_stmt = (
            select(
                Defect.id,
                Defect.number,
                Defect.title,
                Defect.status,
                Defect.responsible,
                Defect.due_date,
                Defect.location,
            )
            .where(Defect.project_id == project_id)
            .order_by(Defect.number, Defect.id)
            .limit(remaining + 1)
        )
        defect_rows_raw = db.execute(defect_stmt).all()
        if len(defect_rows_raw) > remaining:
            raise ValueError("Превышен бюджет строк отчёта")
        remaining -= len(defect_rows_raw)
        defect_rows = [
            {
                "id": d.id,
                "number": d.number,
                "title": d.title,
                "status": d.status,
                "responsible": d.responsible,
                "due_date": d.due_date.isoformat() if d.due_date else None,
                "location": d.location,
            }
            for d in defect_rows_raw
        ]

        # Фото: проекция без бинарных данных; project_id и у фото, и у дефекта.
        photo_stmt = (
            select(
                DefectPhoto.id,
                DefectPhoto.defect_id,
                DefectPhoto.filename,
                DefectPhoto.mime_type,
                DefectPhoto.size_bytes,
            )
            .join(Defect, Defect.id == DefectPhoto.defect_id)
            .where(
                DefectPhoto.project_id == project_id,
                Defect.project_id == project_id,
            )
            .order_by(DefectPhoto.id)
            .limit(remaining + 1)
        )
        photo_rows_raw = db.execute(photo_stmt).all()
        if len(photo_rows_raw) > remaining:
            raise ValueError("Превышен бюджет строк отчёта")
        remaining -= len(photo_rows_raw)
        photo_rows = [
            {
                "id": row.id,
                "defect_id": row.defect_id,
                "filename": row.filename,
                "mime_type": row.mime_type,
                "size_bytes": row.size_bytes,
            }
            for row in photo_rows_raw
        ]

        # Акты КС: проекция колонок, детерминированный порядок.
        act_stmt = (
            select(
                KsAct.id,
                KsAct.number,
                KsAct.status,
                KsAct.customer_status,
                KsAct.period_from,
                KsAct.period_to,
                KsAct.act_date,
            )
            .where(KsAct.project_id == project_id)
            .order_by(KsAct.id)
            .limit(remaining + 1)
        )
        act_rows_raw = db.execute(act_stmt).all()
        if len(act_rows_raw) > remaining:
            raise ValueError("Превышен бюджет строк отчёта")
        remaining -= len(act_rows_raw)
        act_rows = [
            {
                "id": a.id,
                "number": a.number,
                "status": a.status,
                "customer_status": a.customer_status,
                "period_from": a.period_from.isoformat() if a.period_from else None,
                "period_to": a.period_to.isoformat() if a.period_to else None,
                "act_date": a.act_date.isoformat() if a.act_date else None,
            }
            for a in act_rows_raw
        ]

        return {
            "project_id": project.id,
            "project_name": project.name,
            "period": {"date_from": start, "date_to": end},
            "work_facts": work_facts,
            "defects": defect_rows,
            "defect_photos": photo_rows,
            "ks_acts": act_rows,
        }


def collect_weekly_report(db: Session, project_id: int, date_from, date_to) -> dict:
    """Собрать данные недельного отчёта (только чтение, без записи в БД).

    Работы (work facts) фильтруются по периоду; дефекты, фото и акты КС —
    текущее состояние проекта на момент сбора, без выдуманного фильтра по датам.

    Все чтения выполняются в no_autoflush: отложенные изменения вызывающего
    кода не сбрасываются в БД и не попадают в отчёт. Никаких flush/commit/rollback.
    Фото дефектов читаются явной проекцией колонок (без бинарных данных).

    ВНИМАНИЕ: эта функция НЕ ограничена по числу строк (limit в list_entries
    применяется в Python после выборки всех строк). Для ограниченного сбора
    используйте collect_weekly_report_bounded с обязательным row_budget.
    """
    start = _iso(date_from)
    end = _iso(date_to)
    if start > end:
        raise ValueError("Начало периода позже окончания")

    with db.no_autoflush:
        project = db.get(Project, project_id)
        if project is None:
            raise LookupError("Проект не найден")

        entries = wl.list_entries(db, project_id, start, end, limit=100000)
        work_facts = [
            {
                "id": e["id"],
                "work_date": e["work_date"],
                "work_name": e["work_name"],
                "unit": e["unit"],
                "qty": e["qty"],
                "location": e["location"],
                "weather": e["weather"],
                "workers": e["workers"],
                "hours": e["hours"],
                "crew_id": e["crew_id"],
                "note": e["note"],
            }
            for e in entries
        ]

        defects = db.execute(
            select(Defect).where(Defect.project_id == project_id).order_by(Defect.number, Defect.id)
        ).scalars().all()
        defect_rows = [
            {
                "id": d.id,
                "number": d.number,
                "title": d.title,
                "status": d.status,
                "responsible": d.responsible,
                "due_date": d.due_date.isoformat() if d.due_date else None,
                "location": d.location,
            }
            for d in defects
        ]

        # Явная проекция колонок: бинарные данные (data) не загружаются.
        # Требуем совпадения project_id и у фото, и у владеющего дефекта,
        # чтобы отсечь несогласованные строки внешних ключей.
        photo_stmt = (
            select(
                DefectPhoto.id,
                DefectPhoto.defect_id,
                DefectPhoto.filename,
                DefectPhoto.mime_type,
                DefectPhoto.size_bytes,
            )
            .join(Defect, Defect.id == DefectPhoto.defect_id)
            .where(
                DefectPhoto.project_id == project_id,
                Defect.project_id == project_id,
            )
            .order_by(DefectPhoto.id)
        )
        photo_rows = [
            {
                "id": row.id,
                "defect_id": row.defect_id,
                "filename": row.filename,
                "mime_type": row.mime_type,
                "size_bytes": row.size_bytes,
            }
            for row in db.execute(photo_stmt).all()
        ]

        acts = db.execute(
            select(KsAct).where(KsAct.project_id == project_id).order_by(KsAct.id)
        ).scalars().all()
        act_rows = [
            {
                "id": a.id,
                "number": a.number,
                "status": a.status,
                "customer_status": a.customer_status,
                "period_from": a.period_from.isoformat() if a.period_from else None,
                "period_to": a.period_to.isoformat() if a.period_to else None,
                "act_date": a.act_date.isoformat() if a.act_date else None,
            }
            for a in acts
        ]

        return {
            "project_id": project.id,
            "project_name": project.name,
            "period": {"date_from": start, "date_to": end},
            "work_facts": work_facts,
            "defects": defect_rows,
            "defect_photos": photo_rows,
            "ks_acts": act_rows,
        }


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
