"""Разбор сметы из Excel (.xlsx) в список позиций.

Модуль использует только стандартную библиотеку и openpyxl; колонки
определяются по синонимам заголовков (без регистра, подстрока).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

from openpyxl import load_workbook


# Поле -> подстроки заголовка в нижнем регистре (ё нормализована в е).
COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "position": ("№", "n п/п", "№ п/п", "номер"),
    "code": ("шифр", "обоснован", "код"),
    "name": ("наименование",),
    "unit": ("ед. изм", "единица", "ед.изм"),
    "qty": ("колич", "объём", "объем"),
    "unit_price": (
        "цена",
        "стоимость ед",
        "стоимость единицы",
        "на единицу",
    ),
    "total": (
        "всего",
        "общая стоимость",
        "стоимость, руб",
        "сумма",
    ),
}

# Маркеры-заголовки строки-раздела.
_SECTION_PREFIXES = ("раздел", "этап", "глава", "локальн", "объект")

# Маркеры итоговых строк.
_TOTAL_PREFIXES = (
    "итого",
    "всего по",
    "в том числе",
    "накладные",
    "сметная прибыль",
    "ндс",
    "справочно",
    "составил",
    "проверил",
)

# Строки, из которых берём сумму в top-level total.
_GRAND_TOTAL_PREFIXES = ("всего по смете", "итого по смете")

_MAX_LINES = 5000
_HEADER_SCAN_LIMIT = 40


def _norm(value: Any) -> str:
    return str(value or "").lower().replace("ё", "е")


def _cell(row: list[Any], idx: int) -> Any:
    if 0 <= idx < len(row):
        return row[idx]
    return None


def _cell_str(row: list[Any], idx: int) -> str:
    v = _cell(row, idx)
    if v is None:
        return ""
    return str(v).strip()


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    s = str(value).strip().replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _is_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    s = str(value).strip().replace(",", ".")
    if not s:
        return False
    try:
        Decimal(s)
        return True
    except InvalidOperation:
        return False


def _is_numeric_str(value: Any) -> bool:
    return _is_number(value)


def _to_str(d: Decimal | None) -> str | None:
    if d is None:
        return None
    # Decimal без экспоненты, нормализованный.
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s == "" or s == "-":
        return "0"
    return s


def _row_title(row: list[Any], idx: int) -> str:
    return _norm(_cell_str(row, idx))


def _find_col(row: list[str], synonyms: tuple[str, ...], exclude: set[int]) -> int | None:
    for syn in synonyms:
        for i, val in enumerate(row):
            if i in exclude:
                continue
            if syn in _norm(val):
                return i
    return None


def _detect_columns(header_rows: list[list[str]]) -> dict[str, int]:
    # Объединим 1–3 строки шапки в одну (поколоночно).
    n = max((len(r) for r in header_rows), default=0)
    merged: list[str] = []
    for i in range(n):
        parts: list[str] = []
        for r in header_rows:
            v = _cell_str(r, i)
            if v:
                parts.append(v)
        merged.append(" ".join(parts))

    used: set[int] = set()
    cols: dict[str, int] = {}

    # Порядок важен: сначала более специфичные названия.
    for field in (
        "position",
        "code",
        "name",
        "unit",
        "qty",
        "unit_price",
        "total",
    ):
        syn = COLUMN_SYNONYMS[field]
        idx = _find_col(merged, syn, used)
        if idx is not None:
            cols[field] = idx
            used.add(idx)
    return cols


def _row_is_header_candidate(row: list[Any]) -> bool:
    has_name = False
    has_qty = False
    for cell in row:
        h = _norm(cell)
        if "наименование" in h:
            has_name = True
        if "колич" in h or "объем" in h or "объём" in h:
            has_qty = True
    return has_name and has_qty


def _sheet_values(ws) -> list[list[Any]]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _find_header(
    rows: list[list[Any]],
) -> tuple[int, int, dict[str, int]] | None:
    """Найти (first_header_row, last_header_row, columns)."""
    for r in range(min(len(rows), _HEADER_SCAN_LIMIT)):
        if not _row_is_header_candidate(rows[r]):
            continue
        # Шапка может занимать до 3 строк: добавляем следующие строки, пока они выглядят как заголовки (без чисел).
        header_rows = [list(map(str, [c if c is not None else "" for c in rows[r]]))]
        last = r
        for k in (1, 2):
            if r + k >= len(rows):
                break
            nxt = rows[r + k]
            if any(isinstance(c, (int, float)) and not isinstance(c, bool) for c in nxt):
                break
            words = " ".join(_norm(c) for c in nxt if c is not None)
            if not any(w in words for w in ("ед", "цен", "всего", "шифр", "№", "стоимость", "колич", "наименование", "сумма")):
                break
            header_rows.append(list(map(str, [c if c is not None else "" for c in nxt])))
            last = r + k
        cols = _detect_columns(header_rows)
        if "name" in cols and "qty" in cols:
            return r, last, cols
        # Если ни один вариант не дал name+qty, идём дальше.
    return None


def _is_section_title(
    row: list[Any], cols: dict[str, int], name_idx: int
) -> bool:
    name = _cell_str(row, name_idx)
    if not name:
        return False
    low = _norm(name)
    if any(low.startswith(p) for p in _SECTION_PREFIXES):
        return True
    # Полностью жирные строки — трудно определить без стилей; берём только
    # эвристику: name непустое и нет числовых колонок (qty).
    return False


def _empty_except(row: list[Any], name_idx: int) -> bool:
    for i, v in enumerate(row):
        if i == name_idx:
            continue
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return False
    return True


def _is_section_row(row: list[Any], cols: dict[str, int], name_idx: int) -> bool:
    if not _empty_except(row, name_idx):
        # Даже если рядом есть данные, не считаем строкой-разделом —
        # раздел должен быть изолирован.
        # Но у Гранд-Сметы бывает, что в строке раздела что-то есть в №.
        pos_idx = cols.get("position")
        if pos_idx is None:
            return False
        # Позиция — не число (например «Раздел 1»)
        pos_val = _cell(row, pos_idx)
        if _is_number(pos_val):
            return False
        name = _cell_str(row, name_idx)
        if not name:
            return False
        # имя непустое, а qty нет числа
        qty_idx = cols.get("qty")
        if qty_idx is not None and _is_number(_cell(row, qty_idx)):
            return False
        low = _norm(name)
        return any(low.startswith(p) for p in _SECTION_PREFIXES)
    name = _cell_str(row, name_idx)
    low = _norm(name)
    return any(low.startswith(p) for p in _SECTION_PREFIXES)


def _same_level(previous: str, title: str) -> bool:
    """«Раздел 2» после «Раздел 1» заменяет его, а не вкладывается."""
    a, b = _norm(previous), _norm(title)
    for prefix in _SECTION_PREFIXES:
        if a.startswith(prefix) and b.startswith(prefix):
            return True
    return False


def _is_total_row_name(name: str) -> bool:
    low = _norm(name)
    return any(low.startswith(p) for p in _TOTAL_PREFIXES)


def _is_grand_total_name(name: str) -> bool:
    low = _norm(name)
    return any(low.startswith(p) for p in _GRAND_TOTAL_PREFIXES)


def _extract_unit_tail(name: str) -> tuple[str, str]:
    """Если в хвосте наименования есть «, ед» — вынести в unit."""
    s = name.strip()
    if "," not in s:
        return s, ""
    head, _, tail = s.rpartition(",")
    tail_clean = tail.strip()
    if not tail_clean:
        return s, ""
    low = tail_clean.lower()
    # Допустимые единицы: 100 м, м2, м², шт, 100 шт, кг, т, м3, м³, м, чел-ч и т.п.
    unit_markers = (
        "м2",
        "м²",
        "м3",
        "м³",
        "шт",
        "кг",
        "т",
        "чел",
        "м",
        "га",
        "л",
        "км",
        "см",
        "мм",
        "уп",
        "компл",
    )
    # Разрешим «100 м», «10 м2» и т.п.
    candidates = []
    tokens = low.replace("²", "2").replace("³", "3").split()
    if tokens:
        candidates.append(tokens[-1])
    for tok in candidates:
        if any(tok == m or tok.startswith(m) for m in unit_markers):
            return head.rstrip(), tail_clean
    return s, ""


def _normalize_unit(unit: str) -> str:
    s = (unit or "").strip()
    if not s:
        return ""
    s = s.replace("м²", "м2").replace("м³", "м3")
    s = s.lower()
    # убрать точку на конце
    s = s.rstrip(".")
    return s


def _apply_math(
    line: dict[str, Any], sort_order: int, warnings: list[str]
) -> None:
    qty = _parse_decimal(line.get("qty")) if line.get("qty") is not None else None
    price = _parse_decimal(line.get("unit_price")) if line.get("unit_price") is not None else None
    total = _parse_decimal(line.get("total")) if line.get("total") is not None else None

    if price is None and total is not None and qty is not None and qty > 0:
        price = (total / qty).quantize(Decimal("0.01"))
        line["unit_price"] = _to_str(price)

    if total is None and price is not None and qty is not None:
        total = (price * qty).quantize(Decimal("0.01"))
        line["total"] = _to_str(total)

    if qty is not None and price is not None and total is not None and total != 0:
        expected = qty * price
        # расхождение > 1% от total
        if expected != 0:
            diff = abs(expected - total)
            if diff / abs(total) > Decimal("0.01"):
                label = line.get("position") or str(sort_order)
                warnings.append(
                    f"Позиция {label}: сумма не сходится с объёмом × ценой"
                )


def parse_estimate_xlsx(data: bytes) -> dict:
    wb = load_workbook(BytesIO(data), data_only=True)

    sheets = wb.worksheets if hasattr(wb, "worksheets") else list(wb._sheets)  # type: ignore[attr-defined]

    detected_sheet_name: str | None = None
    header_first: int | None = None
    header_last: int | None = None
    columns: dict[str, int] | None = None
    rows: list[list[Any]] | None = None

    for ws in sheets:
        ws_rows = _sheet_values(ws)
        found = _find_header(ws_rows)
        if found is not None:
            header_first, header_last, columns = found
            detected_sheet_name = ws.title
            rows = ws_rows
            break

    if rows is None or header_last is None or columns is None or detected_sheet_name is None:
        raise ValueError(
            "Не удалось найти таблицу сметы: нужны колонки «Наименование» и "
            "«Количество» (или «Объём»)"
        )

    if header_first is None:
        raise ValueError(
            "Не удалось найти таблицу сметы: нужны колонки «Наименование» и "
            "«Количество» (или «Объём»)"
        )

    name_idx = columns["name"]
    qty_idx = columns["qty"]
    pos_idx = columns.get("position")
    code_idx = columns.get("code")
    unit_idx = columns.get("unit")
    price_idx = columns.get("unit_price")
    total_idx = columns.get("total")

    lines: list[dict[str, Any]] = []
    warnings: list[str] = []
    section_path: list[str] = []
    grand_total: str | None = None
    sort_order = 0

    for r in range(header_last + 1, len(rows)):
        row = rows[r]
        name = _cell_str(row, name_idx)
        if not name:
            # Заголовок раздела может стоять в любой колонке (например, в «№»), остальные пусты.
            texts = [str(v).strip() for v in row if v is not None and str(v).strip() and not isinstance(v, (int, float))]
            if texts and _is_total_row_name(texts[0]):
                if _is_grand_total_name(texts[0]) and total_idx is not None:
                    t = _parse_decimal(_cell(row, total_idx))
                    if t is not None:
                        grand_total = _to_str(t)
                continue
            if len(texts) == 1 and any(_norm(texts[0]).startswith(p_) for p_ in _SECTION_PREFIXES):
                title = " ".join(texts[0].split())
                if section_path and _same_level(section_path[-1], title):
                    section_path[-1] = title
                else:
                    section_path.append(title)
            continue

        # Итоговые строки — пропускаем.
        if _is_total_row_name(name):
            if _is_grand_total_name(name) and total_idx is not None:
                t = _parse_decimal(_cell(row, total_idx))
                if t is not None:
                    grand_total = _to_str(t)
            continue

        # Строка-раздел?
        if _is_section_row(row, columns, name_idx):
            title = " ".join(name.split())
            if section_path and _same_level(section_path[-1], title):
                section_path[-1] = title
            else:
                section_path.append(title)
            continue

        # Позиция: нужен номер в qty.
        qty_val = _cell(row, qty_idx)
        if not _is_number(qty_val):
            continue

        # Дополнительная проверка: строка-позиция должна иметь осмысленное
        # присутствие хотя бы одной из колонок данных (price/total) или
        # позицию/код. Иначе это может быть «шум».
        position_str = _cell_str(row, pos_idx) if pos_idx is not None else ""

        qty = _parse_decimal(qty_val)
        unit_price = _parse_decimal(_cell(row, price_idx)) if price_idx is not None else None
        total = _parse_decimal(_cell(row, total_idx)) if total_idx is not None else None

        unit_str = _cell_str(row, unit_idx) if unit_idx is not None else ""
        cleaned_name = name
        if not unit_str:
            cleaned_name, tail_unit = _extract_unit_tail(name)
            if tail_unit:
                unit_str = tail_unit

        code_str = _cell_str(row, code_idx) if code_idx is not None else ""

        sort_order += 1
        if sort_order > _MAX_LINES:
            raise ValueError("Слишком большая смета")

        line: dict[str, Any] = {
            "sort_order": sort_order,
            "section": " / ".join(section_path),
            "position": position_str,
            "code": code_str,
            "name": " ".join(cleaned_name.split()),
            "unit": _normalize_unit(unit_str),
            "qty": _to_str(qty),
            "unit_price": _to_str(unit_price),
            "total": _to_str(total),
            "note": "",
        }
        _apply_math(line, sort_order, warnings)
        lines.append(line)

    detected = {
        "sheet": detected_sheet_name,
        "header_row": header_first,
        "columns": dict(columns),
    }

    return {
        "lines": lines,
        "warnings": warnings,
        "detected": detected,
        "total": grand_total,
    }
