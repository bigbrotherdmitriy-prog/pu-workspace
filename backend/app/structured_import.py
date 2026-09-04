from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation


SOURCE_COORDINATE_MARKER = "__PU_SOURCE_COORD__"


HEADER_ALIASES = {
    "title": ("наименование", "работа", "этап", "описание", "назначение платежа"),
    "category": ("категория", "статья", "раздел"),
    "planned_start": ("дата начала", "начало", "старт"),
    "planned_finish": ("дата окончания", "окончание", "завершение", "срок"),
    "planned_date": ("дата платежа", "плановая дата", "срок оплаты", "дата"),
    "amount": ("плановая сумма", "сумма", "стоимость", "итого"),
    "counterparty": ("контрагент", "поставщик", "получатель", "плательщик"),
    "object_name": ("объект", "площадка", "город"),
    "note": ("описание операции", "назначение", "комментарий", "примечание"),
    "direction": ("направление", "приход расход", "тип платежа", "тип операции", "операция"),
    "progress": ("плановый процент", "прогресс", "готовность"),
}


def _normalized(value: object) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold()).strip()


def _field_for_header(header: str) -> str | None:
    normalized = _normalized(header)
    for field, aliases in HEADER_ALIASES.items():
        if any(alias == normalized or alias in normalized for alias in aliases):
            return field
    return None


def _date(value: str) -> str | None:
    raw = value.strip()
    if re.fullmatch(r"\d+(?:\.0+)?", raw):
        serial = int(float(raw))
        if 1 <= serial <= 100_000:
            return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _amount(value: str) -> str | None:
    raw = re.sub(r"[^0-9,.-]", "", value.replace(" ", ""))
    if raw.count(",") == 1 and raw.count(".") == 0:
        raw = raw.replace(",", ".")
    try:
        return str(Decimal(raw)) if raw else None
    except InvalidOperation:
        return None


def _direction(value: str) -> str | None:
    normalized = _normalized(value)
    if any(word in normalized for word in ("расход", "выплата", "списание", "исход")):
        return "outflow"
    if any(word in normalized for word in ("приход", "поступление", "зачисление", "вход")):
        return "inflow"
    return None


def _coordinate(cells: list[str], fallback_row: int) -> tuple[list[str], str | None, int]:
    if cells and cells[-1].startswith(f"{SOURCE_COORDINATE_MARKER}:"):
        marker = cells[-1][len(SOURCE_COORDINATE_MARKER) + 1:]
        sheet, separator, row = marker.rpartition(":")
        if separator and row.isdigit():
            return cells[:-1], sheet or None, int(row)
    return cells, None, fallback_row


def parse_structured_rows(content: str, kind: str, limit: int = 500, source_name: str | None = None) -> dict:
    """Parse CSV/TSV extracted from a spreadsheet into reviewable proposals."""
    sample = content[:20_000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
    matrix = list(csv.reader(io.StringIO(content), dialect))
    matrix = [(*_coordinate(row, source_line), source_line) for source_line, row in enumerate(matrix, start=1) if any(cell.strip() for cell in row)]
    if not matrix:
        return {"headers": [], "mapping": {}, "rows": [], "issues": ["Таблица пуста"]}

    header_index = 0
    best_mapping: dict[int, str] = {}
    for index, (row, _sheet, _actual_row, _source_line) in enumerate(matrix[:500]):
        mapping = {column: field for column, cell in enumerate(row) if (field := _field_for_header(cell))}
        if len(mapping) > len(best_mapping):
            header_index, best_mapping = index, mapping
    headers, header_sheet, _header_row, _header_line = matrix[header_index]
    issues = []
    required = {"schedule": {"title"}, "budget": {"title", "amount"}, "cash-flow": {"title", "amount", "planned_date"}}[kind]
    mapped_fields = set(best_mapping.values())
    missing = sorted(required - mapped_fields)
    if missing:
        issues.append("Не распознаны обязательные колонки: " + ", ".join(missing))

    rows = []
    candidates = matrix[header_index + 1:]
    if header_sheet:
        candidates = [candidate for candidate in candidates if candidate[1] == header_sheet]
    for cells, source_sheet, source_row, source_line in candidates[:limit]:
        raw = {field: cells[column].strip() for column, field in best_mapping.items() if column < len(cells) and cells[column].strip()}
        title = raw.get("title", "")
        amount = _amount(raw.get("amount", ""))
        planned_date = _date(raw.get("planned_date", ""))
        planned_start = _date(raw.get("planned_start", ""))
        planned_finish = _date(raw.get("planned_finish", ""))
        row_issues = []
        if not title:
            row_issues.append("нет наименования")
        if kind in {"budget", "cash-flow"} and amount is None:
            row_issues.append("не распознана сумма")
        if kind == "cash-flow" and planned_date is None:
            row_issues.append("не распознана дата")
        rows.append({
            "source_row": source_row,
            "source_sheet": source_sheet,
            "source_line": source_line,
            "source_coordinate": f"{source_sheet}!{source_row}" if source_sheet else f"строка {source_row}",
            "source_name": source_name,
            "title": title,
            "category": raw.get("category") or "Прочее",
            "planned_start": planned_start,
            "planned_finish": planned_finish,
            "planned_date": planned_date,
            "amount": amount,
            "counterparty": raw.get("counterparty"),
            "object_name": raw.get("object_name"),
            "note": raw.get("note") or title,
            "direction": _direction(raw.get("direction", "")),
            "progress": float(_amount(raw.get("progress", "")) or 0),
            "issues": row_issues,
            "importable": not row_issues,
            "excerpt": " | ".join(cells)[:2000],
        })
    return {
        "headers": headers,
        "mapping": {headers[column]: field for column, field in best_mapping.items() if column < len(headers)},
        "rows": rows,
        "issues": issues,
        "truncated": len(candidates) > limit,
    }
