"""Приказы на ответственных лиц (охрана труда, огневые работы и др.) с обязательной копией удостоверения."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acceptance_act_document import _MONTHS_GEN, _para, _table
from app.docx_kit import build_docx
from app.models.id_orders import IdOrder, IdOrderFile

MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
BLANK = "________________"

# kind -> (название приказа, «ответственным за ...», удостоверение, обязанности)
CATALOG: dict[str, dict] = {
    "works_manager": {
        "title": "О назначении ответственного за производство работ",
        "duty": "производство строительно-монтажных работ",
        "certificate": "Удостоверение (диплом, аттестат) о квалификации, подтверждающее право руководства работами",
        "duties": ["организацию и ведение работ в соответствии с проектной и рабочей документацией", "качество и сроки выполнения работ",
                   "ведение общего журнала работ и исполнительной документации", "допуск к работам только обученного персонала"],
    },
    "labor_safety": {
        "title": "О назначении ответственного за охрану труда",
        "duty": "охрану труда",
        "certificate": "Удостоверение о проверке знаний требований охраны труда",
        "duties": ["проведение инструктажей на рабочем месте и ведение журналов инструктажей",
                   "контроль применения средств индивидуальной защиты", "расследование несчастных случаев и микротравм",
                   "соблюдение требований охраны труда на объекте"],
    },
    "hot_work": {
        "title": "О назначении ответственного за безопасное проведение огневых работ",
        "duty": "организацию и безопасное проведение огневых работ",
        "certificate": "Удостоверение о проверке знаний по пожарной безопасности и допуску к огневым работам",
        "duties": ["оформление нарядов-допусков на огневые работы", "подготовку места огневых работ и наличие первичных средств пожаротушения",
                   "контроль места работ не менее 4 часов после их окончания", "допуск к огневым работам только лиц с удостоверениями"],
    },
    "electrical_safety": {
        "title": "О назначении ответственного за электробезопасность и электрохозяйство",
        "duty": "электробезопасность и исправное состояние электрохозяйства",
        "certificate": "Удостоверение о проверке знаний норм и правил работы в электроустановках (группа по электробезопасности)",
        "duties": ["допуск персонала к работам в электроустановках", "оформление нарядов-допусков и распоряжений",
                   "содержание электроустановок и электроинструмента в исправном состоянии", "ведение журнала учёта электрозащитных средств"],
    },
    "fire_safety": {
        "title": "О назначении ответственного за пожарную безопасность",
        "duty": "пожарную безопасность",
        "certificate": "Удостоверение о прохождении пожарно-технического минимума",
        "duties": ["соблюдение противопожарного режима на объекте", "содержание первичных средств пожаротушения и путей эвакуации",
                   "проведение противопожарных инструктажей", "действия при возникновении пожара"],
    },
    "work_at_height": {
        "title": "О назначении ответственного за безопасное проведение работ на высоте",
        "duty": "безопасное проведение работ на высоте",
        "certificate": "Удостоверение о проверке знаний по безопасным методам и приёмам работ на высоте",
        "duties": ["оформление нарядов-допусков на работы на высоте", "исправность и испытание лесов, подмостей и страховочных систем",
                   "допуск к работам лиц, прошедших обучение и медосмотр", "контроль применения страховочных систем"],
    },
    "quality_control": {
        "title": "О назначении ответственного за входной контроль и качество",
        "duty": "входной контроль материалов и контроль качества работ",
        "certificate": "Удостоверение о повышении квалификации (строительный контроль)",
        "duties": ["входной контроль материалов, изделий и оборудования", "ведение журнала входного контроля",
                   "освидетельствование скрытых работ совместно с заказчиком", "устранение замечаний строительного контроля"],
    },
    "documentation": {
        "title": "О назначении ответственного за ведение исполнительной документации",
        "duty": "ведение исполнительной документации и общего журнала работ",
        "certificate": "Документ, подтверждающий квалификацию или обучение (удостоверение, диплом)",
        "duties": ["своевременное оформление актов освидетельствования скрытых работ и исполнительных схем",
                   "ведение общего и специальных журналов работ", "сбор и комплектацию исполнительной документации для сдачи заказчику"],
    },
}


def catalog() -> list[dict]:
    return [{"kind": k, "title": v["title"], "certificate": v["certificate"]} for k, v in CATALOG.items()]


def _date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("Некорректная дата приказа") from exc


_FIELDS = ("number", "person_name", "position", "org_name", "object_name", "city", "signer_name", "signer_position", "extra")


def _clean(data: dict, *, partial: bool) -> dict:
    out: dict = {}
    if "kind" in data or not partial:
        kind = data.get("kind")
        if kind not in CATALOG:
            raise ValueError("Неизвестный вид приказа")
        out["kind"] = kind
    if "order_date" in data:
        out["order_date"] = _date(data.get("order_date"))
    for field in _FIELDS:
        if field in data:
            value = data.get(field)
            out[field] = (str(value).strip() or None) if value is not None else None
    return out


def _file_dict(f: IdOrderFile) -> dict:
    return {"id": f.id, "filename": f.filename, "mime_type": f.mime_type, "size_bytes": f.size_bytes}


def _order_dict(db: Session, o: IdOrder) -> dict:
    files = db.execute(
        select(IdOrderFile).where(IdOrderFile.order_id == o.id, IdOrderFile.file_kind == "certificate").order_by(IdOrderFile.id)
    ).scalars().all()
    spec = CATALOG[o.kind]
    missing = []
    if not (o.person_name or "").strip():
        missing.append("ФИО ответственного")
    if not files:
        missing.append("копия удостоверения")
    return {
        "id": o.id, "kind": o.kind, "title": spec["title"], "certificate_required": spec["certificate"],
        "number": o.number, "order_date": o.order_date.isoformat() if o.order_date else None,
        "person_name": o.person_name, "position": o.position, "org_name": o.org_name, "object_name": o.object_name,
        "city": o.city, "signer_name": o.signer_name, "signer_position": o.signer_position, "extra": o.extra,
        "certificates": [_file_dict(f) for f in files], "complete": not missing, "missing": missing,
    }


def list_orders(db: Session, project_id: int) -> list[dict]:
    rows = db.execute(select(IdOrder).where(IdOrder.project_id == project_id).order_by(IdOrder.id)).scalars().all()
    return [_order_dict(db, o) for o in rows]


def _get(db: Session, project_id: int, order_id: int) -> IdOrder:
    o = db.get(IdOrder, order_id)
    if o is None or o.project_id != project_id:
        raise LookupError("Приказ не найден")
    return o


def create_order(db: Session, project_id: int, data: dict) -> dict:
    o = IdOrder(project_id=project_id, **_clean(data, partial=False))
    db.add(o)
    db.flush()
    return _order_dict(db, o)


def update_order(db: Session, project_id: int, order_id: int, data: dict) -> dict:
    o = _get(db, project_id, order_id)
    for k, v in _clean(data, partial=True).items():
        setattr(o, k, v)
    db.flush()
    return _order_dict(db, o)


def delete_order(db: Session, project_id: int, order_id: int) -> None:
    o = _get(db, project_id, order_id)
    for f in db.execute(select(IdOrderFile).where(IdOrderFile.order_id == o.id)).scalars().all():
        db.delete(f)
    db.delete(o)
    db.flush()


def add_certificate(db: Session, project_id: int, order_id: int, filename: str, mime: str | None, data: bytes) -> dict:
    o = _get(db, project_id, order_id)
    if not data:
        raise ValueError("Пустой файл")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Файл больше 10 МБ")
    mime = (mime or "").split(";")[0].strip().lower()
    if mime not in ALLOWED_MIME:
        raise ValueError("Допустимы PDF, JPG, PNG или WEBP")
    name = re.sub(r"[\\/\x00-\x1f]", "_", (filename or "удостоверение").strip())[:300] or "удостоверение"
    f = IdOrderFile(project_id=project_id, order_id=o.id, file_kind="certificate", filename=name,
                    mime_type=mime, size_bytes=len(data), data=data)
    db.add(f)
    db.flush()
    return _order_dict(db, o)


def get_certificate(db: Session, project_id: int, order_id: int, file_id: int) -> IdOrderFile:
    _get(db, project_id, order_id)
    f = db.get(IdOrderFile, file_id)
    if f is None or f.order_id != order_id:
        raise LookupError("Файл не найден")
    return f


def delete_certificate(db: Session, project_id: int, order_id: int, file_id: int) -> dict:
    f = get_certificate(db, project_id, order_id, file_id)
    db.delete(f)
    db.flush()
    return _order_dict(db, _get(db, project_id, order_id))


def _t(value) -> str:
    return (str(value).strip() if value else "") or BLANK


def _date_words(d: date | None) -> str:
    return f"«{d.day:02d}» {_MONTHS_GEN[d.month - 1]} {d.year} г." if d else "«___» __________ 20__ г."


def render_docx(o: IdOrder) -> bytes:
    spec = CATALOG[o.kind]
    person = _t(o.person_name)
    p: list[str] = []
    p.append(_para(_t(o.org_name), bold=True, align="center"))
    p.append(_para(""))
    p.append(_para("ПРИКАЗ", bold=True, align="center"))
    p.append(_para(f"№ {_t(o.number)}", align="center"))
    p.append(_table([[_t(o.city), _date_words(o.order_date)]], border=False, header_bold=False))
    p.append(_para(""))
    p.append(_para(spec["title"], bold=True, align="center"))
    p.append(_para(""))
    p.append(_para(
        f"В целях обеспечения требований законодательства и нормативных документов при выполнении работ на объекте «{_t(o.object_name)}»"
    ))
    p.append(_para(""))
    p.append(_para("ПРИКАЗЫВАЮ:", bold=True))
    p.append(_para(f"1. Назначить {_t(o.position)} {person} ответственным за {spec['duty']} на объекте «{_t(o.object_name)}»."))
    p.append(_para("2. Возложить на ответственного обязанности:"))
    for i, d in enumerate(spec["duties"], start=1):
        p.append(_para(f"2.{i}. {d[0].upper()}{d[1:]};" if i < len(spec["duties"]) else f"2.{i}. {d[0].upper()}{d[1:]}."))
    p.append(_para(f"3. Ответственный обязан иметь действующий документ: {spec['certificate'].lower()}. Копия документа прилагается к настоящему приказу."))
    if o.extra:
        p.append(_para(f"4. {o.extra}"))
        p.append(_para("5. Контроль за исполнением приказа оставляю за собой."))
    else:
        p.append(_para("4. Контроль за исполнением приказа оставляю за собой."))
    p.append(_para(""))
    p.append(_para(f"{_t(o.signer_position)}    ____________ / {_t(o.signer_name)} /"))
    p.append(_para(""))
    p.append(_para("С приказом ознакомлен(а):"))
    p.append(_para(f"{person}    ____________    «___» __________ 20__ г."))
    p.append(_para(""))
    p.append(_para("Приложение: копия удостоверения (прилагается)."))
    return build_docx(p)


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip()[:80] or "file"


def package_zip(db: Session, project_id: int) -> bytes:
    """Комплект: приказы в Word и копии удостоверений. Пока у каждого приказа нет копии, комплект не формируется."""
    orders = db.execute(select(IdOrder).where(IdOrder.project_id == project_id).order_by(IdOrder.id)).scalars().all()
    if not orders:
        raise ValueError("Приказов пока нет")
    problems = []
    for o in orders:
        info = _order_dict(db, o)
        if not info["complete"]:
            problems.append(f"«{info['title']}» ({info['person_name'] or 'без ФИО'}): нет — {', '.join(info['missing'])}")
    if problems:
        raise ValueError("Комплект не готов. " + "; ".join(problems))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, o in enumerate(orders, start=1):
            base = f"{idx:02d}_{_safe(CATALOG[o.kind]['title'])}_{_safe(o.person_name or '')}"
            zf.writestr(f"{base}/Приказ.docx", render_docx(o))
            files = db.execute(select(IdOrderFile).where(IdOrderFile.order_id == o.id, IdOrderFile.file_kind == "certificate")
                               .order_by(IdOrderFile.id)).scalars().all()
            for n, f in enumerate(files, start=1):
                zf.writestr(f"{base}/Удостоверение_{n}_{_safe(f.filename)}", f.data)
    return buf.getvalue()
