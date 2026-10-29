"""Акт индивидуального испытания смонтированного оборудования (Word)."""
from __future__ import annotations

from datetime import date

from app.acceptance_act_document import _MONTHS_GEN, _para, _table
from app.docx_kit import build_docx

BLANK = "________________"
RESULTS = {"pass": "выдержало испытание", "fail": "не выдержало испытание"}


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


def validate(header: dict, items: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for idx, item in enumerate(items or [], start=1):
        name = (item.get("name") or "").strip()
        if not name:
            if any(str(v).strip() for v in item.values() if v is not None):
                raise ValueError(f"Строка {idx}: укажите наименование оборудования")
            continue
        result = (item.get("result") or "pass").strip().lower()
        if result not in RESULTS:
            raise ValueError(f"Строка {idx}: результат должен быть pass или fail")
        rows.append({
            "name": name, "type_mark": (item.get("type_mark") or "").strip(),
            "serial": (item.get("serial") or "").strip(), "location": (item.get("location") or "").strip(),
            "program": (item.get("program") or "").strip(), "values": (item.get("values") or "").strip(),
            "result": result,
        })
    if not rows:
        raise ValueError("Добавьте хотя бы одно оборудование")
    if len(rows) > 200:
        raise ValueError("Слишком много строк (максимум 200)")
    return rows


def render(header: dict, items: list[dict]) -> bytes:
    rows = validate(header, items)
    h = header or {}
    all_ok = all(r["result"] == "pass" for r in rows)
    p: list[str] = []
    p.append(_para(f"АКТ № {_text(h.get('act_no'))}", bold=True, align="center"))
    p.append(_para("индивидуального испытания смонтированного оборудования", bold=True, align="center"))
    p.append(_para(""))
    p.append(_table([[_text(h.get("place")), _date_words(h.get("date"))]], border=False, header_bold=False))
    p.append(_para(""))
    p.append(_para(f"Объект: {_text(h.get('object_name'))}"))
    p.append(_para(f"Адрес: {_text(h.get('address'))}"))
    p.append(_para(f"Заказчик: {_text(h.get('customer'))}"))
    p.append(_para(f"Подрядчик (монтажная организация): {_text(h.get('contractor'))}"))
    p.append(_para(""))
    p.append(_para("Комиссия в составе:"))
    for label, key in (("Представитель заказчика", "customer_rep"), ("Представитель подрядчика", "contractor_rep"),
                       ("Представитель эксплуатирующей организации", "operator_rep")):
        p.append(_para(f"{label}: {_text(h.get(key))}"))
    p.append(_para(""))
    p.append(_para(
        "составила настоящий акт о том, что смонтированное оборудование прошло индивидуальные испытания "
        f"по программе: {_text(h.get('program_ref'))}.",
    ))
    p.append(_para(""))
    table = [["№ п/п", "Оборудование", "Тип, марка", "Заводской №", "Место установки", "Проведено", "Результат"]]
    for i, r in enumerate(rows, start=1):
        done = r["program"] or "—"
        if r["values"]:
            done = f"{done}; {r['values']}" if r["program"] else r["values"]
        table.append([str(i), r["name"], r["type_mark"] or "—", r["serial"] or "—", r["location"] or "—", done, RESULTS[r["result"]]])
    p.append(_table(table))
    p.append(_para(""))
    p.append(_para("Заключение:", bold=True))
    p.append(_para(
        "Оборудование смонтировано в соответствии с проектом и требованиями нормативных документов, "
        "индивидуальные испытания выдержало и может быть принято для комплексного опробования."
        if all_ok else
        "Часть оборудования не выдержала испытания. Выявленные замечания подлежат устранению с повторным испытанием.",
    ))
    p.append(_para(""))
    for label, key in (("Представитель заказчика", "customer_rep"), ("Представитель подрядчика", "contractor_rep"),
                       ("Представитель эксплуатирующей организации", "operator_rep")):
        p.append(_para(f"{label}: ____________ / {_text(h.get(key))} /"))
    return build_docx(p)
