from __future__ import annotations

import csv
import io
import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext


SOURCE_COORDINATE_MARKER = "__PU_SOURCE_COORD__"
MATRIX_ROUNDING_ALGORITHM = "largest_remainder_half_even_v1"
MATRIX_MONEY_MAX = Decimal("9999999999999999.99")
MATRIX_QUANTUM = Decimal("0.01")

MONTH_ALIASES = {
    "январь": 1, "января": 1, "янв": 1,
    "февраль": 2, "февраля": 2, "фев": 2,
    "март": 3, "марта": 3, "мар": 3,
    "апрель": 4, "апреля": 4, "апр": 4,
    "май": 5, "мая": 5,
    "июнь": 6, "июня": 6, "июн": 6,
    "июль": 7, "июля": 7, "июл": 7,
    "август": 8, "августа": 8, "авг": 8,
    "сентябрь": 9, "сентября": 9, "сен": 9,
    "октябрь": 10, "октября": 10, "окт": 10,
    "ноябрь": 11, "ноября": 11, "ноя": 11,
    "декабрь": 12, "декабря": 12, "дек": 12,
}


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
    if any(word in normalized for word in ("приход", "поступление", "зачисление", "вход", "выручка", "доход")):
        return "inflow"
    return None


def _coordinate(cells: list[str], fallback_row: int) -> tuple[list[str], str | None, int]:
    if cells and cells[-1].startswith(f"{SOURCE_COORDINATE_MARKER}:"):
        marker = cells[-1][len(SOURCE_COORDINATE_MARKER) + 1:]
        sheet, separator, row = marker.rpartition(":")
        if separator and row.isdigit():
            return cells[:-1], sheet or None, int(row)
    return cells, None, fallback_row


def _excel_column(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _matrix_decimal(value: str) -> Decimal:
    """Read raw matrix money without stripping invalid/ambiguous characters."""
    raw = value.strip().replace("\u00a0", " ").replace("\u202f", " ")
    if not raw:
        return Decimal("0")
    if len(raw) > 128:
        raise ValueError("слишком длинное числовое значение")
    if " " in raw:
        if not re.fullmatch(r"[+]?\d{1,3}(?: \d{3})+(?:[.,]\d+)?", raw):
            raise ValueError("неоднозначная сумма")
        raw = raw.replace(" ", "")
    if not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d{1,3})?", raw):
        raise ValueError("не распознана однозначная десятичная сумма")
    amount = Decimal(raw.replace(",", "."))
    _validate_matrix_decimal(amount)
    return amount


def _validate_matrix_decimal(amount: Decimal) -> None:
    if not amount.is_finite() or amount < 0 or amount > MATRIX_MONEY_MAX:
        raise ValueError("сумма должна быть неотрицательной и помещаться в Numeric(18,2)")
    if abs(amount.as_tuple().exponent) > 128 or len(amount.as_tuple().digits) > 128:
        raise ValueError("неподдерживаемая точность суммы")


def allocate_monthly_amounts(cells: list[tuple[int, int, Decimal]]) -> dict[int, Decimal]:
    """Round an article once and allocate cents; ties use month, then column."""
    if len({column for _, column, _ in cells}) != len(cells):
        raise ValueError("повтор исходной колонки")
    with localcontext() as context:
        context.prec = 512
        context.rounding = ROUND_HALF_EVEN
        for month, column, amount in cells:
            if not 1 <= month <= 12 or column < 0:
                raise ValueError("неверная координата месяца")
            _validate_matrix_decimal(amount)
        total = sum((amount for _, _, amount in cells), Decimal("0"))
        _validate_matrix_decimal(total)
        target = total.quantize(MATRIX_QUANTUM)
        _validate_matrix_decimal(target)
        floors = {column: (amount * 100).to_integral_value(rounding=ROUND_FLOOR)
                  for _, column, amount in cells}
        remaining = int(target * 100 - sum(floors.values(), Decimal("0")))
        ranked = sorted(
            ((month, column, amount) for month, column, amount in cells if amount > 0),
            key=lambda cell: (-(cell[2] * 100 - floors[cell[1]]), cell[0], cell[1]),
        )
        if not 0 <= remaining <= len(ranked):
            raise ValueError("не удалось распределить остаток")
        for _, column, _ in ranked[:remaining]:
            floors[column] += 1
        result = {column: (cents / 100).quantize(MATRIX_QUANTUM) for column, cents in floors.items()}
        assert sum(result.values(), Decimal("0")) == target
        return result


def _monthly_cash_flow(
    matrix: list[tuple[list[str], str | None, int, int]],
    *,
    plan_year: int | None,
    limit: int,
    source_name: str | None,
) -> dict | None:
    """Parse the common DDS layout where rows are articles and columns are months."""
    best: tuple[int, dict[int, int]] | None = None
    for index, (cells, _sheet, _row, _line) in enumerate(matrix[:500]):
        month_columns = {
            column: MONTH_ALIASES[normalized]
            for column, cell in enumerate(cells)
            if (normalized := _normalized(cell)) in MONTH_ALIASES
        }
        if len(set(month_columns.values())) >= 3 and (best is None or len(month_columns) > len(best[1])):
            best = (index, month_columns)
    if best is None:
        return None

    header_index, month_columns = best
    headers, header_sheet, _header_row, _header_line = matrix[header_index]
    inferred_december = False
    november_columns = [column for column, month in month_columns.items() if month == 11]
    if set(month_columns.values()) == set(range(1, 12)) and len(november_columns) == 1:
        december_column = november_columns[0] + 1
        if december_column < len(headers) and not headers[december_column].strip():
            has_values = any(
                source_sheet == header_sheet
                and december_column < len(cells)
                and bool(cells[december_column].strip())
                for cells, source_sheet, _source_row, _source_line in matrix[header_index + 1:]
            )
            if has_values:
                month_columns[december_column] = 12
                inferred_december = True

    issues: list[str] = []
    blocking_issues: list[str] = []
    if len(set(month_columns.values())) != len(month_columns):
        blocking_issues.append("В заголовке месячной матрицы повторяется месяц")
    if plan_year is None:
        issues.append("Для месячного плана ДДС не указан год")
        blocking_issues.extend(issues)
    elif not 2000 <= plan_year <= 2100:
        blocking_issues.append("Год месячного плана должен быть в диапазоне 2000–2100")
    if blocking_issues:
        return {
            "headers": headers, "mapping": {}, "rows": [], "issues": issues,
            "blocking_issues": blocking_issues, "articles": [],
            "truncated": False, "layout": "monthly_matrix", "plan_year": plan_year,
            "inferred_december": inferred_december,
        }

    first_month_column = min(month_columns)
    rows: list[dict] = []
    articles: list[dict] = []
    candidates = matrix[header_index + 1:]
    if header_sheet:
        candidates = [candidate for candidate in candidates if candidate[1] == header_sheet]
    for cells, source_sheet, source_row, source_line in candidates:
        title = next((
            cell.strip() for cell in cells[:first_month_column]
            if cell.strip() and re.search(r"[a-zа-яё]", cell, re.IGNORECASE)
        ), "")
        normalized_title = _normalized(title)
        if normalized_title.startswith(("расходы по месяцам", "остаток", "баланс")):
            break
        if not title or normalized_title.startswith(("итого", "всего", "платежи в dci")):
            continue
        def coordinate_for(column: int) -> str:
            coordinate = f"{_excel_column(column)}{source_row}"
            return f"{source_sheet}!{coordinate}" if source_sheet else coordinate

        direction = _direction(title) or ("inflow" if normalized_title.startswith("этапы ") else "outflow")
        article = {
            "article_id": source_line, "title": title, "source_row": source_row,
            "source_sheet": source_sheet, "source_line": source_line,
            "source_coordinate": f"{source_sheet}!{source_row}" if source_sheet else f"строка {source_row}",
            "direction": direction, "monthly_total": None, "annual_total": None,
            "annual_difference": None,
            "annual_coordinate": coordinate_for(first_month_column - 1) if first_month_column > 0 else None,
            "rounding_algorithm": MATRIX_ROUNDING_ALGORITHM, "months": [],
            "issues": [], "importable": True, "preview_complete": True,
        }
        articles.append(article)
        raw_cells: list[tuple[int, int, Decimal]] = []
        for column, month in sorted(month_columns.items(), key=lambda item: (item[1], item[0])):
            try:
                raw_cells.append((month, column, _matrix_decimal(cells[column] if column < len(cells) else "")))
            except (ValueError, InvalidOperation) as exc:
                article["issues"].append(f"{coordinate_for(column)}: {exc}")
        annual_amount = None
        if 0 < first_month_column <= len(cells) and cells[first_month_column - 1].strip():
            try:
                annual_amount = _matrix_decimal(cells[first_month_column - 1])
            except (ValueError, InvalidOperation) as exc:
                article["issues"].append(f"{article['annual_coordinate']}: {exc}")
        if not article["issues"]:
            try:
                allocated = allocate_monthly_amounts(raw_cells)
            except (ValueError, InvalidOperation) as exc:
                article["issues"].append(f"{article['source_coordinate']}: {exc}")
        if article["issues"]:
            article["importable"] = False
            blocking_issues.extend(article["issues"])
            continue
        with localcontext() as context:
            context.prec = 512
            context.rounding = ROUND_HALF_EVEN
            monthly_total = sum((value for _, _, value in raw_cells), Decimal("0")).quantize(MATRIX_QUANTUM)
            article["monthly_total"] = str(monthly_total)
            if annual_amount is not None:
                annual_total = annual_amount.quantize(MATRIX_QUANTUM)
                article["annual_total"] = str(annual_total)
                article["annual_difference"] = str(annual_total - monthly_total)
                if annual_total != monthly_total:
                    issues.append(
                        f"{article['source_coordinate']}: годовой итог {annual_total} "
                        f"не равен сумме месяцев {monthly_total}"
                    )
            for month, column, raw_amount in raw_cells:
                ordinary = raw_amount.quantize(MATRIX_QUANTUM)
                article["months"].append({
                    "month": month, "source_column": column, "source_coordinate": coordinate_for(column),
                    "raw_amount": str(raw_amount), "ordinary_amount": str(ordinary),
                    "amount": str(allocated[column]), "adjustment": str(allocated[column] - ordinary),
                })
        for month, column, raw_amount in raw_cells:
            if allocated[column] <= 0:
                continue
            amount = str(allocated[column])
            planned_date = date(plan_year, month, monthrange(plan_year, month)[1]).isoformat()
            coordinate = f"{_excel_column(column)}{source_row}"
            if source_sheet:
                coordinate = f"{source_sheet}!{coordinate}"
            rows.append({
                "selection_id": source_line * 1000 + column + 1,
                "source_row": source_row,
                "source_sheet": source_sheet,
                "source_line": source_line,
                "source_coordinate": coordinate,
                "source_name": source_name,
                "matrix_article_id": source_line,
                "matrix_month": month,
                "title": title,
                "category": "Прочее",
                "planned_start": None,
                "planned_finish": None,
                "planned_date": planned_date,
                "amount": amount,
                "counterparty": None,
                "object_name": None,
                "note": title,
                "direction": direction,
                "progress": 0.0,
                "issues": [],
                "importable": True,
                "excerpt": f"{title} | {plan_year}-{month:02d} | {amount}"[:2000],
            })
    truncated = len(rows) > limit
    if truncated:
        kept_ids = {row["selection_id"] for row in rows[:limit]}
        for article in articles:
            article_rows = [row for row in rows if row["matrix_article_id"] == article["article_id"]]
            article["preview_complete"] = all(row["selection_id"] in kept_ids for row in article_rows)
        blocking_issues.append("Предпросмотр усечён: бюджет нельзя создавать по неполному набору месяцев")
    return {
        "headers": headers,
        "mapping": {"row_title": "title", "month_columns": "planned_date + amount"},
        "rows": rows[:limit],
        "issues": issues + blocking_issues,
        "blocking_issues": blocking_issues,
        "articles": articles,
        "truncated": truncated,
        "layout": "monthly_matrix",
        "plan_year": plan_year,
        "inferred_december": inferred_december,
    }


def parse_structured_rows(content: str, kind: str, limit: int = 500, source_name: str | None = None,
                          plan_year: int | None = None) -> dict:
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

    if kind == "cash-flow" and (monthly := _monthly_cash_flow(
        matrix, plan_year=plan_year, limit=limit, source_name=source_name,
    )) is not None:
        return monthly

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
            "selection_id": source_row,
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
