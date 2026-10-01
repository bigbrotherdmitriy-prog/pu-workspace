"""Exercise the installed PDF reader with small, ordinary PDFs (not mocks).

Only the external OCR process is substituted: it is independent of the pypdf
upgrade and unavailable on some developer machines. These fixtures contain no
scripts, external resources, compressed streams or adversarial parser inputs.
"""

from app.organizer_engine import content


def _ordinary_pdf(*page_texts: str) -> bytes:
    """A self-contained Helvetica PDF, generated independently of pypdf."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            f"<< /Type /Pages /Count {len(page_texts)} /Kids ["
            + " ".join(f"{3 + 2 * index} 0 R" for index in range(len(page_texts)))
            + "] >>"
        ).encode("ascii"),
    ]
    for index, text in enumerate(page_texts):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 50 750 Td ({escaped}) Tj ET".encode("ascii")
        objects.extend([
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
                "/BaseFont /Helvetica >> >> >> "
                f"/Contents {4 + 2 * index} 0 R >>"
            ).encode("ascii"),
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream",
        ])
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode("ascii") + value + b"\nendobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(result)


def test_installed_pypdf_extracts_native_text_and_page_metadata(monkeypatch):
    first = "Invoice 42: equipment supply under contract 17; total 125000.55 RUB."
    second = "Payment conditions: delivery and acceptance are confirmed by the project owner."
    data = _ordinary_pdf(first, second)
    requests = []

    def no_ocr(_data, pages):
        requests.append(pages)
        return {}

    monkeypatch.setattr(content, "_ocr_pdf_pages", no_ocr)
    result = content.extract_text_result(data, "application/pdf", "invoice.pdf")

    assert result.text == f"{first} {second}"
    assert result.method == "native"
    assert result.total_pages == 2
    assert result.ocr_pages == 0
    assert [page.method for page in result.pages] == ["native", "native"]
    assert requests == [set()]
    assert not any(warning.startswith("native_pdf_failed:") for warning in result.warnings)


def test_installed_pypdf_blank_page_reaches_hybrid_ocr_fallback(monkeypatch):
    native = "Contract 17: project equipment delivery, installation and acceptance conditions."
    recognized = "Scanned acceptance certificate 42: amount 125000.55 RUB."
    data = _ordinary_pdf(native, "")
    requests = []

    def recognize_blank_page(received_data, pages):
        assert received_data == data
        requests.append(pages)
        return {2: recognized}

    monkeypatch.setattr(content, "_ocr_pdf_pages", recognize_blank_page)
    result = content.extract_text_result(data, "application/pdf", "mixed.pdf")

    assert result.text == f"{native} {recognized}"
    assert result.method == "hybrid"
    assert result.total_pages == 2
    assert result.ocr_pages == 1
    assert [page.method for page in result.pages] == ["native", "ocr"]
    assert requests == [{2}]
    assert not any(warning.startswith("native_pdf_failed:") for warning in result.warnings)


def test_installed_pypdf_unreadable_input_keeps_existing_ocr_error_path(monkeypatch):
    """An accidentally mislabelled text file must not escape the extraction API."""
    requests = []

    def unavailable_ocr(_data, pages):
        requests.append(pages)
        raise OSError("OCR unavailable in this synthetic test")

    monkeypatch.setattr(content, "_ocr_pdf_pages", unavailable_ocr)
    result = content.extract_text_result(b"This is ordinary text, not a PDF.", "application/pdf", "wrong.pdf")

    assert result.text == ""
    assert result.total_pages == 0
    assert result.ocr_pages == 0
    assert any(warning.startswith("native_pdf_failed:") for warning in result.warnings)
    assert "ocr_failed" in result.warnings
    assert requests == [set(range(1, content.OCR_MAX_PAGES + 1))]
