import io
import re

from pypdf import PdfReader


def split_pages(pdf_bytes: bytes, max_pages: int = 400) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise ValueError("Файл не читается как PDF") from exc
    try:
        pages = list(reader.pages)
    except Exception as exc:
        raise ValueError("Файл не читается как PDF") from exc
    result: list[str] = []
    for page in pages[:max_pages]:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        result.append(text)
    return result


_SPEC_PHRASE_RE = re.compile(
    r"спецификация\s+оборудования,?\s+изделий\s+и\s+материалов",
    re.IGNORECASE,
)
_TABLE_HEADER_RE = re.compile(r"поз.*наименование", re.IGNORECASE | re.DOTALL)
_POS_LINE_RE = re.compile(r"^\s*\d+(\.\d+)*\s")


def _norm_page(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


def _has_table_header(text: str) -> bool:
    return bool(_TABLE_HEADER_RE.search(text))


def _is_spec_start(text: str) -> bool:
    norm = _norm_page(text)
    if not _SPEC_PHRASE_RE.search(norm):
        return False
    return _has_table_header(norm)


def _count_pos_lines(text: str) -> int:
    count = 0
    for line in (text or "").split("\n"):
        if _POS_LINE_RE.match(line):
            count += 1
    return count


def _is_spec_continuation(text: str) -> bool:
    if not text:
        return False
    if _count_pos_lines(text) >= 3:
        return True
    return _has_table_header(_norm_page(text))


def find_spec_pages(pages: list[str]) -> list[int]:
    n = len(pages)
    result: list[int] = []
    i = 0
    while i < n:
        if not _is_spec_start(pages[i]):
            i += 1
            continue
        result.append(i + 1)
        j = i + 1
        empty_run = 0
        last_added = i
        while j < n:
            text = pages[j] or ""
            if not text.strip():
                empty_run += 1
                if empty_run >= 2:
                    break
                j += 1
                continue
            if _is_spec_continuation(text):
                empty_run = 0
                result.append(j + 1)
                last_added = j
                j += 1
                continue
            break
        i = last_added + 1
    return result


def spec_pages_label(pages_numbers: list[int]) -> str:
    if not pages_numbers:
        return ""
    numbers = sorted(set(pages_numbers))
    ranges: list[tuple[int, int]] = []
    start = numbers[0]
    prev = numbers[0]
    for num in numbers[1:]:
        if num == prev + 1:
            prev = num
            continue
        ranges.append((start, prev))
        start = num
        prev = num
    ranges.append((start, prev))
    parts: list[str] = []
    for a, b in ranges:
        if a == b:
            parts.append(str(a))
        else:
            parts.append(f"{a}-{b}")
    return ",".join(parts)


def spec_text(pages: list[str], numbers: list[int]) -> str:
    chunks: list[str] = []
    for num in numbers:
        idx = num - 1
        if 0 <= idx < len(pages):
            chunks.append(pages[idx] or "")
    return "\n".join(chunks)
