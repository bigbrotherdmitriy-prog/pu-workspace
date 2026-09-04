from __future__ import annotations


def run(kind: str, payload: dict) -> dict:
    """Resolve handlers lazily so API modules do not import worker infrastructure."""
    if kind == "v54.synthetic_provider_action":
        from app.provider_actions.runtime import run_installed
        return run_installed(payload)
    if kind == "v54.synthetic_task":
        from app.pilot_dispatch import run_installed
        return run_installed(payload)
    if kind == "local_upload.process":
        from app.local_upload_staging import run_local_upload_job
        return run_local_upload_job(payload)
    if kind == "organizer.scan":
        from app.organizer import _scan_worker
        _scan_worker(int(payload["session_id"]), int(payload["project_id"]), payload["source_folder_id"], payload.get("auto_apply"))
        return {"session_id": int(payload["session_id"])}
    if kind == "documents.ocr":
        from app.ocr_batch import reprocess_documents
        return reprocess_documents(
            int(payload["project_id"]),
            [int(value) for value in (payload.get("document_ids") or [])] or None,
            int(payload["job_id"]) if payload.get("job_id") is not None else None,
        )
    if kind == "workspace.snapshot":
        from app.api.workspace import _build_snapshot
        _build_snapshot(int(payload["snapshot_id"]), int(payload["project_id"]), payload["external_id"], raise_errors=True)
        return {"snapshot_id": int(payload["snapshot_id"])}
    if kind == "workspace.analysis":
        from app.api.workspace import _analyze_snapshot_worker
        _analyze_snapshot_worker(int(payload["snapshot_id"]), int(payload["project_id"]), raise_errors=True)
        return {"snapshot_id": int(payload["snapshot_id"])}
    if kind == "workspace.safe_copy":
        from app.api.workspace import _run_safe_copy_pipeline
        _run_safe_copy_pipeline(
            int(payload["snapshot_id"]), int(payload["session_id"]),
            int(payload["project_id"]), payload["source_folder_id"], raise_errors=True,
        )
        return {"snapshot_id": int(payload["snapshot_id"]), "session_id": int(payload["session_id"])}
    if kind == "gmail.sync":
        from app.automations.gmail import sync_authorized_projects_once
        return sync_authorized_projects_once()
    if kind == "gmail.attachment.materialize":
        from app.staging.gmail import run_gmail_attachment_job
        return run_gmail_attachment_job(payload)
    if kind == "ai.rules":
        from app.automation_engine import run_due_rules
        from app.database import SessionLocal
        with SessionLocal() as db:
            return run_due_rules(db)
    raise ValueError(f"Unknown background job kind: {kind}")


def notify_outcome(kind: str, payload: dict, status: str) -> None:
    """Route lifecycle hooks without coupling the generic queue to staging."""
    if kind == "gmail.attachment.materialize":
        from app.staging.gmail import notify_gmail_attachment_job_outcome
        notify_gmail_attachment_job_outcome(payload, status)
