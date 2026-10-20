"""Универсальный разбор спецификации оборудования, изделий и материалов
(форма ГОСТ 21.110) из Excel или текста PDF.

Модуль использует только стандартную библиотеку и не привязан к конкретному
проекту: колонки определяются по синонимам заголовков.
"""

from __future__ import annotations

import re

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .cable_journal import _join_rows, _looks_like_data, rows_from_tsv


# Поле -> подстроки заголовка в нижнем регистре (ё уже нормализована в е).
COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "pos": (
        "поз",
        "позиция",
        "№ п/п",
    ),
    "name": (
        "наименование и техническая характеристика",
        "наименование",
    ),
    "type": (
        "тип, марка",
        "тип",
        "марка",
        "обозначение документа",
    ),
    "code": (
        "код оборудования",
        "код продукции",
        "код",
    ),
    "maker": (
        "завод-изготовитель",
        "изготовитель",
        "поставщик",
    ),
    "unit": (
        "единица измерения",
        "ед. изм",
        "ед.",
    ),
    "qty": (
        "кол.",
        "количество",
        "кол-во",
    ),
    "mass": (
        "масса",
    ),
    "note": (
        "примечание",
    ),
}


def _norm(value: str) -> str:
    return (value or "").lower().replace("ё", "е")


def _cell(row: list[str], idx: int) -> str:
    if 0 <= idx < len(row):
        return row[idx] or ""
    return ""


def _matches(header: str, synonyms: Iterable[str]) -> bool:
    h = _norm(header)
    return any(syn in h for syn in synonyms)


def _find_first(col_values: list[str], synonyms: Iterable[str], exclude: set[int]) -> int | None:
    syn_list = list(synonyms)
    for syn in syn_list:
        for i, val in enumerate(col_values):
            if i in exclude:
                continue
            if syn in _norm(val):
                return i
    return None


def _header_search_row(row: list[str]) -> dict[str, Any]:
    """Определить колонки по одной строке (возможно, уже объединённой)."""
    used: set[int] = set()

    name = _find_first(row, COLUMN_SYNONYMS["name"], used)
    if name is None:
        return {}
    used.add(name)

    qty = _find_first(row, COLUMN_SYNONYMS["qty"], used)
    if qty is None:
        return {}
    used.add(qty)

    pos = _find_first(row, COLUMN_SYNONYMS["pos"], used)
    if pos is not None:
        used.add(pos)

    type_ = _find_first(row, COLUMN_SYNONYMS["type"], used)
    if type_ is not None:
        used.add(type_)

    code = _find_first(row, COLUMN_SYNONYMS["code"], used)
    if code is not None:
        used.add(code)

    maker = _find_first(row, COLUMN_SYNONYMS["maker"], used)
    if maker is not None:
        used.add(maker)

    unit = _find_first(row, COLUMN_SYNONYMS["unit"], used)
    if unit is not None:
        used.add(unit)

    mass = _find_first(row, COLUMN_SYNONYMS["mass"], used)
    if mass is not None:
        used.add(mass)

    note = _find_first(row, COLUMN_SYNONYMS["note"], used)
    if note is not None:
        used.add(note)

    fields: dict[str, int] = {"name": name, "qty": qty}
    if pos is not None:
        fields["pos"] = pos
    if type_ is not None:
        fields["type"] = type_
    if code is not None:
        fields["code"] = code
    if maker is not None:
        fields["maker"] = maker
    if unit is not None:
        fields["unit"] = unit
    if mass is not None:
        fields["mass"] = mass
    if note is not None:
        fields["note"] = note

    return fields


def find_spec_header(rows: list[list[str]]) -> tuple[int, dict] | None:
    """Найти строку шапки спецификации и индексы колонок.

    Возвращает (индекс_строки_шапки, {field: idx}) или None.
    """
    if not rows:
        return None

    n_rows = len(rows)
    for r in range(min(n_rows, 200)):
        base = _header_search_row(rows[r])
        if base:
            return r, base
        # Склейка двух строк, если следующая — не данные.
        if r + 1 < n_rows and not _looks_like_data(rows[r + 1]):
            merged = _join_rows(rows[r], rows[r + 1])
            base = _header_search_row(merged)
            if base:
                return r, base
    return None


def _is_column_number_row(row: list[str]) -> bool:
    """Строка-номера колонок: подряд идущие целые 1..N."""
    values: list[int] = []
    for cell in row:
        s = (cell or "").strip()
        if not s:
            continue
        if not re.fullmatch(r"\d+", s):
            return False
        values.append(int(s))
    if len(values) < 2:
        return False
    return values == list(range(1, len(values) + 1))


def _parse_decimal(value: str) -> Decimal | None:
    s = (value or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _get(row: list[str], idx: int | None) -> str:
    if idx is None:
        return ""
    if 0 <= idx < len(row):
        return (row[idx] or "").strip()
    return ""


def _row_all_empty(row: list[str]) -> bool:
    return not any((c or "").strip() for c in row)


def parse_specification(rows: list[list[str]]) -> list[dict]:
    """Разобрать строки спецификации в список позиций."""
    if not rows:
        return []

    header = find_spec_header(rows)
    if header is None:
        return []

    header_idx, cols = header
    pos_idx = cols.get("pos")
    name_idx = cols.get("name")
    type_idx = cols.get("type")
    code_idx = cols.get("code")
    maker_idx = cols.get("maker")
    unit_idx = cols.get("unit")
    qty_idx = cols.get("qty")
    note_idx = cols.get("note")

    if name_idx is None or qty_idx is None:
        return []

    result: list[dict] = []
    section = ""

    for row in rows[header_idx + 1:]:
        if _row_all_empty(row):
            continue
        if _is_column_number_row(row):
            continue

        pos = _get(row, pos_idx)
        name = _get(row, name_idx)
        type_ = _get(row, type_idx)
        code = _get(row, code_idx)
        maker = _get(row, maker_idx)
        unit = _get(row, unit_idx)
        qty_raw = _get(row, qty_idx)
        note = _get(row, note_idx)

        if not name and not pos and not qty_raw and not unit and not type_ \
                and not code and not maker and not note:
            continue

        # Нет количества и единицы: либо раздел («1. Оборудование»), либо
        # продолжение наименования предыдущей позиции («в составе: …»).
        if name and not qty_raw and not unit:
            is_section = bool(re.match(r"^\d+(?:\.\d+)*\.?\s+\S", name)) or not result
            if is_section or pos:
                section = name
            else:
                result[-1]["name"] = f"{result[-1]['name']} {name}".strip()
            continue

        # Позиция: должна быть pos или qty.
        if not pos and not qty_raw:
            continue

        qty = _parse_decimal(qty_raw)
        item = {
            "pos": pos,
            "name": name,
            "type": type_,
            "code": code,
            "maker": maker,
            "unit": unit,
            "qty": qty,
            "note": note,
            "section": section,
        }

        # Продолжение наименования: есть только name (без pos и qty) —
        # приклеиваем к предыдущей позиции.
        if not pos and not qty_raw and result:
            prev = result[-1]
            extra = name.strip()
            if extra:
                prev["name"] = (prev["name"] + " " + extra).strip()
            continue

        if not name and not pos:
            continue

        result.append(item)

    return result


def rows_from_layout_text(text: str) -> list[list[str]]:
    """Разобрать текст `pdftotext -layout` в матрицу строк."""
    if not text:
        return []
    result: list[list[str]] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            result.append([])
            continue
        cells = re.split(r"\s{2,}", stripped)
        result.append(cells)
    return result


_UNIT_PIECES = {"шт", "шт.", "компл", "компл.", "к-т", "комплект"}


def _is_piece_unit(unit: str) -> bool:
    u = _norm(unit).strip().rstrip(".")
    return u in {"шт", "компл", "к-т", "комплект"}


def equipment_list(items: Iterable[dict]) -> list[dict]:
    """Оставить только штучные позиции (оборудование), без кабелей/материалов."""
    result: list[dict] = []
    for item in items:
        qty = item.get("qty")
        unit = item.get("unit") or ""
        if qty is None:
            continue
        if not _is_piece_unit(unit):
            continue
        result.append(
            {
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "maker": item.get("maker", ""),
                "qty": qty,
                "unit": unit,
                "pos": item.get("pos", ""),
            }
        )
    return result
