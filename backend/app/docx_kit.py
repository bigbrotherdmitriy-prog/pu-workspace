"""Минимальная сборка .docx из готовых абзацев и таблиц (без внешних зависимостей)."""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape, quoteattr

from app.acceptance_act_document import _CONTENT_TYPES, _DOC_RELS, _RELS, _W_NS

_CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CUSTOM_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
_CUSTOM_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.custom-properties+xml"


def _is_xml1_forbidden_char(ch: str) -> bool:
    code = ord(ch)
    if code in (0x9, 0xA, 0xD):
        return False
    if code < 0x20:
        return True
    if 0xD800 <= code <= 0xDFFF:
        return True
    if code in (0xFFFE, 0xFFFF):
        return True
    return False


def _normalize_template_value(name: str, value: object) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise ValueError(f"{name} must be a non-empty string")
    for ch in value:
        if _is_xml1_forbidden_char(ch):
            raise ValueError(f"{name} contains characters not allowed in XML 1.0")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _custom_properties_xml(template_id: str, template_version: str) -> str:
    props = (
        ("2", "PU.TemplateId", template_id),
        ("3", "PU.TemplateVersion", template_version),
    )
    body = "".join(
        f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{pid}" name={quoteattr(name)}>'
        f'<vt:lpwstr>{escape(value)}</vt:lpwstr></property>'
        for pid, name, value in props
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Properties xmlns="{_CUSTOM_NS}" xmlns:vt="{_VT_NS}">{body}</Properties>'
    )


def _with_custom_properties(content_types: str, package_rels: str) -> tuple[str, str]:
    override = (
        f'<Override PartName="/docProps/custom.xml" ContentType="{_CUSTOM_CONTENT_TYPE}"/>'
    )
    if "</Types>" in content_types:
        content_types = content_types.replace("</Types>", override + "</Types>", 1)
    else:
        content_types = content_types + override
    rel = f'<Relationship Id="rIdCustomProps" Type="{_CUSTOM_REL_TYPE}" Target="docProps/custom.xml"/>'
    if "</Relationships>" in package_rels:
        package_rels = package_rels.replace("</Relationships>", rel + "</Relationships>", 1)
    else:
        package_rels = package_rels + rel
    return content_types, package_rels


def build_docx(
    parts: list[str],
    landscape: bool = False,
    *,
    template_id: str | None = None,
    template_version: str | None = None,
) -> bytes:
    if (template_id is None) != (template_version is None):
        raise ValueError("template_id and template_version must be supplied together")
    custom_xml = None
    if template_id is not None and template_version is not None:
        norm_id = _normalize_template_value("template_id", template_id)
        norm_version = _normalize_template_value("template_version", template_version)
        custom_xml = _custom_properties_xml(norm_id, norm_version)

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
    content_types = _CONTENT_TYPES
    package_rels = _RELS
    if custom_xml is not None:
        content_types, package_rels = _with_custom_properties(content_types, package_rels)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", package_rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        if custom_xml is not None:
            zf.writestr("docProps/custom.xml", custom_xml)
    return buf.getvalue()


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
