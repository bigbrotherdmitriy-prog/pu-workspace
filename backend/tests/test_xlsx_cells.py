"""Unit tests for app.ocr_quality.xlsx_cells, the bounded structural XLSX
cell/sheet parser ported from the review branch (see docs/audits/
mvp1-main-integration-ocr-preflight.md section 2.2).

This module is exercised in isolation here; its integration into
app.organizer_engine.content.extract_text_result (additive spreadsheet_cells/
spreadsheet_sheets fields, `.text` left untouched) is covered by
backend/tests/test_content.py.

Synthetic in-memory XLSX fixtures only; no recalculation or external I/O.
"""

import io
import zipfile
from xml.sax.saxutils import quoteattr

import pytest

from app.ocr_quality.xlsx_cells import XlsxExtractionError, extract_xlsx_cells
from app.source_evidence.fragment_reader import SheetCellLocator


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def package(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def workbook(sheets, *, shared=None, extra=None):
    """sheets: (visible name, sheetId, actual part basename, sheetData XML)."""
    entries = []
    relationships = []
    parts = {}
    for index, (name, sheet_id, part, rows) in enumerate(sheets):
        entries.append(f'<sheet name={quoteattr(name)} sheetId="{sheet_id}" r:id="r{index}"/>')
        relationships.append(f'<Relationship Id="r{index}" Type="{REL}/worksheet" Target="worksheets/{part}"/>')
        parts[f"xl/worksheets/{part}"] = f'<worksheet xmlns="{NS}"><sheetData>{rows}</sheetData></worksheet>'
    if shared is not None:
        relationships.append(f'<Relationship Id="strings" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/>')
        parts["xl/sharedStrings.xml"] = f'<sst xmlns="{NS}">{shared}</sst>'
    parts["xl/workbook.xml"] = f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>{"".join(entries)}</sheets></workbook>'
    parts["xl/_rels/workbook.xml.rels"] = f'<Relationships xmlns="{PKG}">{"".join(relationships)}</Relationships>'
    parts.update(extra or {})
    return package(parts)


def unpack_fixture(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_formula_cache_and_exact_locator_are_reported():
    data = workbook([("Бюджет & план", 7, "actual.xml",
                      '<row r="2"><c r="A2" t="inlineStr"><is><t>Материалы</t></is></c>'
                      '<c r="C2"><f>SUM(A1:B1)</f><v>125000.50</v></c></row>')])
    result = extract_xlsx_cells(data)
    cell = result.cells[1]
    assert cell["sheet_name"] == "Бюджет & план" and cell["sheet_key"] == "7"
    assert cell["cell_ref"] == "C2"
    assert cell["formula"] == "SUM(A1:B1)" and cell["cached_value"] == "125000.50"
    assert cell["cache_state"] == "present"
    assert cell["formula_recalculated"] is False
    for locator in cell["locators"]:
        assert SheetCellLocator.model_validate(locator).range_a1 == "C2"
    assert {locator["value_kind"] for locator in cell["locators"]} == {"formula", "cached_value"}


@pytest.mark.parametrize("cache,state", [("", "missing"), ("<v/>", "empty")])
def test_formula_without_cached_value_requires_review_without_inventing_result(cache, state):
    result = extract_xlsx_cells(workbook([("Plan", 1, "s.xml", f'<row r="1"><c r="A1"><f>1+1</f>{cache}</c></row>')]))
    assert result.needs_review is True
    cell = result.cells[0]
    assert cell["cached_value"] is None and cell["cache_state"] == state
    assert cell["formula"] == "1+1"
    assert {locator["value_kind"] for locator in cell["locators"]} == {"formula"}


def test_workbook_order_relationships_shared_and_inline_strings_are_preserved():
    result = extract_xlsx_cells(workbook([
        ("First", 9, "z.xml", '<row r="1"><c r="B1" t="s"><v>0</v></c></row>'),
        ("Second", 3, "a.xml", '<row r="4"><c r="A4" t="inlineStr"><is><r><t>A</t></r><r><t>B</t></r></is></c></row>'),
    ], shared='<si><r><t>First </t></r><r><t>value</t></r></si>'))
    assert [(c["sheet_key"], c["sheet_name"], c["cell_ref"]) for c in result.cells] == [
        ("9", "First", "B1"), ("3", "Second", "A4"),
    ]
    assert result.cells[0]["cached_value"] == "First value"
    assert result.cells[1]["cached_value"] == "AB"


def test_no_date_conversion_cached_value_stays_raw_serial():
    """Decided scope for this iteration: xlsx_cells does not convert date serials."""
    result = extract_xlsx_cells(workbook([("Plan", 1, "s.xml", '<row r="1"><c r="A1"><v>46388</v></c></row>')]))
    assert result.cells[0]["cached_value"] == "46388"


def test_shared_formula_follower_is_not_guessed_or_expanded():
    result = extract_xlsx_cells(workbook([("Plan", 1, "s.xml",
        '<row r="1"><c r="A1"><f t="shared" si="3" ref="A1:A2">B1+1</f><v>2</v></c></row>'
        '<row r="2"><c r="A2"><f t="shared" si="3"/><v>3</v></c></row>')]))
    first, second = result.cells
    assert first["formula"] == "B1+1" and first["formula_range"] == "A1:A2"
    assert second["formula"] is None and second["formula_present"] is True
    assert second["formula_shared_index"] == "3" and second["cached_value"] == "3"
    assert second["needs_review"] is True and result.needs_review is True
    assert second["locators"][0]["value_kind"] == "cached_value"


def test_worksheet_only_legacy_input_is_readable_but_never_fabricates_exact_locators():
    result = extract_xlsx_cells(package({"xl/worksheets/sheet1.xml":
        '<worksheet xmlns="urn:legacy"><sheetData><row><c><v>10</v></c></row></sheetData></worksheet>'}))
    cell = result.cells[0]
    assert cell["sheet_name"] is None and cell["cell_ref"] is None
    assert cell["row"] is None and cell["column"] is None
    assert cell["locators"] == [] and cell["identity_verified"] is False


@pytest.mark.parametrize("address", ["A0", "A1048577", "XFE1", "../A1", "a1", "A1:B2"])
def test_invalid_addresses_fail_closed(address):
    with pytest.raises(XlsxExtractionError, match="xlsx_cell_address_unavailable"):
        extract_xlsx_cells(workbook([("Plan", 1, "s.xml", f'<row r="1"><c r="{address}"><v>1</v></c></row>')]))


@pytest.mark.parametrize("target", ["../evil.xml", "https://invalid.test/x", "file:///tmp/x", "worksheets/%2e%2e/x.xml", "worksheets\\s.xml"])
def test_relationship_traversal_or_external_target_is_never_resolved(target):
    parts = unpack_fixture(workbook([("Plan", 1, "s.xml", "")]))
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(b"worksheets/s.xml", target.encode())
    with pytest.raises(XlsxExtractionError):
        extract_xlsx_cells(package(parts))


def test_external_relationships_and_macros_are_only_flagged_never_opened():
    parts = unpack_fixture(workbook([("Plan", 1, "s.xml", '<row r="1"><c r="A1"><f>[1]Sheet1!A1</f><v>12</v></c></row>')]))
    rels = parts["xl/_rels/workbook.xml.rels"].decode().replace('</Relationships>',
        f'<Relationship Id="ext" Type="{REL}/externalLink" TargetMode="External" Target="https://invalid.test/private"/></Relationships>')
    parts["xl/_rels/workbook.xml.rels"] = rels
    parts["xl/vbaProject.bin"] = b"never execute"
    result = extract_xlsx_cells(package(parts))
    assert "xlsx_external_links_not_resolved" in result.warnings
    assert "xlsx_macros_not_executed" in result.warnings


@pytest.mark.parametrize("xml", [
    '<!DOCTYPE worksheet [<!ENTITY secret "private">]><worksheet xmlns="x">&secret;</worksheet>',
    '<!DOCTYPE worksheet SYSTEM "https://invalid.test/private"><worksheet xmlns="x"/>',
])
def test_dtd_and_entities_fail_without_resolving_any_resource(xml):
    data = workbook([("Plan", 1, "s.xml", "")], extra={"xl/worksheets/s.xml": xml})
    with pytest.raises(XlsxExtractionError, match="xlsx_xml_declaration_denied") as failure:
        extract_xlsx_cells(data)
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("limit,value", [("MAX_ARCHIVE_BYTES", 10), ("MAX_ENTRIES", 1), ("MAX_XML_BYTES", 10),
                                        ("MAX_UNPACKED_BYTES", 10), ("MAX_CELLS", 1), ("MAX_VALUE_BUDGET", 1),
                                        ("MAX_XML_NODES", 1), ("MAX_XML_DEPTH", 1)])
def test_parser_budgets_fail_instead_of_returning_partial_metadata(monkeypatch, limit, value):
    from app.ocr_quality import xlsx_cells
    data = workbook([("Plan", 1, "s.xml", '<row r="1"><c r="A1"><v>12</v></c><c r="B1"><v>34</v></c></row>')])
    monkeypatch.setattr(xlsx_cells, limit, value)
    with pytest.raises(xlsx_cells.XlsxExtractionError):
        extract_xlsx_cells(data)


@pytest.mark.parametrize("mutation", ["duplicate_sheet_id", "duplicate_sheet_name", "duplicate_relationship",
                                      "external_sheet", "missing_part"])
def test_ambiguous_or_unavailable_package_identity_fails_closed(mutation):
    parts = unpack_fixture(workbook([("One", 1, "a.xml", ""), ("Two", 2, "b.xml", "")]))
    if mutation == "duplicate_sheet_id":
        parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b'sheetId="2"', b'sheetId="01"')
    elif mutation == "duplicate_sheet_name":
        parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(b'name="Two"', b'name="one"')
    elif mutation == "duplicate_relationship":
        parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(b'Id="r1"', b'Id="r0"')
    elif mutation == "external_sheet":
        parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(b'Id="r0"', b'Id="r0" TargetMode="External"')
    elif mutation == "missing_part":
        del parts["xl/worksheets/a.xml"]
    with pytest.raises(XlsxExtractionError):
        extract_xlsx_cells(package(parts))


@pytest.mark.parametrize("index", ["-1", "1", "not-a-number"])
def test_invalid_shared_string_index_never_becomes_a_value(index):
    with pytest.raises(XlsxExtractionError, match="xlsx_shared_string_unavailable"):
        extract_xlsx_cells(workbook([("Plan", 1, "a.xml", f'<row r="1"><c r="A1" t="s"><v>{index}</v></c></row>')],
                                    shared='<si><t>only value</t></si>'))
