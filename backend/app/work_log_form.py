"""Общий журнал работ по форме РД-11-05-2007 (печатная форма, Word)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import work_log as wl
from app.acceptance_act_document import _para, _table
from app.docx_kit import build_docx, page_break
from app.models.defects import Defect, DefectPhoto
from app.models.estimates import KsAct
from app.models.project import Project

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


def collect_weekly_report(db: Session, project_id: int, date_from, date_to) -> dict:
    """Собрать данные недельного отчёта (только чтение, без записи в БД).

    Работы (work facts) фильтруются по периоду; дефекты, фото и акты КС —
    текущее состояние проекта на момент сбора, без выдуманного фильтра по датам.

    Все чтения выполняются в no_autoflush: отложенные изменения вызывающего
    кода не сбрасываются в БД и не попадают в отчёт. Никаких flush/commit/rollback.
    Фото дефектов читаются явной проекцией колонок (без бинарных данных).
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


def collect_weekly_report_bounded(db: Session, project_id: int, date_from, date_to, *, row_budget: int, text_byte_budget: int | None = None) -> dict:
    """Bound output rows at SQL level; text byte budgets remain separate."""
    from datetime import datetime
    from decimal import Decimal
    from app.models.work_log import WorkLogEntry
    if isinstance(row_budget, bool) or not isinstance(row_budget, int) or row_budget <= 0:
        raise ValueError("Укажите положительный бюджет строк")
    start, end = _iso(date_from), _iso(date_to)
    if start > end:
        raise ValueError("Начало периода позже окончания")
    from sqlalchemy import String, Text, func
    if text_byte_budget is not None and (isinstance(text_byte_budget, bool) or not isinstance(text_byte_budget, int) or text_byte_budget <= 0):
        raise ValueError("Некорректный бюджет байт")
    text_remaining = text_byte_budget
    def column(c):
        return func.substr(c, 1, text_remaining + 1).label(c.key) if text_remaining is not None and isinstance(c.type, (String, Text)) else c
    def charge(v):
        nonlocal text_remaining
        if text_remaining is not None and isinstance(v, str):
            text_remaining -= len(v.encode('utf-8'))
            if text_remaining < 0:
                raise ValueError("Превышен бюджет текста отчёта")
    remaining = row_budget

    def fetch(model, fields, *conditions):
        nonlocal remaining
        query = select(*(column(getattr(model, field)) for field in fields)).where(*conditions).order_by(model.id).limit(remaining + 1)
        output = []
        records = db.execute(query.execution_options(yield_per=1)).mappings()
        try:
            for row in records:
                if remaining == 0:
                    raise ValueError("Превышен бюджет строк отчёта")
                record = {k: v.isoformat() if isinstance(v, (date, datetime)) else format(v, 'f') if isinstance(v, Decimal) else v for k,v in row.items()}
                for value in record.values(): charge(value)
                output.append(record)
                remaining -= 1
        finally:
            records.close()
        return output

    with db.no_autoflush:
        project = db.execute(select(Project.id, column(Project.name)).where(Project.id == project_id)).first()
        if project is None:
            raise LookupError("Проект не найден")
        charge(project.name)
        facts = fetch(WorkLogEntry, ('id','work_date','work_name','unit','qty','location','weather','workers','hours','crew_id','note'),
                      WorkLogEntry.project_id == project_id, WorkLogEntry.work_date >= date.fromisoformat(start), WorkLogEntry.work_date <= date.fromisoformat(end))
        defects = fetch(Defect, ('id','number','title','status','responsible','due_date','location'), Defect.project_id == project_id)
        photos = fetch(DefectPhoto, ('id','defect_id','filename','mime_type','size_bytes'), DefectPhoto.project_id == project_id,
                       DefectPhoto.defect_id.in_(select(Defect.id).where(Defect.project_id == project_id)))
        acts = fetch(KsAct, ('id','number','status','customer_status','period_from','period_to','act_date'), KsAct.project_id == project_id)
    return {'project_id': project.id, 'project_name': project.name, 'period': {'date_from': start, 'date_to': end},
            'work_facts': facts, 'defects': defects, 'defect_photos': photos, 'ks_acts': acts}


class ReportRendererUnavailable(ValueError):
    """Required local renderer resource is missing."""


def render_weekly_pdf(report: dict, *, font_path: str | None = None, photo_attachments: tuple = (), photo_byte_budget: int = 0, photo_pixel_budget: int = 0) -> bytes:
    """Render scoped metadata and budgeted photo bytes; print acceptance is separate."""
    from io import BytesIO
    from hashlib import sha256
    from html import escape
    from pathlib import Path
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    path = Path(font_path) if font_path is not None else Path(__file__).parent / "assets" / "fonts" / "DejaVuSans.ttf"
    if not path.is_file():
        raise ReportRendererUnavailable("Не найден шрифт PDF")
    font_name = 'Weekly-' + sha256(path.read_bytes()).hexdigest()[:16]
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(path)))
    style = ParagraphStyle('Weekly',fontName=font_name,fontSize=10,leading=14,spaceAfter=5,splitLongWords=True)
    title_style = ParagraphStyle('WeeklyTitle',parent=style,fontSize=16,leading=20)
    story = []
    def paragraph(text, heading=False):
        story.append(Paragraph(escape(str(text)),title_style if heading else style))
    paragraph('Недельный отчёт',True)
    paragraph(report['project_name'])
    paragraph(str(report['period']['date_from'])+' — '+str(report['period']['date_to']))
    story.append(Spacer(1,10))
    paragraph('Факт за период',True)
    for row in report['work_facts']:
        paragraph(' | '.join('' if row.get(key) is None else str(row[key]) for key in ('work_date','work_name','qty','unit','location')))
    paragraph('Замечания: текущее состояние',True)
    for row in report['defects']:
        paragraph(' | '.join(str(row.get(key) or '') for key in ('title','status','responsible','due_date')))
    paragraph('Документы КС: текущие статусы',True)
    for row in report['ks_acts']:
        paragraph(' | '.join(str(row.get(key) or '') for key in ('number','status','customer_status')))
    paragraph('Фото: сведения о вложениях',True)
    for row in report['defect_photos']:
        paragraph(str(row.get('filename') or '')+' ('+str(row.get('size_bytes') or 0)+' байт)')
    if photo_attachments:
        import warnings
        from PIL import Image as PillowImage, ImageOps
        from reportlab.platypus import Image as PdfImage
        for budget in (photo_byte_budget, photo_pixel_budget):
            if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
                raise ValueError("Укажите положительные бюджеты фотографий")
        metadata = {row['id']: row for row in report['defect_photos']}
        seen = set()
        byte_count = pixel_count = 0
        for attachment in photo_attachments:
            photo_id, data = attachment.get('id'), attachment.get('data')
            if isinstance(photo_id, bool) or not isinstance(photo_id, int) or photo_id not in metadata or photo_id in seen:
                raise ValueError("Неизвестная или повторная фотография отчёта")
            seen.add(photo_id)
            if not isinstance(data, bytes) or not data:
                raise ValueError("Ожидались байты фотографии")
            byte_count += len(data)
            if byte_count > photo_byte_budget or len(data) != metadata[photo_id]['size_bytes']:
                raise ValueError("Превышен бюджет фото или не совпадает размер")
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', PillowImage.DecompressionBombWarning)
                    with PillowImage.open(BytesIO(data)) as source:
                        expected_mime = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}.get(source.format)
                        if expected_mime is None or metadata[photo_id].get('mime_type') != expected_mime:
                            raise ValueError("Недопустимый формат фото или MIME-тип")
                        pixel_count += source.width * source.height
                        if pixel_count > photo_pixel_budget:
                            raise ValueError("Превышен бюджет пикселей")
                        source.load()
                        image = ImageOps.exif_transpose(source).convert('RGB')
                        normalized = BytesIO()
                        image.save(normalized, format='PNG')
                        normalized.seek(0)
                        width, height = image.size
                scale = min(480 / width, 480 / height, 1)
                paragraph(metadata[photo_id]['filename'])
                story.append(PdfImage(normalized, width=width * scale, height=height * scale))
            except (OSError, PillowImage.DecompressionBombError, PillowImage.DecompressionBombWarning) as exc:
                raise ValueError("Некорректное изображение") from exc
    output=BytesIO()
    SimpleDocTemplate(output,pagesize=A4,leftMargin=36,rightMargin=36,topMargin=36,bottomMargin=36).build(story)
    return output.getvalue()



def load_weekly_report_photos(db: Session, project_id: int, metadata: list[dict], *, byte_budget: int) -> tuple:
    """Read only scoped photo bytes; SQL guards oversize cells before transfer."""
    from sqlalchemy import case, func
    if isinstance(byte_budget, bool) or not isinstance(byte_budget, int) or byte_budget <= 0:
        raise ValueError("Некорректный бюджет фотографий")
    remaining, result, seen = byte_budget, [], set()
    with db.no_autoflush:
        for item in metadata:
            photo_id = item.get('id')
            if isinstance(photo_id, bool) or not isinstance(photo_id, int) or photo_id in seen:
                raise ValueError("Некорректная или повторная фотография")
            seen.add(photo_id)
            size = func.length(DefectPhoto.data)
            row = db.execute(select(DefectPhoto.id, size.label('actual_size'),
                case((size <= remaining, DefectPhoto.data), else_=None).label('payload'))
                .join(Defect, Defect.id == DefectPhoto.defect_id)
                .where(DefectPhoto.id == photo_id, DefectPhoto.project_id == project_id,
                       Defect.project_id == project_id)).first()
            if row is None:
                raise LookupError("Фотография отчёта не найдена")
            if row.actual_size is None or row.actual_size > remaining or row.payload is None:
                raise ValueError("Превышен бюджет фотографий")
            if row.actual_size != item.get('size_bytes'):
                raise ValueError("Фотография изменилась при формировании отчёта")
            remaining -= row.actual_size
            result.append({'id': photo_id, 'data': row.payload})
    return tuple(result)
