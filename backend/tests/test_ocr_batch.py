from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.jobs.handlers import run
from app.ocr_batch import _supported


ROOT = Path(__file__).resolve().parents[2]


def test_only_pdf_and_image_documents_enter_ocr_batch():
    assert _supported(SimpleNamespace(name="scan.pdf", mime_type="application/pdf"))
    assert _supported(SimpleNamespace(name="photo.jpg", mime_type="image/jpeg"))
    assert not _supported(SimpleNamespace(name="schedule.xlsx", mime_type="application/vnd.ms-excel"))


def test_durable_worker_dispatches_ocr_batch():
    with patch("app.ocr_batch.reprocess_documents", return_value={"total": 2}) as process:
        result = run("documents.ocr", {"project_id": 7, "document_ids": [10, 11]})
    assert result == {"total": 2}
    process.assert_called_once_with(7, [10, 11])


def test_documents_ui_exposes_bulk_and_single_document_ocr():
    module = (ROOT / "frontend/src/modules/documents/DocumentsModule.tsx").read_text(encoding="utf-8")
    api = (ROOT / "backend/app/api/documents.py").read_text(encoding="utf-8")
    assert "Повторно распознать сканы" in module
    assert "Повторно распознать этот документ" in module
    assert 'documents/ocr-batches' in module
    assert '@router.post("/{project_id}/documents/ocr-batches")' in api
    assert "extraction_quality" in api
