from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.jobs.handlers import run
from app.ocr_batch import _supported
from app.models.document import Document
from app.models.job import BackgroundJob
from app.organizer_engine.content import ExtractionResult, PageExtraction


ROOT = Path(__file__).resolve().parents[2]


def test_only_pdf_and_image_documents_enter_ocr_batch():
    assert _supported(SimpleNamespace(name="scan.pdf", mime_type="application/pdf"))
    assert _supported(SimpleNamespace(name="photo.jpg", mime_type="image/jpeg"))
    assert not _supported(SimpleNamespace(name="schedule.xlsx", mime_type="application/vnd.ms-excel"))


def test_durable_worker_dispatches_ocr_batch():
    with patch("app.ocr_batch.reprocess_documents", return_value={"total": 2}) as process:
        result = run("documents.ocr", {"project_id": 7, "document_ids": [10, 11]})
    assert result == {"total": 2}
    process.assert_called_once_with(7, [10, 11], None)


def test_documents_ui_exposes_bulk_and_single_document_ocr():
    module = (ROOT / "frontend/src/modules/documents/DocumentsModule.tsx").read_text(encoding="utf-8")
    api = (ROOT / "backend/app/api/documents.py").read_text(encoding="utf-8")
    assert "Повторно распознать сканы" in module
    assert "Повторно распознать этот документ" in module
    assert 'documents/ocr-batches' in module
    assert '@router.post("/{project_id}/documents/ocr-batches")' in api
    assert "extraction_quality" in api


def test_low_confidence_batch_is_persisted_for_review_without_actions(db_session):
    document = Document(
        project_id=1, name="scan.pdf", mime_type="application/pdf",
        external_id="safe-id", source="google_drive", status="discovered",
    )
    db_session.add(document)
    db_session.commit()

    class SessionContext:
        def __enter__(self):
            return db_session

        def __exit__(self, *_):
            return False

    low = ExtractionResult(
        text="Акт № 42 от 01.02.2026", method="ocr", quality="low",
        total_pages=1, ocr_pages=1, confidence=.4,
        pages=[PageExtraction(1, "Акт № 42 от 01.02.2026", .4, "ocr")],
        needs_review=True, warnings=["manual_review_required"],
    )
    drive = SimpleNamespace(read_bytes=lambda *_: (b"safe", "application/pdf"))
    with (
        patch("app.ocr_batch.SessionLocal", return_value=SessionContext()),
        patch("app.ocr_batch.DriveClient", return_value=drive),
        patch("app.ocr_batch.get_drive_service", return_value=object()),
        patch("app.ocr_batch.extract_text_result", return_value=low),
        patch("app.ocr_batch.index_documents", return_value=[document]),
        patch("app.ocr_batch.create_tasks_from_files", return_value=[]) as tasks,
        patch("app.ocr_batch.create_response_drafts", return_value=[]) as drafts,
        patch("app.ocr_batch.create_governance_items", return_value=([], [])) as governance,
    ):
        from app.ocr_batch import reprocess_documents
        result = reprocess_documents(1, [document.id])

    assert result["processed"][0]["needs_review"] is True
    assert document.ocr_review_status == "needs_review"
    assert document.ocr_metadata["pages"][0]["page"] == 1
    assert tasks.call_args.args[3] == []
    assert drafts.call_args.args[3] == []
    assert governance.call_args.args[2] == []


def test_ocr_job_reports_progress_and_observes_cooperative_cancel(db_session):
    from app.ocr_batch import _job_control

    job = BackgroundJob(kind="documents.ocr", payload={"project_id": 1}, status="running", result={})
    db_session.add(job)
    db_session.commit()
    assert _job_control(db_session, job.id, completed=2, total=5, document_id=11) is False
    assert db_session.get(BackgroundJob, job.id).result["progress"]["percent"] == 40
    job.result = {"cancel_requested": True}
    db_session.commit()
    assert _job_control(db_session, job.id, completed=3, total=5) is True


def test_cancel_ocr_batch_uses_manager_permission_and_stops_queued_job(db_session):
    from app.api.documents import cancel_ocr_batch

    job = BackgroundJob(kind="documents.ocr", payload={"project_id": 9}, status="queued")
    db_session.add(job)
    db_session.commit()
    user = SimpleNamespace(id=17)
    with patch("app.api.documents.require_project_role") as permission:
        response = cancel_ocr_batch(9, job.id, db=db_session, user=user)
    permission.assert_called_once_with(db_session, user, 9, "manager")
    assert response["status"] == "cancelled"
    assert db_session.get(BackgroundJob, job.id).result["cancelled"] is True
