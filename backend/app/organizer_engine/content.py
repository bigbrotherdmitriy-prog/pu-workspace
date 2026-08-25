from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree


MAX_EXTRACTED_CHARS = 50_000


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
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_EXTRACTED_CHARS]
