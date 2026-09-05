"""Synthetic in-memory XLSX fixtures; no recalculation or external resources."""

import io
import zipfile
from xml.sax.saxutils import quoteattr

import pytest

from app.organizer_engine import content
from app.source_evidence.fragment_reader import SheetCellLocator


MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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


def extract(data):
    return content.extract_text_result(data, MIME, "synthetic.xlsx")


def unpack_fixture(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_formula_cache_and_exact_locator_are_separate_from_legacy_tsv():
    data = workbook([("Бюджет & план", 7, "actual.xml",
                      '<row r="2"><c r="A2" t="inlineStr"><is><t>Материалы</t></is></c>'
                      '<c r="C2"><f>SUM(A1:B1)</f><v>125000.50</v></c></row>')])
    result = extract(data)
    assert result.text == "Материалы\t\t125000.50"
    cell = result.metadata()["spreadsheet_cells"][1]
    assert cell["sheet_name"] == "Бюджет & план" and cell["sheet_key"] == "7"
    assert cell["cell_ref"] == "C2"
    assert cell["formula"] == "SUM(A1:B1)" and cell["cached_value"] == "125000.50"
    assert cell["cache_state"] == "present"
    assert cell["formula_recalculated"] is False
    for locator in cell["locators"]:
        assert SheetCellLocator.model_validate(locator).range_a1 == "C2"
    assert {locator["value_kind"] for locator in cell["locators"]} == {"formula", "cached_value"}


@pytest.mark.parametrize("cache,state", [("", "missing"), ("<v/>", "empty")])
def test_formula_without_cached_value_requires_review_without_inventing_result(cache, state, monkeypatch):
    monkeypatch.setattr(content, "_ocr_text", lambda *_a: pytest.fail("XLSX must not invoke OCR"))
    result = extract(workbook([("Plan", 1, "s.xml", f'<row r="1"><c r="A1"><f>1+1</f>{cache}</c></row>')]))
    assert result.needs_review is True
    assert result.text == ""
    cell = result.metadata()["spreadsheet_cells"][0]
    assert cell["cached_value"] is None and cell["cache_state"] == state
    assert cell["formula"] == "1+1"
    assert {locator["value_kind"] for locator in cell["locators"]} == {"formula"}


def test_workbook_order_relationships_shared_and_inline_strings_are_preserved():
    result = extract(workbook([
        ("First", 9, "z.xml", '<row r="1"><c r="B1" t="s"><v>0</v></c></row>'),
        ("Second", 3, "a.xml", '<row r="4"><c r="A4" t="inlineStr"><is><r><t>A</t></r><r><t>B</t></r></is></c></row>'),
    ], shared='<si><r><t>First </t></r><r><t>value</t></r></si>'))
    cells = result.metadata()["spreadsheet_cells"]
    assert [(c["sheet_key"], c["sheet_name"], c["cell_ref"]) for c in cells] == [
        ("9", "First", "B1"), ("3", "Second", "A4"),
    ]
    assert result.text == "\tFirst value\nAB"
    assert cells[0]["cached_value"] == "First value"
    assert cells[1]["cached_value"] == "AB"


def test_dense_legacy_tsv_still_feeds_structured_import():
    from app.structured_import import parse_structured_rows
    result = extract(workbook([("Budget", 1, "s.xml",
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Описание</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Сумма</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>Материалы</t></is></c>'
        '<c r="B2"><f>100+25</f><v>125.00</v></c></row>')]))
    assert result.text == "Описание\tСумма\nМатериалы\t125.00"
    parsed = parse_structured_rows(result.text, "budget")
    assert parsed["rows"][0]["amount"] == "125.00"
    assert parsed["rows"][0]["importable"] is True


def test_shared_formula_follower_is_not_guessed_or_expanded():
    result = extract(workbook([("Plan", 1, "s.xml",
        '<row r="1"><c r="A1"><f t="shared" si="3" ref="A1:A2">B1+1</f><v>2</v></c></row>'
        '<row r="2"><c r="A2"><f t="shared" si="3"/><v>3</v></c></row>')]))
    first, second = result.metadata()["spreadsheet_cells"]
    assert first["formula"] == "B1+1" and first["formula_range"] == "A1:A2"
    assert second["formula"] is None and second["formula_present"] is True
    assert second["formula_shared_index"] == "3" and second["cached_value"] == "3"
    assert second["needs_review"] is True and result.needs_review is True
    assert second["locators"][0]["value_kind"] == "cached_value"


def test_zero_error_and_empty_inline_values_are_distinct():
    result = extract(workbook([("Values", 1, "s.xml",
        '<row r="1"><c r="A1"><f>1-1</f><v>0</v></c>'
        '<c r="B1" t="e"><f>1/0</f><v>#DIV/0!</v></c>'
        '<c r="C1" t="inlineStr"><is><t/></is></c></row>')]))
    zero, error, empty = result.metadata()["spreadsheet_cells"]
    assert zero["cached_value"] == "0" and zero["cache_state"] == "present"
    assert error["cached_value"] == "#DIV/0!" and error["needs_review"]
    assert empty["cached_value"] == "" and empty["cache_state"] == "present"


def test_empty_sheet_identity_and_absolute_opc_target_are_preserved():
    data = workbook([("Empty", 1, "e.xml", ""), ("Data", 2, "d.xml", '<row r="1"><c r="A1"><v>9</v></c></row>')])
    parts = unpack_fixture(data)
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(b'Target="worksheets/', b'Target="/xl/worksheets/')
    result = extract(package(parts))
    assert [sheet["sheet_name"] for sheet in result.metadata()["spreadsheet_sheets"]] == ["Empty", "Data"]
    assert result.text == "9"


def test_worksheet_only_legacy_input_is_readable_but_never_fabricates_exact_locators():
    result = extract(package({"xl/worksheets/sheet1.xml":
        '<worksheet xmlns="urn:legacy"><sheetData><row><c><v>10</v></c></row></sheetData></worksheet>'}))
    assert result.text == "10" and result.needs_review
    cell = result.metadata()["spreadsheet_cells"][0]
    assert cell["sheet_name"] is None and cell["cell_ref"] is None
    assert cell["row"] is None and cell["column"] is None
    assert cell["locators"] == [] and cell["identity_verified"] is False


@pytest.mark.parametrize("address", ["A0", "A1048577", "XFE1", "../A1", "a1", "A1:B2"])
def test_invalid_addresses_fail_closed(address):
    from app.ocr_quality.xlsx_cells import XlsxExtractionError
    with pytest.raises(XlsxExtractionError, match="xlsx_cell_address_unavailable"):
        extract(workbook([("Plan", 1, "s.xml", f'<row r="1"><c r="{address}"><v>1</v></c></row>')]))


@pytest.mark.parametrize("rows", [
    '<row r="1"><c r="A1"><v>1</v></c><c r="A1"><v>2</v></c></row>',
    '<row r="1"><c r="A2"><v>1</v></c></row>',
    '<row r="2"/><row r="1"/>',
])
def test_conflicting_cell_and_row_identity_is_rejected(rows):
    from app.ocr_quality.xlsx_cells import XlsxExtractionError
    with pytest.raises(XlsxExtractionError):
        extract(workbook([("Plan", 1, "s.xml", rows)]))


@pytest.mark.parametrize("target", ["../evil.xml", "https://invalid.test/x", "file:///tmp/x", "worksheets/%2e%2e/x.xml", "worksheets\\s.xml"])
def test_relationship_traversal_or_external_target_is_never_resolved(target):
    from app.ocr_quality.xlsx_cells import XlsxExtractionError
    parts = unpack_fixture(workbook([("Plan", 1, "s.xml", "")]))
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(b"worksheets/s.xml", target.encode())
    with pytest.raises(XlsxExtractionError):
        extract(package(parts))


def test_external_relationships_and_macros_are_only_flagged_never_opened(monkeypatch):
    parts = unpack_fixture(workbook([("Plan", 1, "s.xml", '<row r="1"><c r="A1"><f>[1]Sheet1!A1</f><v>12</v></c></row>')]))
    rels = parts["xl/_rels/workbook.xml.rels"].decode().replace('</Relationships>',
        f'<Relationship Id="ext" Type="{REL}/externalLink" TargetMode="External" Target="https://invalid.test/private"/></Relationships>')
    parts["xl/_rels/workbook.xml.rels"] = rels
    parts["xl/vbaProject.bin"] = b"never execute"
    data = package(parts)
    original_read = zipfile.ZipFile.open
    def checked_open(self, name, *args, **kwargs):
        actual = name.filename if isinstance(name, zipfile.ZipInfo) else name
        assert actual != "xl/vbaProject.bin"
        assert not actual.startswith("https:")
        return original_read(self, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", checked_open)
    result = extract(data)
    assert result.text == "12" and result.needs_review
    assert "xlsx_external_links_not_resolved" in result.warnings
    assert "xlsx_macros_not_executed" in result.warnings


@pytest.mark.parametrize("name", ["../private.xml", "/absolute.xml", "xl/../bad.xml", "xl\\bad.xml"])
def test_zip_paths_are_validated_even_for_unread_parts(name):
    from app.ocr_quality.xlsx_cells import XlsxExtractionError
    # Windows ZipInfo normalizes backslashes on construction. Modify only the
    # equal-length local/central filename bytes to represent the hostile input.
    data = workbook([("Plan", 1, "s.xml", "")], extra={name.replace("\\", "/"): "unread"})
    if "\\" in name:
        data = data.replace(name.replace("\\", "/").encode(), name.encode())
    with pytest.raises(XlsxExtractionError, match="xlsx_path_unavailable"):
        extract(data)


@pytest.mark.parametrize("xml", [
    '<!DOCTYPE worksheet [<!ENTITY secret "private">]><worksheet xmlns="x">&secret;</worksheet>',
    '<!DOCTYPE worksheet SYSTEM "https://invalid.test/private"><worksheet xmlns="x"/>',
])
def test_dtd_and_entities_fail_without_resolving_any_resource(xml):
    from app.ocr_quality.xlsx_cells import XlsxExtractionError
    data = workbook([("Plan", 1, "s.xml", "")], extra={"xl/worksheets/s.xml": xml})
    with pytest.raises(XlsxExtractionError, match="xlsx_xml_declaration_denied") as failure:
        extract(data)
    assert "private" not in str(failure.value)


@pytest.mark.parametrize("limit,value", [("MAX_ARCHIVE_BYTES", 10), ("MAX_ENTRIES", 1), ("MAX_XML_BYTES", 10),
                                        ("MAX_UNPACKED_BYTES", 10), ("MAX_CELLS", 1), ("MAX_VALUE_BUDGET", 1),
                                        ("MAX_XML_NODES", 1), ("MAX_XML_DEPTH", 1)])
def test_parser_budgets_fail_instead_of_returning_partial_metadata(monkeypatch, limit, value):
    from app.ocr_quality import xlsx_cells
    data = workbook([("Plan", 1, "s.xml", '<row r="1"><c r="A1"><v>12</v></c><c r="B1"><v>34</v></c></row>')])
    monkeypatch.setattr(xlsx_cells, limit, value)
    with pytest.raises(xlsx_cells.XlsxExtractionError):
        extract(data)


def test_tsv_truncation_is_reported_and_never_appends_later_rows(monkeypatch):
    from app.ocr_quality import xlsx_cells
    monkeypatch.setattr(xlsx_cells, "MAX_TSV_CHARS", 3)
    result = extract(workbook([("Plan", 1, "s.xml",
        '<row r="1"><c r="A1"><v>1</v></c></row>'
        '<row r="2"><c r="A2"><v>2345</v></c></row>'
        '<row r="3"><c r="A3"><v>6</v></c></row>')]))
    assert result.text == "1" and result.needs_review
    assert "xlsx_text_limit" in result.warnings
    assert len(result.metadata()["spreadsheet_cells"]) == 3


@pytest.mark.parametrize("limit,value", [("MAX_COMPRESSION_RATIO", 1), ("MAX_SHEETS", 1),
                                        ("MAX_SHARED_STRINGS", 1), ("MAX_VALUE_CHARS", 1)])
def test_remaining_resource_limits_are_enforced(monkeypatch, limit, value):
    from app.ocr_quality import xlsx_cells
    data = workbook([("One", 1, "a.xml", '<row r="1"><c r="A1"><v>1234</v></c></row>'),
                     ("Two", 2, "b.xml", "")], shared='<si><t>ab</t></si><si><t>cd</t></si>')
    monkeypatch.setattr(xlsx_cells, limit, value)
    with pytest.raises(xlsx_cells.XlsxExtractionError):
        extract(data)


@pytest.mark.parametrize("mutation", ["duplicate_sheet_id", "duplicate_sheet_name", "duplicate_relationship",
                                      "external_sheet", "missing_part", "duplicate_zip_entry"])
def test_ambiguous_or_unavailable_package_identity_fails_closed(mutation):
    from app.ocr_quality.xlsx_cells import XlsxExtractionError
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
    data = package(parts)
    if mutation == "duplicate_zip_entry":
        buffer = io.BytesIO(data)
        with zipfile.ZipFile(buffer, "a") as archive, pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("xl/workbook.xml", parts["xl/workbook.xml"])
        data = buffer.getvalue()
    with pytest.raises(XlsxExtractionError):
        extract(data)


@pytest.mark.parametrize("index", ["-1", "1", "not-a-number"])
def test_invalid_shared_string_index_never_becomes_a_value(index):
    from app.ocr_quality.xlsx_cells import XlsxExtractionError
    with pytest.raises(XlsxExtractionError, match="xlsx_shared_string_unavailable"):
        extract(workbook([("Plan", 1, "a.xml", f'<row r="1"><c r="A1" t="s"><v>{index}</v></c></row>')],
                         shared='<si><t>only value</t></si>'))


def test_shared_strings_use_relationship_target_not_conventional_filename():
    parts = unpack_fixture(workbook([("Plan", 1, "a.xml", '<row r="1"><c r="A1" t="s"><v>0</v></c></row>')],
                                   shared='<si><t>actual value</t></si>'))
    parts["xl/strings/actual.xml"] = parts.pop("xl/sharedStrings.xml")
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(b'Target="sharedStrings.xml"', b'Target="strings/actual.xml"')
    assert extract(package(parts)).text == "actual value"
