from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree


MAX_EXTRACTED_CHARS = 50_000
XLSX_COORDINATE_MARKER = "__PU_SOURCE_COORD__"
OCR_MIN_NATIVE_CHARS = 40
OCR_MAX_PAGES = max(1, min(50, int(os.getenv("OCR_MAX_PAGES", "20"))))
OCR_TIMEOUT_SECONDS = max(10, min(300, int(os.getenv("OCR_TIMEOUT_SECONDS", "120"))))
OCR_DPI = max(200, min(400, int(os.getenv("OCR_DPI", "300"))))
OCR_PSM = max(1, min(13, int(os.getenv("OCR_PSM", "1"))))


@dataclass(slots=True)
class ExtractionResult:
    text: str
    method: str
    quality: str
    total_pages: int = 0
    ocr_pages: int = 0
    warnings: list[str] = field(default_factory=list)


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


def _xlsx_text(data: bytes) -> str:
    """Preserve spreadsheet rows and columns for the structured import preview."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
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
                    kind = cell.attrib.get("t")
                    raw = next((node.text or "" for node in cell.iter() if node.tag.endswith("}v")), "")
                    if kind == "s" and raw.isdigit() and int(raw) < len(shared):
                        value = shared[int(raw)]
                    elif kind == "inlineStr":
                        value = " ".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
                    else:
                        value = raw
                    values.append(value.replace("\t", " ").replace("\n", " ").strip())
                if any(values):
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


def _tesseract(path: Path, timeout: float | None = None) -> str:
    if not shutil.which("tesseract"):
        return ""
    result = subprocess.run(
        [
            "tesseract", str(path), "stdout",
            "-l", os.getenv("OCR_LANGUAGES", "rus+eng"),
            "--psm", str(OCR_PSM),
            "-c", "preserve_interword_spaces=1",
        ],
        capture_output=True,
        text=True,
        timeout=max(1, timeout or OCR_TIMEOUT_SECONDS),
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


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
            source = temp_dir / f"source.{suffix or 'png'}"
            source.write_bytes(data)
            return _tesseract(source)
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
        return " ".join(_tesseract(page) for page in sorted(temp_dir.glob("page-*.jpg")))


def _ocr_pdf_pages(data: bytes, page_numbers: set[int]) -> dict[int, str]:
    """OCR selected one-based PDF pages within one total time budget."""
    if not page_numbers or not _ocr_enabled() or not shutil.which("pdftoppm"):
        return {}
    deadline = time.monotonic() + OCR_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="pu-ocr-") as temp:
        temp_dir = Path(temp)
        source = temp_dir / "source.pdf"
        source.write_bytes(data)
        result: dict[int, str] = {}
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
                    result[page_number] = _tesseract(image, remaining)
        return result


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
        if len(text.strip()) < OCR_MIN_NATIVE_CHARS:
            try:
                ocr_text = _ocr_text(data, suffix, mime_type)
                if len(ocr_text.strip()) > len(text.strip()):
                    text, used_ocr = ocr_text, True
            except (OSError, subprocess.SubprocessError):
                pass
        return ExtractionResult(
            text=_finalize_text(text, suffix, mime_type),
            method="ocr" if used_ocr else "native",
            quality=_quality(text, used_ocr=used_ocr),
            ocr_pages=1 if used_ocr else 0,
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
    if native_pages:
        pages = []
        for index, native in enumerate(native_pages):
            ocr = recognized.get(index + 1, "")
            pages.append(ocr if len(ocr.strip()) > len(native.strip()) else native)
        text = "\n\n".join(pages)
    else:
        text = "\n\n".join(recognized[index] for index in sorted(recognized))
    used_ocr = bool(recognized)
    method = "hybrid" if used_ocr and any(page.strip() for page in native_pages) else "ocr" if used_ocr else "native"
    finalized = _finalize_text(text, suffix, mime_type)
    if total_pages > OCR_MAX_PAGES and weak_pages:
        warnings.append(f"ocr_page_limit:{OCR_MAX_PAGES}")
    return ExtractionResult(
        text=finalized, method=method, quality=_quality(finalized, used_ocr=used_ocr),
        total_pages=total_pages, ocr_pages=len([value for value in recognized.values() if value.strip()]),
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
