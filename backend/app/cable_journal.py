"""Универсальный разбор кабельного журнала (Excel или таблица из PDF).

Модуль использует только стандартную библиотеку и не привязан к конкретному
проекту: колонки определяются по синонимам заголовков.
"""

from __future__ import annotations

import re

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


# Поле -> подстроки заголовка в нижнем регистре (ё уже нормализована в е).
COLUMN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "cable_id": (
        "маркировка кабеля",
        "обозначение кабеля",
        "обозначение кабеля, провода",
        "№ кабеля",
        "номер кабеля",
    ),
    "brand": (
        "заводская маркировка",
        "марка",
    ),
    "cores": (
        "сечение",
        "кол-во и сечение",
        "количество, число и сечение",
        "число и сечение жил",
    ),
    "from": (
        "откуда",
        "начало",
    ),
    "to": (
        "куда",
        "конец",
    ),
    "length": (
        "общая длина",
        "длина, м",
        "длина",
    ),
    "circuit": (
        "наименование монтажной единицы",
        "назначение",
        "наименование цепи",
    ),
    "laying": (
        "в мет. каб. лотках",
        "в ж/б",
        "в металлорукаве",
        "опорные",
        "способ прокладки",
        "в трубе",
    ),
}

# Заголовки-маркеры для двухстрочной шапки «По проекту» / «Проложен».
_DESIGN_MARKERS = ("по проекту",)
_LAID_MARKERS = ("проложен",)


def _norm(value: str) -> str:
    return (value or "").lower().replace("ё", "е")


def _cell(row: list[str], idx: int) -> str:
    if 0 <= idx < len(row):
        return row[idx] or ""
    return ""


def _join_rows(a: list[str], b: list[str]) -> list[str]:
    n = max(len(a), len(b))
    out: list[str] = []
    for i in range(n):
        left = _cell(a, i).strip()
        right = _cell(b, i).strip()
        if left and right and left != right:
            out.append(f"{left} {right}")
        else:
            out.append(left or right)
    return out


def _matches(header: str, synonyms: Iterable[str]) -> bool:
    h = _norm(header)
    return any(syn in h for syn in synonyms)


def _find_first(col_values: list[str], synonyms: Iterable[str], exclude: set[int]) -> int | None:
    # Приоритет — порядку синонимов: сначала ищем по первому синониму по всем столбцам,
    # затем по второму и т.д.
    syn_list = list(synonyms)
    for syn in syn_list:
        for i, val in enumerate(col_values):
            if i in exclude:
                continue
            if syn in _norm(val):
                return i
    return None


def _pick_length_column(col_values: list[str], exclude: set[int]) -> int | None:
    # Приоритет: "общая длина" -> "длина, м" -> "длина".
    for syn in ("общая длина", "длина, м", "длина"):
        for i, val in enumerate(col_values):
            if i in exclude:
                continue
            if syn in _norm(val):
                return i
    return None


def _design_laid_split(
    header_a: list[str], header_b: list[str], fields: dict[str, int]
) -> tuple[dict[str, int], dict[str, int], list[int], dict[str, int]]:
    """Разбить «длина»/«марка» на design_/laid_ по маркерам над ними.

    Возвращает (fields, laid_fields, laying_idx, laying_map).
    """
    fields = dict(fields)
    laid_fields: dict[str, int] = {}
    laying_idx: list[int] = []
    laying_map: dict[str, int] = {}

    n = max(len(header_a), len(header_b))

    # Разделы определяем по верхней строке шапки.
    sections: list[str] = []  # "design" | "laid" | ""
    current = ""
    for i in range(n):
        cell_a = _norm(_cell(header_a, i)).strip()
        if any(m in cell_a for m in _DESIGN_MARKERS):
            current = "design"
        elif any(m in cell_a for m in _LAID_MARKERS):
            current = "laid"
        sections.append(current)

    # Ищем по нижней строке колонки с «длина»/«марка».
    lower = [_norm(_cell(header_b, i)) for i in range(n)]
    length_cols = [i for i in range(n) if "длина" in lower[i]]
    brand_cols = [i for i in range(n) if "марка" in lower[i]]

    # Разбиение по «По проекту» / «Проложен».
    design_length: int | None = None
    laid_length: int | None = None
    design_brand: int | None = None
    laid_brand: int | None = None

    if len(length_cols) >= 2:
        design_candidates = [i for i in length_cols if sections[i] == "design"]
        laid_candidates = [i for i in length_cols if sections[i] == "laid"]
        if design_candidates:
            design_length = design_candidates[0]
        if laid_candidates:
            laid_length = laid_candidates[0]
        if design_length is None:
            design_length = length_cols[0]
        if laid_length is None:
            other = [i for i in length_cols if i != design_length]
            if other:
                laid_length = other[0]

    if len(brand_cols) >= 2:
        design_candidates = [i for i in brand_cols if sections[i] == "design"]
        laid_candidates = [i for i in brand_cols if sections[i] == "laid"]
        if design_candidates:
            design_brand = design_candidates[0]
        if laid_candidates:
            laid_brand = laid_candidates[0]
        if design_brand is None:
            design_brand = brand_cols[0]
        if laid_brand is None:
            other = [i for i in brand_cols if i != design_brand]
            if other:
                laid_brand = other[0]

    if design_length is not None:
        fields["length"] = design_length
    if laid_length is not None:
        laid_fields["laid_length"] = laid_length
    if design_brand is not None:
        fields["brand"] = design_brand

    # Laying-колонки собираем из объединённых заголовков.
    for i in range(n):
        merged = f"{_cell(header_a, i)} {_cell(header_b, i)}".strip()
        if _matches(merged, COLUMN_SYNONYMS["laying"]):
            laying_idx.append(i)
            laying_map[merged] = i

    return fields, laid_fields, laying_idx, laying_map


def _header_search_row(row: list[str]) -> dict[str, Any]:
    """Определить колонки по одной (объединённой) строке шапки."""
    n = len(row)
    used: set[int] = set()

    cable_id = _find_first(row, COLUMN_SYNONYMS["cable_id"], set())
    if cable_id is not None:
        used.add(cable_id)
    from_idx = _find_first(row, COLUMN_SYNONYMS["from"], used)
    if from_idx is not None:
        used.add(from_idx)
    to_idx = _find_first(row, COLUMN_SYNONYMS["to"], used)
    if to_idx is not None:
        used.add(to_idx)
    if cable_id is None or from_idx is None or to_idx is None:
        return {}

    fields: dict[str, int] = {
        "cable_id": cable_id,
        "from": from_idx,
        "to": to_idx,
    }

    brand = _find_first(row, COLUMN_SYNONYMS["brand"], used)
    if brand is not None:
        fields["brand"] = brand
        used.add(brand)

    cores = _find_first(row, COLUMN_SYNONYMS["cores"], used)
    if cores is not None:
        fields["cores"] = cores
        used.add(cores)

    length = _pick_length_column(row, used)
    if length is not None:
        fields["length"] = length
        used.add(length)

    circuit = _find_first(row, COLUMN_SYNONYMS["circuit"], used)
    if circuit is not None:
        fields["circuit"] = circuit
        used.add(circuit)

    laying_idx: list[int] = []
    laying_map: dict[str, int] = {}
    for i in range(n):
        val = row[i] if i < len(row) else ""
        if _matches(val, COLUMN_SYNONYMS["laying"]):
            laying_idx.append(i)
            laying_map[val] = i

    return {
        "fields": fields,
        "laid_fields": {},
        "laying_idx": laying_idx,
        "laying_map": laying_map,
    }


def _looks_like_data(row: list[str]) -> bool:
    numeric = sum(1 for cell in row if re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", str(cell or "")))
    return numeric >= 2


def find_header(rows: list[list[str]]) -> tuple[int, dict[str, Any]] | None:
    """Найти строку шапки и индексы колонок.

    Возвращает (индекс_строки_шапки, {field: idx, "laid_length": idx,
    "laying_columns": {заголовок: idx}}).
    """
    if not rows:
        return None

    n_rows = len(rows)
    for r in range(min(n_rows, 200)):
        base = _header_search_row(rows[r])
        top_has_id = _find_first(rows[r], COLUMN_SYNONYMS["cable_id"], set()) is not None
        if not base and top_has_id:
            # Multi-row header: «cable id» in the top row, «from/to» one or two rows below.
            for extra in (1, 2):
                if r + extra < n_rows and not any(_looks_like_data(rows[r + k]) for k in range(1, extra + 1)):
                    joined = rows[r]
                    for k in range(1, extra + 1):
                        joined = _join_rows(joined, rows[r + k])
                    base = _header_search_row(joined)
                    if base:
                        break
        if base:
            # Сначала пробуем трактовать шапку как многострочную (это более
            # информативный вариант разбора — «По проекту»/«Проложен»).
            for extra in (1, 2):
                if r + extra >= n_rows:
                    continue
                # Never glue a data row (numbers in it) into the header.
                if any(_looks_like_data(rows[r + k]) for k in range(1, extra + 1)):
                    continue
                header_a = rows[r]
                header_b = rows[r + 1]
                # Для extra == 2 свернём три строки шапки в одну логическую
                # пару (a — верхняя, b — нижняя).
                if extra == 2 and r + 2 < n_rows:
                    header_a = _join_rows(rows[r], rows[r + 1])
                    header_b = rows[r + 2]
                merged = _join_rows(header_a, header_b)
                base2 = _header_search_row(merged)
                if not base2:
                    continue
                fields, laid_fields, laying_idx, laying_map = _design_laid_split(
                    header_a, header_b, base2["fields"]
                )
                if "length" in fields and "brand" in fields:
                    result: dict[str, Any] = dict(fields)
                    result.update(laid_fields)
                    if laying_idx:
                        result["laying_columns"] = laying_map
                    return r, result

            # Одно­строчные поля — обычный случай.
            result = dict(base["fields"])
            result.update(base["laid_fields"])
            if base["laying_idx"]:
                result["laying_columns"] = base["laying_map"]
            return r, result

    return None


def _parse_decimal(value: str) -> Decimal | None:
    s = (value or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _is_total_row(cable_id: str) -> bool:
    v = _norm(cable_id).strip()
    if not v:
        return True
    for marker in ("итог", "всего"):
        if marker in v:
            return True
    return False


def _get(row: list[str], idx: int | None) -> str:
    if idx is None:
        return ""
    if 0 <= idx < len(row):
        return (row[idx] or "").strip()
    return ""


def parse_cable_journal(rows: list[list[str]]) -> list[dict]:
    """Разобрать строки кабельного журнала в список кабелей."""
    if not rows:
        return []

    header = find_header(rows)
    if header is None:
        return []

    header_idx, cols = header
    cable_id_idx = cols.get("cable_id")
    if cable_id_idx is None:
        return []

    brand_idx = cols.get("brand")
    cores_idx = cols.get("cores")
    from_idx = cols.get("from")
    to_idx = cols.get("to")
    length_idx = cols.get("length")
    laid_length_idx = cols.get("laid_length")
    circuit_idx = cols.get("circuit")
    laying_columns: dict[str, int] = cols.get("laying_columns", {}) or {}

    result: list[dict] = []
    for row in rows[header_idx + 1:]:
        cable_id = _get(row, cable_id_idx)
        if not cable_id or _is_total_row(cable_id):
            continue

        laying: dict[str, Decimal] = {}
        for title, idx in laying_columns.items():
            val = _parse_decimal(_get(row, idx))
            if val is not None and val != 0:
                laying[title] = val

        result.append(
            {
                "cable_id": cable_id,
                "brand": _get(row, brand_idx),
                "cores": _get(row, cores_idx),
                "from": _get(row, from_idx),
                "to": _get(row, to_idx),
                "length": _parse_decimal(_get(row, length_idx)),
                "laid_length": _parse_decimal(_get(row, laid_length_idx)),
                "circuit": _get(row, circuit_idx),
                "laying": laying,
            }
        )

    return result


def rows_from_tsv(text: str) -> list[list[str]]:
    """Разобрать текст extract_text для xlsx в матрицу строк."""
    if text is None:
        return []
    lines = text.split("\n")
    result: list[list[str]] = []
    for line in lines:
        cells = line.split("\t")
        if cells and cells[-1].startswith("__PU_SOURCE_COORD__"):
            cells = cells[:-1]
        result.append(cells)
    return result


def summary(cables: Iterable[dict]) -> dict:
    """Сводка по списку кабелей."""
    count = 0
    total_length = Decimal("0")
    by_brand_cores: dict[str, Decimal] = {}

    for c in cables:
        count += 1
        length = c.get("length")
        if isinstance(length, Decimal):
            total_length += length
        brand = c.get("brand") or ""
        cores = c.get("cores") or ""
        key = f"{brand} {cores}".strip()
        if key:
            by_brand_cores[key] = by_brand_cores.get(key, Decimal("0")) + (
                length if isinstance(length, Decimal) else Decimal("0")
            )

    return {
        "count": count,
        "total_length": total_length,
        "by_brand_cores": by_brand_cores,
    }
