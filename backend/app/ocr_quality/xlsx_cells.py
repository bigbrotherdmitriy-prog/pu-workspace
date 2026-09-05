"""Bounded XLSX cell extraction without formula evaluation or external I/O.

Cell locators describe this workbook representation only. The caller still
owns source-version/integrity/access pins before creating durable Evidence.
"""
from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_UNPACKED_BYTES = 20 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_SHEETS = 100
MAX_CELLS = 20_000
MAX_SHARED_STRINGS = 20_000
MAX_VALUE_CHARS = 32_767
MAX_VALUE_BUDGET = 1_000_000
MAX_TSV_CHARS = 50_000
MAX_XML_NODES = 100_000
MAX_XML_DEPTH = 128
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
STRICT_NS = "http://purl.oclc.org/ooxml/spreadsheetml/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
STRICT_REL_NS = "http://purl.oclc.org/ooxml/officeDocument/relationships"
PACKAGE_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6})\Z")


class XlsxExtractionError(ValueError):
    """Only fixed content-free codes escape the parser boundary."""


@dataclass(slots=True)
class XlsxExtraction:
    text: str = ""
    cells: list[dict] = field(default_factory=list)
    sheets: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_review: bool = False

    def warn(self, code):
        if code not in self.warnings:
            self.warnings.append(code)
        self.needs_review = True


def _deny(code="xlsx_package_unavailable"):
    raise XlsxExtractionError(code)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _children(node, name):
    prefix = node.tag.rsplit("}", 1)[0] + "}" if "}" in node.tag else ""
    return node.findall(prefix + name)


def _one(node, name):
    matches = _children(node, name)
    if len(matches) > 1:
        _deny("xlsx_xml_ambiguous")
    return matches[0] if matches else None


def _path(name):
    if (not name or len(name) > 255 or "\\" in name or ":" in name
            or name.startswith("/") or any(ord(c) < 32 for c in name)
            or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))):
        _deny("xlsx_path_unavailable")
    return name


def _target(target):
    # Support package-absolute OPC targets, never filesystem absolute paths.
    if not isinstance(target, str) or any(c in target for c in ("%", "?", "#")):
        _deny("xlsx_path_unavailable")
    return _path(target[1:] if target.startswith("/xl/") else "xl/" + target)


def _xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        _deny("xlsx_size_limit")
    with archive.open(info) as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        _deny("xlsx_size_limit")
    # UTF-8 is the supported OOXML encoding in this bounded parser. Rejecting
    # other encodings avoids declaration obfuscation and never resolves DTDs.
    text = data.decode("utf-8-sig")
    if "\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
        _deny("xlsx_xml_declaration_denied")
    depth = nodes = 0
    parser = ET.iterparse(io.StringIO(text), events=("start", "end"))
    for event, _node in parser:
        if event == "start":
            depth += 1
            nodes += 1
            if depth > MAX_XML_DEPTH or nodes > MAX_XML_NODES:
                _deny("xlsx_xml_limit")
        else:
            depth -= 1
    return parser.root


def _text(node):
    if node is None:
        return None
    # Rich-text runs concatenate exactly; phonetic annotations are not values.
    pieces = []
    prefix = node.tag.rsplit("}", 1)[0] + "}" if "}" in node.tag else ""
    for part in node:
        if part.tag == prefix + "t":
            pieces.append(part.text or "")
        elif part.tag == prefix + "r":
            pieces.extend(text.text or "" for text in _children(part, "t"))
    result = "".join(pieces)
    if len(result) > MAX_VALUE_CHARS:
        _deny("xlsx_value_limit")
    return result


def _sheet_parts(archive, result):
    names = set(archive.namelist())
    if "xl/workbook.xml" not in names:
        if "xl/_rels/workbook.xml.rels" in names:
            _deny()
        # Compatibility with the existing worksheet-only TSV fixture/input.
        # Missing identity is explicitly unverified; no sheet/cell pin invented.
        result.warn("xlsx_workbook_identity_missing")
        parts = sorted(name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml"))
        if not parts or len(parts) > MAX_SHEETS:
            _deny("xlsx_sheet_limit")
        shared = "xl/sharedStrings.xml" if "xl/sharedStrings.xml" in names else None
        return [(None, None, name) for name in parts], shared
    book = _xml(archive, "xl/workbook.xml")
    if book.tag not in {f"{{{MAIN_NS}}}workbook", f"{{{STRICT_NS}}}workbook"}:
        _deny("xlsx_workbook_unavailable")
    relationships = _xml(archive, "xl/_rels/workbook.xml.rels")
    if relationships.tag != f"{{{PACKAGE_NS}}}Relationships":
        _deny()
    rels = {}
    shared = None
    for rel in _children(relationships, "Relationship"):
        key = rel.get("Id")
        if not key or key in rels:
            _deny("xlsx_relationship_ambiguous")
        rels[key] = rel
        if rel.get("TargetMode") == "External":
            result.warn("xlsx_external_links_not_resolved")
            continue
        if rel.get("Type") in {REL_NS + "/sharedStrings", STRICT_REL_NS + "/sharedStrings"}:
            if shared is not None:
                _deny("xlsx_relationship_ambiguous")
            shared = _target(rel.get("Target"))
    if _one(book, "externalReferences") is not None:
        result.warn("xlsx_external_links_not_resolved")
    sheets = _one(book, "sheets")
    if sheets is None:
        _deny("xlsx_workbook_unavailable")
    output = []
    sheet_ids, sheet_names, parts = set(), set(), set()
    for sheet in _children(sheets, "sheet"):
        key, name = sheet.get("sheetId"), sheet.get("name")
        if (not key or not key.isdecimal() or len(key) > 20 or int(key) < 1
                or not name or len(name) > 255 or any(ord(c) < 32 for c in name)
                or int(key) in sheet_ids or name.casefold() in sheet_names):
            _deny("xlsx_sheet_identity_unavailable")
        rel_id = sheet.get(f"{{{REL_NS}}}id") or sheet.get(f"{{{STRICT_REL_NS}}}id")
        rel = rels.get(rel_id)
        if rel is None or rel.get("TargetMode", "Internal") != "Internal":
            _deny("xlsx_sheet_relationship_unavailable")
        if rel.get("Type") not in {REL_NS + "/worksheet", STRICT_REL_NS + "/worksheet"}:
            _deny("xlsx_sheet_type_unsupported")
        part = _target(rel.get("Target"))
        if part in parts or part not in names:
            _deny("xlsx_sheet_relationship_unavailable")
        sheet_ids.add(int(key))
        sheet_names.add(name.casefold())
        parts.add(part)
        output.append((key, name, part))
        if len(output) > MAX_SHEETS:
            _deny("xlsx_sheet_limit")
    if not output:
        _deny("xlsx_sheet_limit")
    return output, shared


def _address(address):
    match = _CELL.fullmatch(address or "")
    if match is None:
        _deny("xlsx_cell_address_unavailable")
    column = 0
    for character in match[1]:
        column = column * 26 + ord(character) - ord("A") + 1
    row = int(match[2])
    if column > 16384 or row > 1048576:
        _deny("xlsx_cell_address_unavailable")
    return row, column


def _parse(data):
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE_BYTES:
        _deny("xlsx_size_limit")
    result = XlsxExtraction()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            _deny("xlsx_entry_limit")
        names, unpacked = set(), 0
        for info in entries:
            # ZipInfo normalizes native separators on Windows and truncates at
            # NUL; validate the original central-directory name as well.
            _path(info.orig_filename)
            name = _path(info.filename)
            if name in names or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                _deny()
            names.add(name)
            unpacked += info.file_size
            if (unpacked > MAX_UNPACKED_BYTES or info.file_size > MAX_XML_BYTES
                    or info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO):
                _deny("xlsx_size_limit")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                _deny("xlsx_compression_unsupported")
            if name.casefold().endswith("vbaproject.bin"):
                result.warn("xlsx_macros_not_executed")
        sheets, shared_part = _sheet_parts(archive, result)
        strings = []
        value_budget = 0
        if shared_part is not None:
            shared_root = _xml(archive, shared_part)
            if (_local(shared_root.tag) != "sst" or (sheets[0][0] is not None and shared_root.tag not in {
                f"{{{MAIN_NS}}}sst", f"{{{STRICT_NS}}}sst",
            })):
                _deny()
            for si in _children(shared_root, "si"):
                value = _text(si)
                strings.append(value)
                value_budget += len(value)
                if len(strings) > MAX_SHARED_STRINGS or value_budget > MAX_VALUE_BUDGET:
                    _deny("xlsx_value_limit")
        lines, text_size = [], 0
        text_truncated = False
        for sheet_key, sheet_name, part in sheets:
            result.sheets.append({"sheet_key": sheet_key, "sheet_name": sheet_name,
                                  "sheet_part": part, "identity_verified": sheet_key is not None})
            root = _xml(archive, part)
            if (_local(root.tag) != "worksheet" or (sheet_key is not None and root.tag not in {
                f"{{{MAIN_NS}}}worksheet", f"{{{STRICT_NS}}}worksheet",
            })):
                _deny()
            sheet_data = _one(root, "sheetData")
            if sheet_data is None:
                continue
            last_row = 0
            for row_node in _children(sheet_data, "row"):
                row_attr = row_node.get("r")
                if row_attr is not None and (not row_attr.isdecimal() or len(row_attr) > 7):
                    _deny("xlsx_cell_address_unavailable")
                row_number = int(row_attr) if row_attr is not None else last_row + 1
                if not last_row < row_number <= 1048576:
                    _deny("xlsx_cell_address_unavailable")
                last_row = row_number
                row_values, last_column = {}, 0
                for node in _children(row_node, "c"):
                    address = node.get("r")
                    cell_row, column = _address(address) if address is not None else (row_number, last_column + 1)
                    if cell_row != row_number or not last_column < column <= 16384:
                        _deny("xlsx_cell_address_unavailable")
                    last_column = column
                    exact = sheet_key is not None and address is not None and row_attr is not None
                    if not exact:
                        result.warn("xlsx_cell_identity_unverified")
                    kind = node.get("t", "n")
                    if kind not in {"n", "s", "inlineStr", "str", "b", "e", "d"}:
                        _deny("xlsx_cell_type_unsupported")
                    formula_node, cached_node = _one(node, "f"), _one(node, "v")
                    formula = formula_node.text if formula_node is not None else None
                    formula_type = formula_node.get("t", "normal") if formula_node is not None else None
                    formula_index = formula_node.get("si") if formula_node is not None else None
                    formula_range = formula_node.get("ref") if formula_node is not None else None
                    if formula_type not in {None, "normal", "shared", "array", "dataTable"}:
                        _deny("xlsx_formula_type_unsupported")
                    if formula_index is not None and (not formula_index.isdecimal() or len(formula_index) > 10):
                        _deny("xlsx_formula_metadata_unavailable")
                    if formula_range is not None:
                        bounds = formula_range.split(":")
                        if not 1 <= len(bounds) <= 2:
                            _deny("xlsx_formula_metadata_unavailable")
                        coordinates = [_address(bound) for bound in bounds]
                        if len(coordinates) == 2 and any(a > b for a, b in zip(*coordinates)):
                            _deny("xlsx_formula_metadata_unavailable")
                    raw = cached_node.text if cached_node is not None else None
                    cache_state = "missing" if cached_node is None else "empty" if not raw else "present"
                    value = raw
                    if kind == "s":
                        if raw is None or not raw.isdecimal() or len(raw) > 9 or int(raw) >= len(strings):
                            _deny("xlsx_shared_string_unavailable")
                        value = strings[int(raw)]
                    elif kind == "inlineStr":
                        inline = _one(node, "is")
                        value = _text(inline)
                        cache_state = "present" if inline is not None else "missing"
                    warnings = []
                    if formula_node is not None:
                        if cache_state != "present":
                            warnings.append("xlsx_formula_cache_" + cache_state)
                        if not formula:
                            warnings.append("xlsx_formula_expression_unavailable")
                        if formula_type != "normal":
                            warnings.append("xlsx_formula_type_not_expanded")
                    if kind == "e":
                        warnings.append("xlsx_cell_error")
                    for warning in warnings:
                        result.warn(warning)
                    for string in (formula, value):
                        if string is not None:
                            if len(string) > MAX_VALUE_CHARS:
                                _deny("xlsx_value_limit")
                            value_budget += len(string)
                    if value_budget > MAX_VALUE_BUDGET or len(result.cells) >= MAX_CELLS:
                        _deny("xlsx_value_limit")
                    locators = []
                    if exact:
                        base = {"kind": "sheet_cell", "sheet_key": sheet_key,
                                "sheet_name": sheet_name, "range_a1": address}
                        if formula:
                            locators.append({**base, "value_kind": "formula"})
                        if cache_state == "present":
                            locators.append({**base, "value_kind": "cached_value"})
                    result.cells.append({
                        "sheet_key": sheet_key, "sheet_name": sheet_name, "sheet_part": part,
                        "cell_ref": address,
                        "row": cell_row if address is not None else None,
                        "column": column if address is not None else None,
                        "identity_verified": exact, "cell_type": kind,
                        "formula": formula, "formula_present": formula_node is not None,
                        "formula_type": formula_type,
                        "formula_shared_index": formula_index,
                        "formula_range": formula_range,
                        "cached_value": value, "cached_value_raw": raw,
                        "cache_state": cache_state, "formula_recalculated": False,
                        "cache_freshness": "not_verified", "locators": locators,
                        "needs_review": bool(warnings) or not exact, "warnings": warnings,
                    })
                    row_values[column] = (value or "").replace("\t", " ").replace("\n", " ").replace("\r", " ").strip()
                if any(row_values.values()):
                    line = "\t".join(row_values.get(col, "") for col in range(1, last_column + 1))
                    if not text_truncated and text_size + len(line) + bool(lines) <= MAX_TSV_CHARS:
                        lines.append(line)
                        text_size += len(line) + (len(lines) > 1)
                    else:
                        text_truncated = True
                        result.warn("xlsx_text_limit")
        result.text = "\n".join(lines)
    return result


def extract_xlsx_cells(data: bytes) -> XlsxExtraction:
    """Return review metadata or a content-free failure; never extract ZIP files."""
    try:
        return _parse(data)
    except XlsxExtractionError:
        raise
    except (ET.ParseError, UnicodeError, KeyError, ValueError, RuntimeError, OSError,
            zipfile.BadZipFile, NotImplementedError):
        raise XlsxExtractionError("xlsx_package_unavailable") from None
