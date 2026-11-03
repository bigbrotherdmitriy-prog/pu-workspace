"""Synthetic document regression; no API requests, real forms or DB writes."""
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile
from xml.etree import ElementTree

import pytest

from app.customer_portal import header_of
from app.ks_forms import render_ks2_docx, render_ks3_docx


@pytest.mark.parametrize("raw", ["[]", "null", "true", '\"legacy text\"', "invalid-json"])
def test_legacy_non_mapping_header_does_not_crash_customer_forms(raw):
    act = SimpleNamespace(header_json=raw)
    header = header_of(act)
    data = {
        "act": {"number": "SYNTHETIC", "act_date": "2026-01-01", "period_from": "2026-01-01", "period_to": "2026-01-01"},
        "estimate": {"total": "0"}, "lines": [], "sections": {},
        "from_start": "0", "from_year_start": "0", "period_total": "0",
        "vat_rate": "0", "vat": "0", "total_with_vat": "0",
    }
    for render in (render_ks2_docx, render_ks3_docx):
        with ZipFile(BytesIO(render(header, data))) as document:
            xml = ElementTree.fromstring(document.read("word/document.xml"))
            assert "SYNTHETIC" in "".join(xml.itertext())
    assert act.header_json == raw


def test_customer_header_preserves_valid_fields():
    act = SimpleNamespace(header_json='{"customer":"Synthetic customer","contract_no":"SYNTHETIC"}')
    assert header_of(act) == {"customer": "Synthetic customer", "contract_no": "SYNTHETIC"}
