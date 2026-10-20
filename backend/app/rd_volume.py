import re

_VOLUME_RE = re.compile(
    r"[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{2,40}-(ЭМ|ЭО|ЭС|ЭН|АР|АС|КЖ|КМ|ОВ|ВК|СС|АСДУ|АК|АТХ|СКТП|ТХ|ПБ|ПТ|ГП|ПОС|СКС|ОД|ПЗ)\b"
)

_SHEET_RE = re.compile(
    r"(?P<code>[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_-]{2,40}-[А-ЯЁA-Z]{2,5}(?:[.-][А-ЯЁA-Z]{1,4})?)(?:\s*л\.?\s*(?P<sheet>\d{1,3}))?"
)

_CELL_STOP_MARKERS = (
    "Изм.",
    "Кол.уч",
    "Лист",
    "№ док.",
    "Подпись",
    "Дата",
    "Разраб.",
    "Провер",
    "ГИП",
    "Н. контр",
    "Утвердил",
    "Стадия",
    "Листов",
)


def parse_volume_code(text: str) -> str | None:
    if not text:
        return None
    match = _VOLUME_RE.search(text)
    if not match:
        return None
    return match.group(0)


def section_of(code: str) -> str | None:
    if not code:
        return None
    upper = code.upper()
    for section in ("АСДУ", "СКТП"):
        if f"-{section}" in upper:
            if section == "АСДУ":
                return "АСДУ"
            return "ОВиК"
    for section in ("ЭМ", "ЭО", "ЭС", "ЭН"):
        if f"-{section}" in upper:
            return "ЭМ"
    if "-АР" in upper:
        return "АР"
    for section in ("АС", "КЖ", "КМ"):
        if f"-{section}" in upper:
            return "КЖ"
    for section in ("ОВ",):
        if f"-{section}" in upper:
            return "ОВиК"
    for section in ("АК", "АТХ"):
        if f"-{section}" in upper:
            return "АСДУ"
    return None


def _classify(title: str) -> str:
    lowered = title.lower()
    if "кабельный журнал" in lowered:
        return "cable_journal"
    if ("спецификац" in lowered) or ("ведомость" in lowered and "ведомость рабочих чертежей" not in lowered):
        return "schedule"
    if (
        "текстовая часть" in lowered
        or "пояснительная записка" in lowered
        or "общие данные" in lowered
    ):
        return "text"
    if "содержание" in lowered or "состав" in lowered:
        return "contents"
    return "drawing"


def _clean_title(raw: str) -> str:
    cell = raw
    # Cut off any "header" column markers.
    for marker in _CELL_STOP_MARKERS:
        idx = cell.find(marker)
        if idx != -1:
            cell = cell[:idx]
    cell = cell.replace("\u00a0", " ")
    cell = re.sub(r"\s+", " ", cell).strip()
    cell = cell.strip(" .")
    return cell


def parse_sheet_list(text: str) -> list[dict]:
    if not text:
        return []
    matches = list(_SHEET_RE.finditer(text))
    results: list[dict] = []
    seen: set[tuple[str, int | None]] = set()
    for i, match in enumerate(matches):
        code = match.group("code")
        sheet_raw = match.group("sheet")
        sheet: int | None = int(sheet_raw) if sheet_raw else None
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = _clean_title(text[start:end])
        if not title:
            continue
        key = (code, sheet)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "code": code,
                "sheet": sheet,
                "title": title,
                "kind": _classify(title),
            }
        )
    return results


def volume_summary(text: str) -> dict:
    sheets = parse_sheet_list(text)
    drawings = sum(1 for s in sheets if s["kind"] == "drawing")
    has_specification = any(s["kind"] == "schedule" for s in sheets)
    has_cable_journal = any(s["kind"] == "cable_journal" for s in sheets)
    return {
        "volume_code": parse_volume_code(text),
        "section": section_of(parse_volume_code(text) or ""),
        "sheets": sheets,
        "drawings": drawings,
        "has_specification": has_specification,
        "has_cable_journal": has_cable_journal,
    }
