"""Протокол проверки устройства автоматического ввода резерва (АВР).

Форма по ПУЭ 3.1 / И 1.13-07 (упрощённо): валидация строк проверок,
заключение по действию и времени переключения, генерация Excel.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


MAX_ROWS = 500

_DEFAULT_ALLOWED_TIME = Decimal("1.0")

_SCENARIO_HINTS: tuple[str, ...] = (
    "Пропадание напряжения на вводе 1",
    "Пропадание напряжения на вводе 2",
    "Восстановление напряжения на вводе 1",
    "Исчезновение фазы",
    "Ручной возврат",
    "Блокировка при КЗ на шинах",
)


def _parse_decimal(value: Any, row_no: int, field_name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError(f"Строка {row_no}: поле «{field_name}» должно быть числом")
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        s = value.strip().replace(",", ".")
        if not s:
            raise ValueError(f"Строка {row_no}: поле «{field_name}» пустое")
        try:
            return Decimal(s)
        except InvalidOperation:
            raise ValueError(
                f"Строка {row_no}: поле «{field_name}» должно быть числом"
            )
    raise ValueError(f"Строка {row_no}: поле «{field_name}» должно быть числом")


def _is_blank_row(raw: dict) -> bool:
    if not isinstance(raw, dict):
        return False
    scenario = raw.get("scenario")
    if isinstance(scenario, str) and scenario.strip():
        return False
    for key in ("expected", "measured_time_s", "action_ok", "note", "allowed_time_s"):
        v = raw.get(key)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return False
    return True


def _parse_action_ok(value: Any, row_no: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return ""
        if s in ("yes", "да", "true", "1"):
            return "yes"
        if s in ("no", "нет", "false", "0"):
            return "no"
        raise ValueError(
            f"Строка {row_no}: поле «Сработало как ожидалось» должно быть yes или no"
        )
    raise ValueError(
        f"Строка {row_no}: поле «Сработало как ожидалось» должно быть yes или no"
    )


def validate_rows(rows: list[dict]) -> list[dict]:
    """Проверить и нормализовать строки проверок АВР."""
    if not isinstance(rows, list):
        raise ValueError("rows должен быть списком")
    result: list[dict] = []
    for idx, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Строка {idx}: ожидается объект")
        if _is_blank_row(raw):
            continue
        scenario_raw = raw.get("scenario")
        if not isinstance(scenario_raw, str) or not scenario_raw.strip():
            raise ValueError(f"Строка {idx}: не указан сценарий (scenario)")

        expected_raw = raw.get("expected")
        expected = expected_raw.strip() if isinstance(expected_raw, str) else ""

        measured_raw = raw.get("measured_time_s")
        if measured_raw is None or (
            isinstance(measured_raw, str) and not measured_raw.strip()
        ):
            measured_time_s: Decimal | None = None
        else:
            measured_time_s = _parse_decimal(
                measured_raw, idx, "Измеренное время"
            )
            if measured_time_s < 0:
                raise ValueError(
                    f"Строка {idx}: измеренное время не может быть отрицательным"
                )

        allowed_raw = raw.get("allowed_time_s")
        if allowed_raw is None or (
            isinstance(allowed_raw, str) and not allowed_raw.strip()
        ):
            allowed_time_s = _DEFAULT_ALLOWED_TIME
        else:
            allowed_time_s = _parse_decimal(allowed_raw, idx, "Допустимое время")
            if allowed_time_s < 0:
                raise ValueError(
                    f"Строка {idx}: допустимое время не может быть отрицательным"
                )

        action_ok = _parse_action_ok(raw.get("action_ok"), idx)

        note_raw = raw.get("note")
        note = note_raw.strip() if isinstance(note_raw, str) else ""

        result.append(
            {
                "scenario": scenario_raw.strip(),
                "expected": expected,
                "measured_time_s": measured_time_s,
                "allowed_time_s": allowed_time_s,
                "action_ok": action_ok,
                "note": note,
            }
        )
    if len(result) > MAX_ROWS:
        raise ValueError(f"Слишком много строк: {len(result)} (максимум {MAX_ROWS})")
    return result


def verdict(row: dict) -> str:
    action_ok = row.get("action_ok") or ""
    measured = row.get("measured_time_s")
    allowed = row.get("allowed_time_s") or _DEFAULT_ALLOWED_TIME

    if isinstance(measured, str):
        s = measured.strip().replace(",", ".")
        measured = Decimal(s) if s else None
    if isinstance(allowed, str):
        allowed = Decimal(allowed.strip().replace(",", "."))
    if not isinstance(allowed, Decimal):
        allowed = Decimal(str(allowed))

    if action_ok == "no":
        return "не годен"
    if measured is not None and Decimal(measured) > allowed:
        return "не годен"
    if action_ok == "yes":
        return "годен"
    if measured is None:
        return "нет данных"
    # есть время, но нет действия
    return "нет данных"


def _stringify_dt(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    if "T" in s and len(s) > 10:
        return s[:10]
    return s


_THIN = Side(style="thin", color="000000")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_RED_FILL = PatternFill("solid", fgColor="FFC7CE")
_HEADER_FILL = PatternFill("solid", fgColor="D9D9D9")
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
_LEFT_WRAP = Alignment(horizontal="left", vertical="top", wrap_text=True)


def _format_decimal(value: Decimal | None) -> Any:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def to_xlsx(header: dict, rows: list[dict]) -> bytes:
    normalized = validate_rows(rows)

    wb = Workbook()
    ws = wb.active
    ws.title = "Протокол"

    protocol_no = _stringify_dt(header.get("protocol_no")) or "б/н"
    title = f"ПРОТОКОЛ № {protocol_no} проверки устройства АВР"

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=7)
    title_cell = ws.cell(row=1, column=1, value=title)
    title_cell.font = Font(bold=True, size=13)
    title_cell.alignment = _CENTER
    ws.row_dimensions[1].height = 28

    requisites: list[tuple[str, Any]] = [
        ("Объект", header.get("object_name")),
        ("Адрес", header.get("address")),
        ("Заказчик", header.get("customer")),
        ("Подрядчик", header.get("contractor")),
        ("Лаборатория", header.get("laboratory")),
        ("Устройство АВР", header.get("device")),
        ("Прибор", header.get("instrument")),
        ("Дата поверки прибора", header.get("instrument_check_date")),
        ("Ввод 1", header.get("input1")),
        ("Ввод 2", header.get("input2")),
        ("Дата", _stringify_dt(header.get("date"))),
        ("Проверку выполнил", header.get("tested_by")),
        ("Проверил", header.get("checked_by")),
    ]

    r = 3
    for label, value in requisites:
        label_cell = ws.cell(row=r, column=1, value=label)
        label_cell.font = Font(bold=True)
        label_cell.alignment = _LEFT_WRAP
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=7)
        value_cell = ws.cell(row=r, column=2, value="" if value is None else str(value))
        value_cell.alignment = _LEFT_WRAP
        r += 1

    r += 1
    table_start = r

    fixed_headers = [
        "№",
        "Сценарий",
        "Ожидаемое действие",
        "Измерено, с",
        "Допустимо, с",
        "Сработало как ожидалось",
        "Заключение",
    ]

    header_row = table_start
    for col, title_text in enumerate(fixed_headers, start=1):
        c = ws.cell(row=header_row, column=col, value=title_text)
        c.font = Font(bold=True)
        c.fill = _HEADER_FILL
        c.alignment = _CENTER
        c.border = _BORDER
    total_cols = len(fixed_headers)

    r = header_row + 1
    for i, row in enumerate(normalized, start=1):
        row_verdict = verdict(row)
        action_display = ""
        if row["action_ok"] == "yes":
            action_display = "да"
        elif row["action_ok"] == "no":
            action_display = "нет"

        values: list[Any] = [
            i,
            row["scenario"],
            row["expected"],
            _format_decimal(row["measured_time_s"]),
            _format_decimal(row["allowed_time_s"]),
            action_display,
            row_verdict,
        ]
        for j, v in enumerate(values, start=1):
            c = ws.cell(row=r, column=j, value=v)
            c.border = _BORDER
            c.alignment = _LEFT_WRAP if j in (2, 3) else _CENTER

        if row_verdict == "не годен":
            for j in range(1, total_cols + 1):
                ws.cell(row=r, column=j).fill = _RED_FILL
        if (
            row["measured_time_s"] is not None
            and row["measured_time_s"] > row["allowed_time_s"]
        ):
            ws.cell(row=r, column=4).fill = _RED_FILL
        r += 1

    r += 1
    bad = [
        row["scenario"]
        for row in normalized
        if verdict(row) == "не годен"
    ]
    if not normalized:
        conclusion = "проверок нет"
    elif bad:
        conclusion = "есть замечания: " + ", ".join(bad)
    else:
        conclusion = "АВР работоспособен"

    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=total_cols)
    concl_cell = ws.cell(row=r, column=1, value=f"Заключение: {conclusion}")
    concl_cell.font = Font(bold=True)
    concl_cell.alignment = _LEFT_WRAP
    r += 2

    ws.cell(row=r, column=1, value="Проверку выполнил")
    ws.cell(row=r, column=5, value="Проверил")
    r += 1
    ws.cell(row=r, column=1, value=header.get("tested_by") or "")
    ws.cell(row=r, column=5, value=header.get("checked_by") or "")

    widths = {
        1: 6,
        2: 32,
        3: 30,
        4: 12,
        5: 12,
        6: 16,
        7: 16,
    }
    for idx, width in widths.items():
        ws.column_dimensions[get_column_letter(idx)].width = width

    import io

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
