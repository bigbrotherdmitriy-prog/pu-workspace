import re

_VOLUME_RE = re.compile(
    # «Д/ФНС.2024-ЭМ», «П2200175-11-021-РЗ», «123-45-ИОС1.1»: segments joined by «-»,
    # the last one is the section code; the code must contain a digit somewhere.
    r"(?<![\w/])([А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_]*(?:-[А-ЯЁA-Z0-9./_]+){0,6}-(?P<sec>[А-ЯЁA-Z]{2,5}\d?(?:\.\d{1,2})?))(?![\w])"
)

_NORM_PREFIXES = ("ГОСТ", "СП", "СНиП", "ТУ", "СТО")

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
    counts: dict[str, int] = {}
    order: list[str] = []
    for match in _VOLUME_RE.finditer(text):
        code = match.group(1)
        upper = code.upper()
        if any(upper.startswith(prefix) for prefix in _NORM_PREFIXES):
            continue
        if not any(ch.isdigit() for ch in code[: len(code) - len(match.group("sec"))]):
            continue
        if code not in counts:
            counts[code] = 0
            order.append(code)
        counts[code] += 1
    if not counts:
        return None
    best: str | None = None
    best_count = -1
    for code in order:
        cnt = counts[code]
        if cnt > best_count:
            best = code
            best_count = cnt
    return best


_SECTION_MAP: dict[str, str] = {}
for _codes, _target in (
    (("ЭМ", "ЭО", "ЭС", "ЭН"), "ЭМ"),
    (("АР",), "АР"),
    (("АС", "КЖ", "КМ"), "КЖ"),
    (("ОВ", "СКТП"), "ОВиК"),
    (("АСДУ", "АК", "АТХ"), "АСДУ"),
    (("РЗ", "РЗА", "РЗиА"), "РЗА"),
    (("УА", "АСУ", "АСУТП", "ТМ", "ТМС"), "АСУ ТП"),
    (("КХ", "КЖЭ"), "КХ"),
    (("ОРУ", "ЗРУ", "КРУ", "КРУН", "ПС", "ЭТ", "ТП"), "ЭТ"),
    (("СС", "СКС", "ВОЛС"), "СС"),
    (("ПБ", "АПС", "АПТ", "СОУЭ"), "ПБ"),
    (("ВК",), "ВК"),
    (("ТХ",), "ТХ"),
    (("ПТ",), "ПТ"),
    (("ГП",), "ГП"),
    (("ПОС",), "ПОС"),
    (("ОД",), "ОД"),
    (("ПЗ",), "ПЗ"),
):
    for _code in _codes:
        _SECTION_MAP[_code.upper()] = _target

_SECTION_KEYS_SORTED = sorted(_SECTION_MAP, key=len, reverse=True)


def _last_segment(code: str) -> str | None:
    if not code:
        return None
    parts = code.split("-")
    if len(parts) < 2:
        return None
    seg = parts[-1]
    seg = re.sub(r"[0-9.]+$", "", seg)
    return seg.strip() or None


def section_of(code: str) -> str | None:
    if not code:
        return None
    seg = _last_segment(code)
    if not seg:
        return None
    upper = seg.upper()
    # ИОС with optional decimal suffix, e.g. ИОС1.1
    ios_match = re.match(r"^ИОС\d?(?:\.\d{1,2})?$", upper)
    if ios_match:
        return "ИОС"
    if upper in _SECTION_MAP:
        return _SECTION_MAP[upper]
    # Prefix fallback for longer codes like АСУТП1
    for key in _SECTION_KEYS_SORTED:
        if upper.startswith(key):
            return _SECTION_MAP[key]
    return seg


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


def parse_sheet_list(text: str, volume_code: str | None = None) -> list[dict]:
    if not text:
        return []
    if volume_code:
        sheet_re = re.compile(
            r"(?P<code>"
            + re.escape(volume_code)
            + r"(?:[.-][А-ЯЁA-Z]{1,4})?"
            # other volumes (from «Состав проекта») so they land in referenced_volumes
            + r"|(?<![\w/])[А-ЯЁA-Z0-9][А-ЯЁA-Z0-9./_]*(?:-[А-ЯЁA-Z0-9./_]+){0,6}-[А-ЯЁA-Z]{2,5}\d?(?:\.\d{1,2})?(?![\w])"
            + r")(?:\s*(?:л\.?\s*(?P<sheet_l>\d{1,3})|Лист\s*(?P<sheet_w>\d{1,3})))?"
        )
    else:
        sheet_re = _SHEET_RE
    matches = list(sheet_re.finditer(text))
    results: list[dict] = []
    seen: set[tuple[str, int | None]] = set()
    for i, match in enumerate(matches):
        code = match.group("code")
        groups = match.groupdict()
        if "sheet_l" in groups:
            sheet_raw = groups.get("sheet_l") or groups.get("sheet_w")
        else:
            sheet_raw = groups.get("sheet")
        sheet: int | None = int(sheet_raw) if sheet_raw else None
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = _clean_title(text[start:end])
        if volume_code is not None and code == volume_code and sheet is None:
            continue  # title page / stamp, not a sheet
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


def _split_referenced(entries: list[dict], volume_code: str | None) -> tuple[list[dict], list[dict]]:
    if volume_code is None:
        return entries, []
    own: list[dict] = []
    referenced: list[dict] = []
    seen_ref: set[str] = set()
    for entry in entries:
        code = entry["code"]
        if code.startswith(volume_code):
            own.append(entry)
        else:
            if code in seen_ref:
                continue
            seen_ref.add(code)
            referenced.append({"code": code, "title": entry["title"]})
    return own, referenced


_MAIN_SET_HEADER_RE = re.compile(r"Ведомость\s+рабочих\s+чертежей", re.IGNORECASE)
_MAIN_SET_ENTRY_RE = re.compile(
    r"([А-ЯЁ][^№]{2,120}?)\s+Лист\s*№\s*(\d{1,3})(?:\s*-\s*(\d{1,3}))?"
)
_MAIN_SET_STOP_MARKERS = (
    "Инв.",
    "Подпись",
    "Взам.",
    "Изм.",
)  # the «Лист Наименование Примечание» caption is stripped as a prefix, not a stop
_MAIN_SET_TRIM_PREFIX_RE = re.compile(
    r"^(?:.*?\bД/)?",  # placeholder; unused
)
_MAIN_SET_LEADING_RE = re.compile(
    r"^(?:"
    r"Лист\s+Наименование\s+Примечание"
    r"|Лист\s+Наименование"
    r"|Лист"
    r"|Ведомость\s+рабочих\s+чертежей[^А-ЯЁ]*"
    r"|основного\s+комплекта"
    r")\s*",
    re.IGNORECASE,
)


def _clean_main_set_title(raw: str) -> str:
    cell = raw
    for marker in _MAIN_SET_STOP_MARKERS:
        idx = cell.find(marker)
        if idx != -1:
            cell = cell[:idx]
    cell = re.sub(r"\s+", " ", cell).strip()
    # Strip any leading header/caption words that precede the actual title.
    prev = None
    while prev != cell:
        prev = cell
        cell = _MAIN_SET_LEADING_RE.sub("", cell).strip()
    cell = cell.strip(" .")
    return cell


def parse_main_set_sheets(text: str, volume_code: str) -> list[dict]:
    if not text:
        return []
    header = _MAIN_SET_HEADER_RE.search(text)
    if not header:
        return []
    body = text[header.end():]
    # Cut off the stamp/footer block that follows the table.
    for marker in ("Инв.", "Взам.", "Изм."):
        idx = body.find(marker)
        if idx != -1:
            body = body[:idx]
    results: list[dict] = []
    seen: set[tuple[int, int | None]] = set()
    for match in _MAIN_SET_ENTRY_RE.finditer(body):
        title_raw = match.group(1)
        sheet = int(match.group(2))
        sheet_to = int(match.group(3)) if match.group(3) else None
        title = _clean_main_set_title(title_raw)
        if title.startswith(volume_code):
            title = title[len(volume_code):].strip(" .")
        if not title:
            continue
        # Drop trailing fragments that only contain a volume code (e.g. after "Дверные проемы").
        key = (sheet, sheet_to)
        if key in seen:
            continue
        seen.add(key)
        kind = "text" if "общие данные" in title.lower() else "drawing"
        results.append(
            {
                "code": volume_code,
                "sheet": sheet,
                "sheet_to": sheet_to,
                "title": title,
                "kind": kind,
            }
        )
    return results


_NORMS_HEADER_RE = re.compile(r"Ведомость\s+ссылочных", re.IGNORECASE)
_NORMS_END_RE = re.compile(r"Прилагаемые\s+документы", re.IGNORECASE)
_NORMS_CODE_RE = re.compile(
    r"(СНиП|СП|ГОСТ\s+Р|ГОСТ|ПУЭ|РД|СО|ВСН|ФЗ)\s*[\dA-ZА-Я][\d.\-*/A-ZА-Я]*"
)
_NORMS_STOP_MARKERS = (
    "Инв.",
    "Подпись",
    "Взам.",
    "Изм.",
    "Лист Наименование Примечание",
)


def _clean_norm_title(raw: str) -> str:
    cell = raw
    for marker in _NORMS_STOP_MARKERS:
        idx = cell.find(marker)
        if idx != -1:
            cell = cell[:idx]
    cell = re.sub(r"\s+", " ", cell).strip()
    cell = cell.strip(" .")
    return cell


def parse_referenced_norms(text: str) -> list[dict]:
    if not text:
        return []
    header = _NORMS_HEADER_RE.search(text)
    if not header:
        return []
    body = text[header.end():header.end() + 3000]
    end = _NORMS_END_RE.search(body)
    if end:
        body = body[:end.start()]
    matches = list(_NORMS_CODE_RE.finditer(body))
    results: list[dict] = []
    seen: set[str] = set()
    for i, match in enumerate(matches):
        code = re.sub(r"\s+", " ", match.group(0)).strip()
        start = match.end()
        stop = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        title = _clean_norm_title(body[start:stop])
        if code in seen:
            continue
        seen.add(code)
        results.append({"code": code, "title": title})
    return results


def volume_summary(text: str) -> dict:
    volume_code = parse_volume_code(text)
    raw_sheets = parse_sheet_list(text, volume_code)
    sheets, referenced_volumes = _split_referenced(raw_sheets, volume_code)
    if volume_code and not any(s["kind"] == "drawing" for s in sheets):
        main_set = parse_main_set_sheets(text, volume_code)
        if main_set:
            existing_sheets = {(s.get("code"), s.get("sheet")) for s in sheets}
            for entry in main_set:
                key = (entry["code"], entry["sheet"])
                if key in existing_sheets:
                    continue
                existing_sheets.add(key)
                sheets.append(entry)
    drawings = sum(1 for s in sheets if s["kind"] == "drawing")
    has_specification = any(s["kind"] == "schedule" for s in sheets)
    has_cable_journal = any(s["kind"] == "cable_journal" for s in sheets)
    norms = parse_referenced_norms(text)
    return {
        "volume_code": volume_code,
        "section": section_of(volume_code or ""),
        "sheets": sheets,
        "drawings": drawings,
        "has_specification": has_specification,
        "has_cable_journal": has_cable_journal,
        "referenced_volumes": referenced_volumes,
        "norms": norms,
    }
