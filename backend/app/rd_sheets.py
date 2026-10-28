"""Pure helpers for classifying RD volume pages and building a sheet register."""
from __future__ import annotations

_DRAWING_MARKERS = (
    "изм.",
    "кол.уч",
    "лист",
    "подп.",
    "разраб.",
    "н. контр",
    "стадия",
    "листов",
)

_TITLE_KEYWORDS = (
    "схема",
    "план",
    "разрез",
    "узел",
    "таблица",
    "ведомость",
    "спецификация",
)


def _meaningful_len(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def classify_page(text: str, is_spec: bool) -> str:
    if is_spec:
        return "spec"
    if text is None:
        return "blank"
    cleaned = text.strip()
    if _meaningful_len(cleaned) < 20:
        return "blank"
    lower = cleaned.lower()
    markers_found = sum(1 for m in _DRAWING_MARKERS if m in lower)
    if markers_found >= 3:
        return "drawing"
    return "text"


def _extract_sheet_no(text: str) -> str | None:
    lower = text.lower()
    idx = 0
    word = "лист"
    while True:
        pos = lower.find(word, idx)
        if pos < 0:
            return None
        after = pos + len(word)
        rest = text[after:]
        # Skip optional ':' and whitespace
        i = 0
        while i < len(rest) and rest[i] in ":. \t":
            i += 1
        # Collect digits
        j = i
        while j < len(rest) and rest[j].isdigit():
            j += 1
        if j > i:
            return rest[i:j]
        idx = after


def _extract_title(text: str) -> str | None:
    lines = [ln.strip() for ln in text.splitlines()]
    # Look for "Наименование листа" / "Наименование" markers
    for i, ln in enumerate(lines):
        low = ln.lower()
        marker = None
        if "наименование листа" in low:
            marker = "наименование листа"
        elif "наименование" in low:
            marker = "наименование"
        if marker is None:
            continue
        # Remainder on the same line after marker + optional ':'?
        after = ln[low.find(marker) + len(marker):]
        after = after.lstrip(": \t")
        if after.strip():
            return after.strip()[:200]
        # Take the next non-empty line(s)
        for j in range(i + 1, len(lines)):
            if lines[j]:
                return lines[j][:200]
    # Fallback: first line containing a title keyword
    for ln in lines:
        low = ln.lower()
        if any(k in low for k in _TITLE_KEYWORDS):
            return ln[:200]
    return None


def parse_stamp(text: str) -> dict:
    result: dict = {"sheet_no": None, "sheet_title": None}
    if not text or not isinstance(text, str):
        return result
    try:
        result["sheet_no"] = _extract_sheet_no(text)
    except Exception:
        result["sheet_no"] = None
    try:
        result["sheet_title"] = _extract_title(text)
    except Exception:
        result["sheet_title"] = None
    return result


def build_register(pages: list[str], spec_pages: list[int], sources: list[str]) -> list[dict]:
    spec_set = set(spec_pages or [])
    result: list[dict] = []
    for i, text in enumerate(pages):
        page_no = i + 1
        clean = (text or "").strip()
        is_spec = page_no in spec_set
        kind = classify_page(text or "", is_spec)
        stamp = parse_stamp(text or "")
        if i < len(sources):
            source = sources[i]
        else:
            source = "none"
        result.append({
            "page_no": page_no,
            "kind": kind,
            "sheet_no": stamp.get("sheet_no"),
            "sheet_title": stamp.get("sheet_title"),
            "text_source": source,
            "text_len": len(clean),
        })
    return result
