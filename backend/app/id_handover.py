"""Документы сдачи объекта: ведомость оборудования, реестр ИД, акты 72 часов и ввода в эксплуатацию."""
from __future__ import annotations

import io
from datetime import date

from sqlalchemy.orm import Session

from app.acceptance_act_document import _MONTHS_GEN, _para, _table
from app.docx_kit import build_docx

BLANK = "________________"
KIND_LABELS = {
    "aosr": "АОСР", "act": "Акт", "protocol": "Протокол", "passport": "Паспорт", "certificate": "Сертификат",
    "declaration": "Декларация", "scheme": "Схема", "journal": "Журнал", "register": "Ведомость/реестр",
    "report": "Отчёт", "order": "Приказ", "other": "Прочее",
}
STATUS_LABELS = {"present": "есть", "draft": "черновик", "missing": "нет"}


def _text(value) -> str:
    return (str(value).strip() if value is not None else "") or BLANK


def _date_words(value) -> str:
    if not value:
        return "«___» __________ 20__ г."
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"«{d.day:02d}» {_MONTHS_GEN[d.month - 1]} {d.year} г."


def default_header(db: Session, project_id: int, object_name: str | None) -> dict:
    """Что известно системе: заказчик, подрядчик, представители из участников ИД."""
    from app.models.executive_docs import IdParticipant
    from app.models.organization_contract import Contract, Organization
    from app.models.project import Project

    project = db.query(Project).filter(Project.id == project_id).first()
    header: dict = {"object_name": object_name or (project.name if project else ""), "date": date.today().isoformat()}
    contract = db.query(Contract).filter(Contract.project_id == project_id).order_by(Contract.id).first()
    if contract is not None and contract.counterparty:
        header["customer"] = contract.counterparty
    if project is not None and project.organization_id is not None:
        org = db.query(Organization).filter(Organization.id == project.organization_id).first()
        if org is not None and org.name:
            header["contractor"] = org.name
    people = db.query(IdParticipant).filter(
        IdParticipant.project_id == project_id,
        (IdParticipant.object_name == object_name) | (IdParticipant.object_name.is_(None)),
    ).order_by(IdParticipant.id).all()
    for p in people:
        label = f"{p.full_name}, {p.position}" if p.position else p.full_name
        if p.role in ("customer_control",) and "customer_rep" not in header:
            header["customer_rep"] = label
        elif p.role in ("builder", "builder_control", "performer") and "contractor_rep" not in header:
            header["contractor_rep"] = label
    return header


# ---------- ведомость смонтированного оборудования ----------

def equipment_rows(db: Session, project_id: int, object_name: str | None) -> list[dict]:
    from app.models.rd import RdSpecItem, RdVolume

    vols = db.query(RdVolume).filter(RdVolume.project_id == project_id).all()
    if object_name and any(v.object_name == object_name for v in vols):
        vols = [v for v in vols if v.object_name == object_name]
    by_id = {v.id: v for v in vols}
    if not by_id:
        return []
    items = db.query(RdSpecItem).filter(
        RdSpecItem.volume_id.in_(list(by_id)), RdSpecItem.status != "rejected",
    ).order_by(RdSpecItem.volume_id, RdSpecItem.sort_order, RdSpecItem.id).all()
    rows = []
    for it in items:
        vol = by_id[it.volume_id]
        rows.append({
            "name": it.name, "type_mark": it.type_mark or "", "code": it.code or "",
            "manufacturer": it.manufacturer or "", "unit": it.unit or "",
            "qty": "" if it.qty is None else f"{it.qty.normalize():f}",
            "section": vol.section or "", "volume": vol.code or vol.title,
        })
    return rows


def render_equipment_register(header: dict, rows: list[dict]) -> bytes:
    if not rows:
        raise ValueError("В РД нет позиций спецификации — сначала разберите РД")
    h = header or {}
    p = [
        _para("ВЕДОМОСТЬ СМОНТИРОВАННОГО ОБОРУДОВАНИЯ", bold=True, align="center"),
        _para(f"Объект: {_text(h.get('object_name'))}"),
        _para(f"Заказчик: {_text(h.get('customer'))}"),
        _para(f"Подрядчик: {_text(h.get('contractor'))}"),
        _para(f"Дата: {_date_words(h.get('date'))}"),
        _para(""),
    ]
    table = [["№", "Наименование", "Тип, марка", "Код", "Изготовитель", "Ед.", "Кол-во", "Раздел", "Том РД"]]
    for i, r in enumerate(rows, start=1):
        table.append([str(i), r["name"], r["type_mark"] or "—", r["code"] or "—", r["manufacturer"] or "—",
                      r["unit"] or "—", r["qty"] or "—", r["section"] or "—", r["volume"] or "—"])
    p.append(_table(table))
    p.append(_para(""))
    p.append(_para("Оборудование смонтировано в соответствии с проектом (рабочей документацией)."))
    p.append(_para(f"Представитель подрядчика: ____________ / {_text(h.get('contractor_rep'))} /"))
    p.append(_para(f"Представитель заказчика: ____________ / {_text(h.get('customer_rep'))} /"))
    return build_docx(p, landscape=True)


def render_equipment_register_xlsx(header: dict, rows: list[dict]) -> bytes:
    if not rows:
        raise ValueError("В РД нет позиций спецификации — сначала разберите РД")
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Ведомость оборудования"
    ws.append([f"Ведомость смонтированного оборудования. Объект: {(header or {}).get('object_name') or ''}"])
    ws.append(["№", "Наименование", "Тип, марка", "Код", "Изготовитель", "Ед.", "Кол-во", "Раздел", "Том РД"])
    for i, r in enumerate(rows, start=1):
        ws.append([i, r["name"], r["type_mark"], r["code"], r["manufacturer"], r["unit"], r["qty"], r["section"], r["volume"]])
    for col, width in zip("ABCDEFGHI", (5, 50, 28, 18, 24, 7, 10, 10, 24)):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- реестр исполнительной документации ----------

def register_rows(db: Session, project_id: int, object_name: str) -> list[dict]:
    from app.executive_docs import object_report

    report = object_report(db, project_id, object_name)
    rows = []
    for m in report["matches"]:
        rows.append({
            "title": m["title"], "kind": KIND_LABELS.get(m["doc_kind"], m["doc_kind"]),
            "section": m["section"] or "", "required": m["required"],
            "status": STATUS_LABELS.get(m["status"], m["status"]),
            "files": "; ".join(f.rsplit("/", 1)[-1] for f in m["files"]),
        })
    rows.sort(key=lambda r: (r["section"] or "я", r["title"]))
    return rows


def render_register(header: dict, rows: list[dict]) -> bytes:
    if not rows:
        raise ValueError("Комплект ИД пуст — сначала создайте его на вкладке «Состав комплекта»")
    h = header or {}
    p = [
        _para("РЕЕСТР ИСПОЛНИТЕЛЬНОЙ ДОКУМЕНТАЦИИ", bold=True, align="center"),
        _para(f"Объект: {_text(h.get('object_name'))}"),
        _para(f"Заказчик: {_text(h.get('customer'))}"),
        _para(f"Подрядчик: {_text(h.get('contractor'))}"),
        _para(f"Дата: {_date_words(h.get('date'))}"),
        _para(""),
    ]
    table = [["№", "Документ", "Вид", "Раздел", "Наличие", "Файл"]]
    for i, r in enumerate(rows, start=1):
        table.append([str(i), r["title"], r["kind"], r["section"] or "—", r["status"] + ("" if r["required"] else " (по желанию)"), r["files"] or "—"])
    p.append(_table(table))
    p.append(_para(""))
    p.append(_para(f"Представитель подрядчика: ____________ / {_text(h.get('contractor_rep'))} /"))
    p.append(_para(f"Представитель заказчика: ____________ / {_text(h.get('customer_rep'))} /"))
    return build_docx(p, landscape=True)


def render_register_xlsx(header: dict, rows: list[dict]) -> bytes:
    if not rows:
        raise ValueError("Комплект ИД пуст — сначала создайте его на вкладке «Состав комплекта»")
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Реестр ИД"
    ws.append([f"Реестр исполнительной документации. Объект: {(header or {}).get('object_name') or ''}"])
    ws.append(["№", "Документ", "Вид", "Раздел", "Наличие", "Обязательный", "Файл"])
    for i, r in enumerate(rows, start=1):
        ws.append([i, r["title"], r["kind"], r["section"], r["status"], "да" if r["required"] else "нет", r["files"]])
    for col, width in zip("ABCDEFG", (5, 60, 18, 14, 12, 14, 40)):
        ws.column_dimensions[col].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- акт комплексного опробования (72 часа) и акт ввода в эксплуатацию ----------

def _commission(h: dict) -> list[str]:
    out = [_para("Комиссия в составе:")]
    for label, key in (("Представитель заказчика", "customer_rep"), ("Представитель подрядчика", "contractor_rep"),
                       ("Представитель эксплуатирующей организации", "operator_rep")):
        out.append(_para(f"{label}: {_text(h.get(key))}"))
    return out


def _signatures(h: dict) -> list[str]:
    out = [_para("")]
    for label, key in (("Представитель заказчика", "customer_rep"), ("Представитель подрядчика", "contractor_rep"),
                       ("Представитель эксплуатирующей организации", "operator_rep")):
        out.append(_para(f"{label}: ____________ / {_text(h.get(key))} /"))
    return out


def render_trial_act(header: dict, systems: list[dict]) -> bytes:
    h = header or {}
    rows = [s for s in (systems or []) if (s.get("name") or "").strip()]
    if not rows:
        raise ValueError("Добавьте хотя бы одну систему или установку")
    if len(rows) > 200:
        raise ValueError("Слишком много строк (максимум 200)")
    ok = all((s.get("result") or "pass") == "pass" for s in rows)
    p = [
        _para(f"АКТ № {_text(h.get('act_no'))}", bold=True, align="center"),
        _para("комплексного опробования оборудования (испытания под нагрузкой)", bold=True, align="center"),
        _para(""),
        _table([[_text(h.get("place")), _date_words(h.get("date"))]], border=False, header_bold=False),
        _para(""),
        _para(f"Объект: {_text(h.get('object_name'))}"),
        _para(f"Заказчик: {_text(h.get('customer'))}"),
        _para(f"Подрядчик: {_text(h.get('contractor'))}"),
        _para(""),
        *_commission(h),
        _para(""),
        _para(
            f"составила настоящий акт о том, что с {_text(h.get('start'))} по {_text(h.get('end'))} "
            f"(продолжительность {_text(h.get('hours') or '72')} ч) проведено комплексное опробование оборудования "
            f"под нагрузкой по программе: {_text(h.get('program_ref'))}.",
        ),
        _para(""),
    ]
    table = [["№", "Система / установка", "Режим и нагрузка", "Результат"]]
    for i, s in enumerate(rows, start=1):
        table.append([str(i), s["name"].strip(), (s.get("mode") or "").strip() or "—",
                      "работа устойчивая, отказов нет" if (s.get("result") or "pass") == "pass" else "выявлены замечания"])
    p.append(_table(table))
    p += [
        _para(""),
        _para("Заключение:", bold=True),
        _para("Оборудование выдержало комплексное опробование, нарушений в работе не выявлено, оно может быть принято в эксплуатацию."
              if ok else "Выявлены замечания; после их устранения опробование повторить."),
        *_signatures(h),
    ]
    return build_docx(p)


def render_commissioning_act(header: dict) -> bytes:
    h = header or {}
    p = [
        _para(f"АКТ № {_text(h.get('act_no'))}", bold=True, align="center"),
        _para("ввода в эксплуатацию", bold=True, align="center"),
        _para(""),
        _table([[_text(h.get("place")), _date_words(h.get("date"))]], border=False, header_bold=False),
        _para(""),
        _para(f"Объект: {_text(h.get('object_name'))}"),
        _para(f"Адрес: {_text(h.get('address'))}"),
        _para(f"Заказчик: {_text(h.get('customer'))}"),
        _para(f"Подрядчик: {_text(h.get('contractor'))}"),
        _para(""),
        *_commission(h),
        _para(""),
        _para(
            "рассмотрела представленную исполнительную документацию, акты испытаний и комплексного опробования "
            f"(акт № {_text(h.get('trial_act_no'))} от {_date_words(h.get('trial_act_date'))}) и установила, что работы "
            "выполнены в соответствии с проектом и требованиями нормативных документов."
        ),
        _para(f"Оборудование и системы: {_text(h.get('scope'))}"),
        _para(""),
        _para("Решение комиссии:", bold=True),
        _para("Объект (оборудование, системы) принять и ввести в эксплуатацию."),
        _para(f"Гарантийный срок: {_text(h.get('warranty'))}."),
        *_signatures(h),
    ]
    return build_docx(p)
