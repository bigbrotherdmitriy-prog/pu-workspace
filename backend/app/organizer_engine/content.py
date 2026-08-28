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
        text = _zip_xml_text(data, ("xl/sharedStrings.xml", "xl/worksheets/"))
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
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_EXTRACTED_CHARS]
