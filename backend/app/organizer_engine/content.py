from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


MAX_EXTRACTED_CHARS = 50_000
XLSX_COORDINATE_MARKER = "__PU_SOURCE_COORD__"
OCR_MIN_NATIVE_CHARS = 40
OCR_MAX_PAGES = max(1, min(50, int(os.getenv("OCR_MAX_PAGES", "20"))))
OCR_TIMEOUT_SECONDS = max(10, min(300, int(os.getenv("OCR_TIMEOUT_SECONDS", "120"))))
OCR_DPI = max(200, min(400, int(os.getenv("OCR_DPI", "300"))))
OCR_PSM = max(1, min(13, int(os.getenv("OCR_PSM", "1"))))
OCR_FALLBACK_PSM = max(1, min(13, int(os.getenv("OCR_FALLBACK_PSM", "6"))))
OCR_ADAPTIVE_FALLBACK = os.getenv("OCR_ADAPTIVE_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}
OCR_FALLBACK_MAX_PAGES = max(0, min(5, int(os.getenv("OCR_FALLBACK_MAX_PAGES", "2"))))
OCR_REVIEW_CONFIDENCE = max(0.0, min(1.0, float(os.getenv("OCR_REVIEW_CONFIDENCE", "0.72"))))

OCR_ORPHAN_ENDINGS = frozenset({"ый", "ий", "ая", "яя", "ое", "ые", "ую", "юю", "ых", "ым", "ть", "ться", "го"})
NUMBERED_PROSE_RE = re.compile(
    r"(?ms)^[ \t]*(?P<number>\d{1,2})[.)]\s+(?P<body>.*?)"
    r"(?=^[ \t]*\d{1,2}[.)]\s+|^[ \t]*(?:№|Артикул|Сумма(?:\s|$)|Итого:)|\Z)"
)


@dataclass(slots=True)
class OcrToken:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]
    line_id: tuple[int, int, int]


@dataclass(slots=True)
class PageExtraction:
    page: int
    text: str
    confidence: float
    method: str
    width: int = 0
    height: int = 0
    tokens: list[OcrToken] = field(default_factory=list)
    preprocessing: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FieldEvidence:
    field: str
    value: str
    page: int
    confidence: float
    excerpt: str
    bbox: tuple[int, int, int, int] | None = None


@dataclass(slots=True)
class TableCell:
    page: int
    row: int
    column: int
    text: str
    bbox: tuple[int, int, int, int]
    confidence: float


@dataclass(slots=True)
class ExtractionResult:
    text: str
    method: str
    quality: str
    total_pages: int = 0
    ocr_pages: int = 0
    confidence: float = 0.0
    pages: list[PageExtraction] = field(default_factory=list)
    fields: dict[str, list[FieldEvidence]] = field(default_factory=dict)
    table_cells: list[TableCell] = field(default_factory=list)
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 4),
            "needs_review": self.needs_review,
            "pages": [
                {
                    "page": page.page, "confidence": round(page.confidence, 4),
                    "method": page.method, "width": page.width, "height": page.height,
                    "preprocessing": page.preprocessing, "excerpt": page.text[:500],
                }
                for page in self.pages
            ],
            "fields": {
                name: [
                    {
                        "value": evidence.value, "page": evidence.page,
                        "confidence": round(evidence.confidence, 4),
                        "excerpt": evidence.excerpt, "bbox": evidence.bbox,
                    }
                    for evidence in values
                ]
                for name, values in self.fields.items()
            },
            "table_cells": [
                {
                    "page": cell.page, "row": cell.row, "column": cell.column,
                    "text": cell.text, "bbox": cell.bbox,
                    "confidence": round(cell.confidence, 4),
                }
                for cell in self.table_cells[:2000]
            ],
            "warnings": self.warnings,
        }


def _xml_text(data: bytes) -> str:
    root = ElementTree.fromstring(data)
    return " ".join(text.strip() for text in root.itertext() if text.strip())


def _zip_xml_text(data: bytes, prefixes: tuple[str, ...]) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.endswith(".xml") and name.startswith(prefixes):
                try:
                    parts.append(_xml_text(archive.read(name)))
                except ElementTree.ParseError:
                    continue
    return " ".join(parts)


def _xlsx_column_index(reference: str | None, fallback: int) -> int:
    match = re.match(r"([A-Za-z]+)", reference or "")
    if not match:
        return fallback
    value = 0
    for character in match.group(1).upper():
        value = value * 26 + ord(character) - ord("A") + 1
    return max(0, value - 1)


def _xlsx_date_style_indexes(archive: zipfile.ZipFile) -> set[int]:
    if "xl/styles.xml" not in archive.namelist():
        return set()
    root = ElementTree.fromstring(archive.read("xl/styles.xml"))
    custom_formats: dict[int, str] = {}
    for node in root.iter():
        if node.tag.endswith("}numFmt"):
            try:
                custom_formats[int(node.attrib.get("numFmtId", ""))] = node.attrib.get("formatCode", "")
            except ValueError:
                continue
    date_format_ids = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
    for format_id, code in custom_formats.items():
        normalized = re.sub(r'"[^"]*"|\\.|_.|\*.', "", code.casefold())
        if re.search(r"(?:^|[^a-z])[dmyhs]+(?:[^a-z]|$)", normalized):
            date_format_ids.add(format_id)
    cell_xfs = next((node for node in root.iter() if node.tag.endswith("}cellXfs")), None)
    if cell_xfs is None:
        return set()
    result: set[int] = set()
    for index, node in enumerate(child for child in cell_xfs if child.tag.endswith("}xf")):
        try:
            if int(node.attrib.get("numFmtId", "0")) in date_format_ids:
                result.add(index)
        except ValueError:
            continue
    return result


def _xlsx_date_value(raw: str, *, date_1904: bool) -> str:
    try:
        serial = float(raw)
    except ValueError:
        return raw
    if not 0 <= serial <= 2_958_465:
        return raw
    epoch = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)
    value = epoch + timedelta(days=serial)
    if abs(serial - round(serial)) < 1e-9:
        return value.date().isoformat()
    return value.isoformat(timespec="seconds", sep=" ")


def _xlsx_text(data: bytes) -> str:
    """Preserve spreadsheet rows and columns for the structured import preview."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        date_1904 = False
        if "xl/workbook.xml" in archive.namelist():
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            properties = next((node for node in workbook.iter() if node.tag.endswith("}workbookPr")), None)
            date_1904 = bool(properties is not None and properties.attrib.get("date1904", "0").casefold() in {"1", "true"})
        date_styles = _xlsx_date_style_indexes(archive)
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [" ".join(node.itertext()).strip() for node in root]
        sheet_names: dict[str, str] = {}
        names = set(archive.namelist())
        if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {
                relation.attrib.get("Id", ""): relation.attrib.get("Target", "")
                for relation in relationships
            }
            for sheet in (node for node in workbook.iter() if node.tag.endswith("}sheet")):
                relationship_id = next(
                    (value for key, value in sheet.attrib.items() if key.endswith("}id")), ""
                )
                target = targets.get(relationship_id, "")
                if target:
                    target = target.lstrip("/")
                    path = target if target.startswith("xl/") else f"xl/{target}"
                    sheet_names[path] = sheet.attrib.get("name", "")
        lines: list[str] = []
        for name in sorted(item for item in archive.namelist() if item.startswith("xl/worksheets/") and item.endswith(".xml")):
            root = ElementTree.fromstring(archive.read(name))
            sheet_name = sheet_names.get(name) or name.rsplit("/", 1)[-1].removesuffix(".xml")
            for sequential_row, row in enumerate(
                (node for node in root.iter() if node.tag.endswith("}row")), start=1
            ):
                values: list[str] = []
                for cell in (node for node in row if node.tag.endswith("}c")):
                    column = _xlsx_column_index(cell.attrib.get("r"), len(values))
                    if column >= 200:
                        continue
                    if column >= len(values):
                        values.extend([""] * (column - len(values) + 1))
                    kind = cell.attrib.get("t")
                    raw = next((node.text or "" for node in cell.iter() if node.tag.endswith("}v")), "")
                    if kind == "s" and raw.isdigit() and int(raw) < len(shared):
                        value = shared[int(raw)]
                    elif kind == "inlineStr":
                        value = " ".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
                    elif kind in {None, "n"} and cell.attrib.get("s", "").isdigit() and int(cell.attrib["s"]) in date_styles:
                        value = _xlsx_date_value(raw, date_1904=date_1904)
                    else:
                        value = raw
                    values[column] = value.replace("\t", " ").replace("\n", " ").strip()
                if any(values):
                    while values and not values[-1]:
                        values.pop()
                    source_row = row.attrib.get("r") or str(sequential_row)
                    safe_sheet_name = sheet_name.replace("\t", " ").replace("\n", " ").strip()
                    values.append(f"{XLSX_COORDINATE_MARKER}:{safe_sheet_name}:{source_row}")
                    lines.append("\t".join(values))
        return "\n".join(lines)


def _xls_text(data: bytes) -> str:
    import xlrd

    workbook = xlrd.open_workbook(file_contents=data, on_demand=True)
    lines: list[str] = []
    for sheet in workbook.sheets():
        for row_index in range(min(sheet.nrows, 10_000)):
            values = [str(sheet.cell_value(row_index, column)).replace("\t", " ").replace("\n", " ").strip()
                      for column in range(min(sheet.ncols, 200))]
            if any(values):
                lines.append("\t".join(values))
    return "\n".join(lines)


def _legacy_doc_text(data: bytes) -> str:
    if not shutil.which("antiword"):
        return ""
    with tempfile.TemporaryDirectory(prefix="pu-doc-") as temp:
        source = Path(temp) / "source.doc"
        source.write_bytes(data)
        result = subprocess.run(["antiword", str(source)], capture_output=True, timeout=OCR_TIMEOUT_SECONDS, check=False)
        if result.returncode != 0:
            return ""
        for encoding in ("utf-8", "cp1251"):
            try:
                return result.stdout.decode(encoding)
            except UnicodeDecodeError:
                continue
        return result.stdout.decode("utf-8", errors="replace")


def _normalise_table_text(text: str) -> str:
    """Keep row/column boundaries used by schedule, budget and cash-flow importers."""
    lines: list[str] = []
    for line in text.splitlines():
        cells = [re.sub(r"[ \r\f\v]+", " ", cell).strip() for cell in line.split("\t")]
        if any(cells):
            lines.append("\t".join(cells))
    return "\n".join(lines)


def _ocr_enabled() -> bool:
    return os.getenv("OCR_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _projection_score(image) -> float:
    """Reward rotations that concentrate dark pixels into horizontal text rows."""
    width, height = image.size
    if not width or not height:
        return 0.0
    pixels = image.load()
    rows = []
    step = max(1, width // 1200)
    for y in range(height):
        rows.append(sum(1 for x in range(0, width, step) if pixels[x, y] < 180))
    mean = sum(rows) / max(1, len(rows))
    return sum((value - mean) ** 2 for value in rows) / max(1, len(rows))


def _detect_orientation(path: Path, timeout: float) -> int:
    if not shutil.which("tesseract"):
        return 0
    try:
        result = subprocess.run(
            ["tesseract", str(path), "stdout", "-l", "osd", "--psm", "0"],
            capture_output=True, text=True, timeout=max(1, timeout), check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    match = re.search(r"Rotate:\s*(0|90|180|270)", result.stdout + "\n" + result.stderr)
    return int(match.group(1)) if match else 0


def _preprocess_image(source: Path, target: Path, timeout: float) -> tuple[int, int, list[str]]:
    """Local deterministic preprocessing; originals are read-only and never replaced."""
    from PIL import Image, ImageFilter, ImageOps

    actions: list[str] = []
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("L")
    actions.append("grayscale")
    orientation = _detect_orientation(source, min(15, timeout))
    if orientation:
        image = image.rotate(orientation, expand=True, fillcolor=255)
        actions.append(f"autorotate:{orientation}")
    image = ImageOps.autocontrast(image, cutoff=1)
    actions.append("autocontrast")
    image = image.filter(ImageFilter.MedianFilter(size=3))
    actions.append("median_denoise")

    # A bounded projection search corrects typical scanner skew without OpenCV.
    preview = image.copy()
    preview.thumbnail((1400, 1400))
    best_angle, best_score = 0.0, _projection_score(preview)
    for angle in (-3.0, -2.0, -1.0, 1.0, 2.0, 3.0):
        candidate = preview.rotate(angle, expand=True, fillcolor=255)
        score = _projection_score(candidate)
        if score > best_score * 1.03:
            best_angle, best_score = angle, score
    if best_angle:
        image = image.rotate(best_angle, expand=True, fillcolor=255)
        actions.append(f"deskew:{best_angle:g}")
    image.save(target, format="PNG", optimize=True)
    return image.width, image.height, actions


def _parse_tsv(tsv: str) -> list[OcrToken]:
    tokens: list[OcrToken] = []
    lines = tsv.splitlines()
    if not lines:
        return tokens
    headers = lines[0].split("\t")
    positions = {name: index for index, name in enumerate(headers)}
    required = {"text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num"}
    if not required.issubset(positions):
        return tokens
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) < len(headers):
            continue
        text_value = values[positions["text"]].strip()
        if not text_value:
            continue
        try:
            raw_confidence = float(values[positions["conf"]])
            left, top = int(values[positions["left"]]), int(values[positions["top"]])
            width, height = int(values[positions["width"]]), int(values[positions["height"]])
            line_id = tuple(int(values[positions[name]]) for name in ("block_num", "par_num", "line_num"))
        except (TypeError, ValueError):
            continue
        if raw_confidence < 0:
            continue
        tokens.append(OcrToken(
            text=text_value, confidence=max(0.0, min(1.0, raw_confidence / 100.0)),
            bbox=(left, top, width, height), line_id=line_id,
        ))
    return tokens


def _page_from_tokens(page: int, tokens: list[OcrToken], width: int, height: int, actions: list[str]) -> PageExtraction:
    grouped: dict[tuple[int, int, int], list[OcrToken]] = {}
    for token in tokens:
        grouped.setdefault(token.line_id, []).append(token)
    lines = [" ".join(item.text for item in sorted(line, key=lambda token: token.bbox[0]))
             for _, line in sorted(grouped.items())]
    confidence = sum(token.confidence for token in tokens) / len(tokens) if tokens else 0.0
    return PageExtraction(
        page=page, text="\n".join(lines), confidence=confidence,
        method="ocr", width=width, height=height, tokens=tokens,
        preprocessing=actions,
    )


def _tesseract_page(
    path: Path,
    page: int = 1,
    timeout: float | None = None,
    *,
    psm: int | None = None,
) -> PageExtraction:
    if not shutil.which("tesseract"):
        return PageExtraction(page=page, text="", confidence=0.0, method="ocr")
    budget = max(1, timeout or OCR_TIMEOUT_SECONDS)
    processed = path.with_name(f"{path.stem}-processed.png")
    try:
        width, height, actions = _preprocess_image(path, processed, budget)
    except Exception:
        processed = path
        width, height, actions = 0, 0, ["preprocessing_failed"]
    result = subprocess.run(
        [
            "tesseract", str(processed), "stdout",
            "-l", os.getenv("OCR_LANGUAGES", "rus+eng"),
            "--psm", str(psm or OCR_PSM),
            "-c", "preserve_interword_spaces=1", "tsv",
        ],
        capture_output=True, text=True, timeout=budget, check=False,
    )
    tokens = _parse_tsv(result.stdout) if result.returncode == 0 else []
    return _page_from_tokens(page, tokens, width, height, actions)


def _run_tesseract(path: Path, psm: int, timeout: float) -> str:
    """Return plain OCR text for the bounded adaptive fallback path."""
    result = subprocess.run(
        [
            "tesseract", str(path), "stdout",
            "-l", os.getenv("OCR_LANGUAGES", "rus+eng"),
            "--psm", str(psm),
            "-c", "preserve_interword_spaces=1",
        ],
        capture_output=True,
        text=True,
        timeout=max(1, timeout),
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def _ocr_image_page(data: bytes, suffix: str) -> PageExtraction:
    with tempfile.TemporaryDirectory(prefix="pu-ocr-") as temp:
        source = Path(temp) / f"source.{suffix or 'png'}"
        source.write_bytes(data)
        return _tesseract_page(source, 1)


def _ocr_fragment_penalty(text: str) -> tuple[int, int]:
    """Return review-only fragmentation evidence and Cyrillic prose volume."""
    words = re.findall(r"(?iu)(?<![\w/-])[а-яё]+(?![\w/-])", text)
    endings = [word.casefold() for word in words if word.casefold() in OCR_ORPHAN_ENDINGS]
    return len(endings), sum(len(word) >= 3 for word in words)


def _numeric_tokens(text: str) -> list[str]:
    return re.findall(r"\d+", text)


def _non_fragment_corruption(text: str) -> int:
    controls = sum(unicodedata.category(char) in {"Cc", "Cs", "Co"} and not char.isspace() for char in text)
    mixed = sum(
        bool(re.search(r"[а-яё]", token, re.IGNORECASE)) and bool(re.search(r"[a-z]", token, re.IGNORECASE))
        for token in re.findall(r"[^\W_]+", text)
    )
    return text.count("\ufffd") + controls + mixed


def _looks_tabular(text: str) -> bool:
    table_rows = sum(
        bool(re.search(r"\d", line)) and ("|" in line or len(re.findall(r"\s{3,}", line)) >= 2)
        for line in text.splitlines()
    )
    return table_rows >= 2


def _preserve_primary_near_matches(primary: str, fallback: str) -> str:
    """Do not let a structural fallback introduce an undecidable one-letter edit.

    The adaptive pass is meant to join visibly fragmented prose. If two aligned
    alphabetic words differ by exactly one substitution, neither OCR result proves
    which spelling is faithful. Keeping the primary token is conservative and
    avoids silently changing already-readable evidence. Larger edits remain the
    fallback's responsibility, so fragments can still become complete words.
    """
    word_re = re.compile(r"(?iu)[^\W\d_]+")
    primary_matches = list(word_re.finditer(primary))
    fallback_matches = list(word_re.finditer(fallback))
    primary_words = [match.group().casefold() for match in primary_matches]
    fallback_words = [match.group().casefold() for match in fallback_matches]
    replacements: list[tuple[int, int, str]] = []
    for tag, old_start, old_end, new_start, new_end in SequenceMatcher(
        None, primary_words, fallback_words, autojunk=False
    ).get_opcodes():
        if tag != "replace":
            continue
        old_indices = range(old_start, old_end)
        new_indices = range(new_start, new_end)
        near_pairs = [
            (old_index, new_index)
            for old_index in old_indices
            for new_index in new_indices
            if len(primary_words[old_index]) >= 4
            and len(primary_words[old_index]) == len(fallback_words[new_index])
            and sum(
                left != right
                for left, right in zip(primary_words[old_index], fallback_words[new_index])
            ) == 1
            and primary_words[old_index] not in OCR_ORPHAN_ENDINGS
        ]
        # Preserve only unambiguous pairs inside the changed region. Fragment
        # repair often changes several neighboring tokens, so a useful pair need
        # not form a standalone SequenceMatcher opcode.
        for old_index, new_index in near_pairs:
            if sum(pair[0] == old_index for pair in near_pairs) != 1:
                continue
            if sum(pair[1] == new_index for pair in near_pairs) != 1:
                continue
            new_match = fallback_matches[new_index]
            replacements.append((new_match.start(), new_match.end(), primary_matches[old_index].group()))
    result = fallback
    for start, end, replacement in reversed(replacements):
        result = result[:start] + replacement + result[end:]
    return result


def _merge_safe_numbered_prose(primary: str, fallback: str) -> tuple[str, int]:
    """Replace only damaged numbered prose; never rewrite the surrounding table."""
    fallback_sections = {match.group("number"): match for match in NUMBERED_PROSE_RE.finditer(fallback)}
    replacements: list[tuple[int, int, str]] = []
    for match in NUMBERED_PROSE_RE.finditer(primary):
        candidate = fallback_sections.get(match.group("number"))
        if candidate is None:
            continue
        old_body, new_body = match.group("body"), candidate.group("body")
        old_penalty, old_words = _ocr_fragment_penalty(old_body)
        new_penalty, new_words = _ocr_fragment_penalty(new_body)
        old_volume = max(1, sum(character.isalnum() for character in old_body))
        new_volume = sum(character.isalnum() for character in new_body)
        if (
            old_penalty >= 2
            and old_words >= 12
            and new_penalty == 0
            and 0.75 <= new_volume / old_volume <= 1.6
            and _numeric_tokens(old_body) == _numeric_tokens(new_body)
            and _non_fragment_corruption(new_body) == 0
            and new_words >= old_words * 0.75
        ):
            replacements.append((
                match.start(), match.end(),
                _preserve_primary_near_matches(match.group(0), candidate.group(0)),
            ))
    merged = primary
    for start, end, replacement in reversed(replacements):
        merged = merged[:start] + replacement + merged[end:]
    return merged, len(replacements)


def _safe_whole_page_fallback(primary: str, fallback: str) -> bool:
    primary_penalty, primary_words = _ocr_fragment_penalty(primary)
    fallback_penalty, fallback_words = _ocr_fragment_penalty(fallback)
    primary_volume = max(1, sum(character.isalnum() for character in primary))
    fallback_volume = sum(character.isalnum() for character in fallback)
    return (
        not _looks_tabular(primary)
        and not _looks_tabular(fallback)
        and fallback_penalty == 0
        and 0.75 <= fallback_volume / primary_volume <= 1.35
        and _numeric_tokens(primary) == _numeric_tokens(fallback)
        and _non_fragment_corruption(fallback) == 0
        and fallback_words >= primary_words * 0.75
    )


def _tesseract(path: Path, timeout: float | None = None, *, allow_fallback: bool = True) -> str:
    if not shutil.which("tesseract"):
        return ""
    budget = max(1, timeout or OCR_TIMEOUT_SECONDS)
    started = time.monotonic()
    try:
        primary = _run_tesseract(path, OCR_PSM, budget)
    except (OSError, subprocess.SubprocessError):
        return ""
    primary_penalty, primary_words = _ocr_fragment_penalty(primary)
    primary_volume = sum(character.isalnum() for character in primary)
    remaining = budget - (time.monotonic() - started)
    if (
        not allow_fallback or not OCR_ADAPTIVE_FALLBACK or OCR_FALLBACK_PSM == OCR_PSM
        or (primary_volume >= 20 and (primary_penalty < 2 or primary_words < 20)) or remaining <= 1
    ):
        return primary

    try:
        fallback = _run_tesseract(path, OCR_FALLBACK_PSM, remaining)
    except (OSError, subprocess.SubprocessError):
        return primary
    fallback_volume = sum(character.isalnum() for character in fallback)
    if primary_volume < 20:
        if (
            fallback_volume >= 20
            and _numeric_tokens(primary) == _numeric_tokens(fallback)[:len(_numeric_tokens(primary))]
            and _non_fragment_corruption(fallback) <= 2
        ):
            return fallback
        return primary
    merged, replacements = _merge_safe_numbered_prose(primary, fallback)
    if replacements:
        return merged
    if _safe_whole_page_fallback(primary, fallback):
        return _preserve_primary_near_matches(primary, fallback)
    return primary


def _ocr_text(data: bytes, suffix: str, mime_type: str) -> str:
    """Run bounded local OCR. No document bytes leave the application host."""
    if not _ocr_enabled():
        return ""
    is_pdf = mime_type == "application/pdf" or suffix == "pdf"
    is_image = mime_type.startswith("image/") or suffix in {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"}
    if not (is_pdf or is_image):
        return ""
    with tempfile.TemporaryDirectory(prefix="pu-ocr-") as temp:
        temp_dir = Path(temp)
        if is_image:
            return _ocr_image_page(data, suffix).text
        if not shutil.which("pdftoppm"):
            return ""
        source = temp_dir / "source.pdf"
        source.write_bytes(data)
        prefix = temp_dir / "page"
        subprocess.run(
            [
                "pdftoppm", "-f", "1", "-l", str(OCR_MAX_PAGES),
                "-r", str(OCR_DPI), "-jpeg", str(source), str(prefix),
            ],
            capture_output=True,
            timeout=OCR_TIMEOUT_SECONDS,
            check=False,
        )
        return " ".join(
            _tesseract(page, allow_fallback=index < OCR_FALLBACK_MAX_PAGES)
            for index, page in enumerate(sorted(temp_dir.glob("page-*.jpg")))
        )


def _ocr_pdf_pages(data: bytes, page_numbers: set[int]) -> dict[int, PageExtraction]:
    """OCR selected one-based PDF pages within one total time budget."""
    if not page_numbers or not _ocr_enabled() or not shutil.which("pdftoppm"):
        return {}
    deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="pu-ocr-") as temp:
        temp_dir = Path(temp)
        source = temp_dir / "source.pdf"
        source.write_bytes(data)
        result: dict[int, PageExtraction] = {}
        for page_number in sorted(page_numbers):
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                break
            prefix = temp_dir / f"page-{page_number}"
            rendered = subprocess.run(
                [
                    "pdftoppm", "-f", str(page_number), "-l", str(page_number),
                    "-singlefile", "-r", str(OCR_DPI), "-jpeg", str(source), str(prefix),
                ],
                capture_output=True, timeout=remaining, check=False,
            )
            image = prefix.with_suffix(".jpg")
            if rendered.returncode == 0 and image.exists():
                remaining = deadline - time.monotonic()
                if remaining > 1:
                    text = _tesseract(
                        image,
                        remaining,
                        allow_fallback=len(result) < OCR_FALLBACK_MAX_PAGES,
                    )
                    result[page_number] = _coerce_page(text, page_number)
        return result


def _coerce_page(value: PageExtraction | str, page_number: int, method: str = "ocr") -> PageExtraction:
    if isinstance(value, PageExtraction):
        return value
    confidence = 0.8 if value.strip() else 0.0
    return PageExtraction(page=page_number, text=value, confidence=confidence, method=method)


def _native_page(page_number: int, text: str) -> PageExtraction:
    compact = "".join(text.split())
    confidence = 0.98 if len(compact) >= OCR_MIN_NATIVE_CHARS else 0.65 if compact else 0.0
    return PageExtraction(page=page_number, text=text, confidence=confidence, method="native")


def _excerpt(text: str, start: int, end: int, radius: int = 80) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - radius):min(len(text), end + radius)]).strip()


def _union_bbox(tokens: list[OcrToken], value: str) -> tuple[int, int, int, int] | None:
    normalized_value = re.sub(r"\W+", "", value, flags=re.UNICODE).casefold()
    matches = []
    for token in tokens:
        normalized_token = re.sub(r"\W+", "", token.text, flags=re.UNICODE).casefold()
        if normalized_token and (normalized_token in normalized_value or normalized_value in normalized_token):
            matches.append(token)
    if not matches:
        return None
    left = min(token.bbox[0] for token in matches)
    top = min(token.bbox[1] for token in matches)
    right = max(token.bbox[0] + token.bbox[2] for token in matches)
    bottom = max(token.bbox[1] + token.bbox[3] for token in matches)
    return left, top, right - left, bottom - top


_FIELD_PATTERNS = {
    "number": re.compile(r"(?:договор|контракт|сч[её]т|акт)\s*(?:№|N|номер)?\s*([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9./_-]{2,})", re.I),
    "date": re.compile(r"\b([0-3]?\d[./-][01]?\d[./-](?:19|20)\d{2})\b"),
    "amount": re.compile(r"\b(\d{1,3}(?:[\s\u00a0]\d{3})*(?:[,.]\d{1,2})?)\s*(?:руб(?:лей|ля|ль|\.)?|₽)", re.I),
    "party": re.compile(r"\b((?:ООО|АО|ПАО|ЗАО|ИП|ФКУ|ФГУП|МУП)\s+(?:«[^»\n]{2,80}»|\"[^\"\n]{2,80}\"|'[^'\n]{2,80}'|[А-ЯЁA-Z][^,;\n]{1,60}))", re.I),
}


def _extract_fields(pages: list[PageExtraction]) -> dict[str, list[FieldEvidence]]:
    result: dict[str, list[FieldEvidence]] = {}
    for page in pages:
        for name, pattern in _FIELD_PATTERNS.items():
            seen: set[str] = set()
            for match in pattern.finditer(page.text):
                value = match.group(1).strip(" .,:;\"'")
                normalized = value.casefold()
                if normalized in seen:
                    continue
                seen.add(normalized)
                confidence = page.confidence
                if name == "number" and len(value) < 4:
                    confidence *= 0.65
                result.setdefault(name, []).append(FieldEvidence(
                    field=name, value=value, page=page.page,
                    confidence=max(0.0, min(1.0, confidence)),
                    excerpt=_excerpt(page.text, match.start(), match.end()),
                    bbox=_union_bbox(page.tokens, value),
                ))
    return result


def _extract_table_cells(pages: list[PageExtraction]) -> list[TableCell]:
    """Foundation for table reconstruction: stable row/column coordinates from OCR tokens."""
    cells: list[TableCell] = []
    for page in pages:
        grouped: dict[tuple[int, int, int], list[OcrToken]] = {}
        for token in page.tokens:
            grouped.setdefault(token.line_id, []).append(token)
        row_number = 0
        for _, line in sorted(grouped.items()):
            ordered = sorted(line, key=lambda item: item.bbox[0])
            if len(ordered) < 2:
                continue
            heights = [max(1, item.bbox[3]) for item in ordered]
            gap_threshold = max(25, int(sum(heights) / len(heights) * 2.2))
            groups: list[list[OcrToken]] = [[ordered[0]]]
            for token in ordered[1:]:
                previous = groups[-1][-1]
                gap = token.bbox[0] - (previous.bbox[0] + previous.bbox[2])
                if gap > gap_threshold:
                    groups.append([])
                groups[-1].append(token)
            if len(groups) < 2:
                continue
            row_number += 1
            for column, group in enumerate(groups, start=1):
                left = min(item.bbox[0] for item in group)
                top = min(item.bbox[1] for item in group)
                right = max(item.bbox[0] + item.bbox[2] for item in group)
                bottom = max(item.bbox[1] + item.bbox[3] for item in group)
                cells.append(TableCell(
                    page=page.page, row=row_number, column=column,
                    text=" ".join(item.text for item in group),
                    bbox=(left, top, right - left, bottom - top),
                    confidence=sum(item.confidence for item in group) / len(group),
                ))
    return cells


def _result_confidence(pages: list[PageExtraction], fields: dict[str, list[FieldEvidence]]) -> float:
    nonempty = [page.confidence for page in pages if page.text.strip()]
    if not nonempty:
        return 0.0
    page_score = sum(nonempty) / len(nonempty)
    field_scores = [item.confidence for values in fields.values() for item in values]
    return max(0.0, min(1.0, page_score * 0.8 + (sum(field_scores) / len(field_scores) if field_scores else page_score) * 0.2))


def _quality(text: str, *, used_ocr: bool) -> str:
    compact = "".join(text.split())
    if not compact:
        return "empty"
    readable = sum(character.isalnum() for character in compact) / len(compact)
    # A short invoice heading with a number and amount can be useful even below
    # the native-text threshold; reserve `low` for fragments/noise.
    if len(compact) < 20 or readable < 0.45:
        return "low"
    if used_ocr and (len(compact) < 150 or readable < 0.65):
        return "medium"
    return "high"


def extract_text_result(data: bytes, mime_type: str, filename: str = "") -> ExtractionResult:
    """Extract text plus provider-neutral quality metadata."""
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    is_pdf = mime_type == "application/pdf" or suffix == "pdf"
    if not is_pdf:
        text = _extract_native_text(data, mime_type, filename)
        used_ocr = False
        page = _native_page(1, text)
        if len(text.strip()) < OCR_MIN_NATIVE_CHARS:
            try:
                is_image = mime_type.startswith("image/") or suffix in {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"}
                if is_image:
                    ocr_page = _ocr_image_page(data, suffix)
                    # Keep the legacy bounded OCR seam as a fail-safe. It also
                    # preserves compatibility for deployments that override the
                    # text-only OCR implementation without structured TSV data.
                    if not ocr_page.text.strip():
                        ocr_page = _coerce_page(_ocr_text(data, suffix, mime_type), 1)
                else:
                    ocr_page = _coerce_page(_ocr_text(data, suffix, mime_type), 1)
                if len(ocr_page.text.strip()) > len(text.strip()):
                    text, page, used_ocr = ocr_page.text, ocr_page, True
            except (OSError, subprocess.SubprocessError):
                pass
        finalized = _finalize_text(text, suffix, mime_type)
        page.text = finalized
        pages = [page]
        fields = _extract_fields(pages)
        confidence = _result_confidence(pages, fields)
        return ExtractionResult(
            text=finalized,
            method="ocr" if used_ocr else "native",
            quality=_quality(text, used_ocr=used_ocr),
            ocr_pages=1 if used_ocr else 0,
            confidence=confidence, pages=pages, fields=fields,
            table_cells=_extract_table_cells(pages),
            needs_review=used_ocr and confidence < OCR_REVIEW_CONFIDENCE,
        )

    from pypdf import PdfReader
    warnings: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        total_pages = len(reader.pages)
        native_pages = [(page.extract_text() or "") for page in reader.pages[:100]]
    except Exception as exc:
        total_pages = 0
        native_pages = []
        warnings.append(f"native_pdf_failed:{exc.__class__.__name__}")
    limit = min(len(native_pages) or OCR_MAX_PAGES, OCR_MAX_PAGES)
    weak_pages = {
        index + 1 for index, text in enumerate(native_pages[:limit])
        if len("".join(text.split())) < OCR_MIN_NATIVE_CHARS
    }
    if not native_pages:
        weak_pages = set(range(1, OCR_MAX_PAGES + 1))
    try:
        recognized = _ocr_pdf_pages(data, weak_pages)
    except (OSError, subprocess.SubprocessError):
        recognized = {}
        warnings.append("ocr_failed")
    page_results: list[PageExtraction] = []
    if native_pages:
        selected_text: list[str] = []
        for index, native in enumerate(native_pages):
            ocr = _coerce_page(recognized.get(index + 1, ""), index + 1)
            if len(ocr.text.strip()) > len(native.strip()):
                page_results.append(ocr)
                selected_text.append(ocr.text)
            else:
                native_result = _native_page(index + 1, native)
                page_results.append(native_result)
                selected_text.append(native)
        text = "\n\n".join(selected_text)
    else:
        page_results = [_coerce_page(recognized[index], index) for index in sorted(recognized)]
        text = "\n\n".join(page.text for page in page_results)
    used_ocr = any(page.method == "ocr" and page.text.strip() for page in page_results)
    method = "hybrid" if used_ocr and any(page.strip() for page in native_pages) else "ocr" if used_ocr else "native"
    finalized = _finalize_text(text, suffix, mime_type)
    if total_pages > OCR_MAX_PAGES and weak_pages:
        warnings.append(f"ocr_page_limit:{OCR_MAX_PAGES}")
    fields = _extract_fields(page_results)
    confidence = _result_confidence(page_results, fields)
    needs_review = used_ocr and confidence < OCR_REVIEW_CONFIDENCE
    if needs_review:
        warnings.append("manual_review_required")
    return ExtractionResult(
        text=finalized, method=method, quality=_quality(finalized, used_ocr=used_ocr),
        total_pages=total_pages,
        ocr_pages=len([value for value in recognized.values() if _coerce_page(value, 0).text.strip()]),
        confidence=confidence, pages=page_results, fields=fields,
        table_cells=_extract_table_cells(page_results), needs_review=needs_review,
        warnings=warnings,
    )


def _extract_native_text(data: bytes, mime_type: str, filename: str = "") -> str:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    text = ""
    if suffix == "docx" or mime_type.endswith("wordprocessingml.document"):
        text = _zip_xml_text(data, ("word/document.xml", "word/header", "word/footer"))
    elif suffix == "xlsx" or mime_type.endswith("spreadsheetml.sheet"):
        text = _xlsx_text(data)
    elif suffix == "xls" or mime_type in {"application/vnd.ms-excel", "application/xls"}:
        text = _xls_text(data)
    elif suffix == "doc" or mime_type == "application/msword":
        text = _legacy_doc_text(data)
    elif mime_type.startswith("text/") or suffix in {"txt", "csv", "md", "log"}:
        text = data.decode("utf-8", errors="replace")
    return text


def _finalize_text(text: str, suffix: str, mime_type: str) -> str:
    if suffix in {"xls", "xlsx", "csv"} or mime_type in {
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    }:
        text = _normalise_table_text(text)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_EXTRACTED_CHARS]


def extract_text(data: bytes, mime_type: str, filename: str = "") -> str:
    """Backward-compatible plain-text facade for Document Core consumers."""
    return extract_text_result(data, mime_type, filename).text
