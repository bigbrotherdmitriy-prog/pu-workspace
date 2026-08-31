from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree


MAX_EXTRACTED_CHARS = 50_000
OCR_MIN_NATIVE_CHARS = 40
OCR_MAX_PAGES = max(1, min(50, int(os.getenv("OCR_MAX_PAGES", "20"))))
OCR_TIMEOUT_SECONDS = max(10, min(300, int(os.getenv("OCR_TIMEOUT_SECONDS", "120"))))


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
        lines: list[str] = []
        for name in sorted(item for item in archive.namelist() if item.startswith("xl/worksheets/") and item.endswith(".xml")):
            root = ElementTree.fromstring(archive.read(name))
            for row in (node for node in root.iter() if node.tag.endswith("}row")):
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


def _tesseract(path: Path) -> str:
    if not shutil.which("tesseract"):
        return ""
    result = subprocess.run(
        ["tesseract", str(path), "stdout", "-l", os.getenv("OCR_LANGUAGES", "rus+eng")],
        capture_output=True,
        text=True,
        timeout=OCR_TIMEOUT_SECONDS,
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
                "-r", "200", "-jpeg", str(source), str(prefix),
            ],
            capture_output=True,
            timeout=OCR_TIMEOUT_SECONDS,
            check=False,
        )
        return " ".join(_tesseract(page) for page in sorted(temp_dir.glob("page-*.jpg")))


def extract_text(data: bytes, mime_type: str, filename: str = "") -> str:
    """Extract bounded plain text without executing embedded document content."""
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    text = ""
    if mime_type == "application/pdf" or suffix == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        text = " ".join((page.extract_text() or "") for page in reader.pages[:100])
    elif suffix == "docx" or mime_type.endswith("wordprocessingml.document"):
        text = _zip_xml_text(data, ("word/document.xml", "word/header", "word/footer"))
    elif suffix == "xlsx" or mime_type.endswith("spreadsheetml.sheet"):
        text = _xlsx_text(data)
    elif suffix == "xls" or mime_type in {"application/vnd.ms-excel", "application/xls"}:
        text = _xls_text(data)
    elif suffix == "doc" or mime_type == "application/msword":
        text = _legacy_doc_text(data)
    elif mime_type.startswith("text/") or suffix in {"txt", "csv", "md", "log"}:
        text = data.decode("utf-8", errors="replace")
    if len(text.strip()) < OCR_MIN_NATIVE_CHARS:
        try:
            ocr_text = _ocr_text(data, suffix, mime_type)
            if len(ocr_text.strip()) > len(text.strip()):
                text = ocr_text
        except (OSError, subprocess.SubprocessError):
            # OCR is a best-effort local fallback; native extraction remains valid.
            pass
    if suffix in {"xls", "xlsx", "csv"} or mime_type in {
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv",
    }:
        text = _normalise_table_text(text)
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_EXTRACTED_CHARS]
