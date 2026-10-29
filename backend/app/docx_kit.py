"""Минимальная сборка .docx из готовых абзацев и таблиц (без внешних зависимостей)."""
from __future__ import annotations

import io
import zipfile

from app.acceptance_act_document import _CONTENT_TYPES, _DOC_RELS, _RELS, _W_NS


def build_docx(parts: list[str], landscape: bool = False) -> bytes:
    if landscape:
        sect = (
            '<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
            '<w:pgMar w:top="850" w:right="850" w:bottom="850" w:left="1134" '
            'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        )
    else:
        sect = (
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701" '
            'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>' + "".join(parts) + sect + "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
    return buf.getvalue()


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
