from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_canonical_job_status_contract_and_operator_api():
    queue = (ROOT / "backend/app/jobs/queue.py").read_text(encoding="utf-8")
    api = (ROOT / "backend/app/api/jobs.py").read_text(encoding="utf-8")
    for status in ("queued", "running", "retrying", "failed", "dead_letter", "completed", "cancelled"):
        assert status in queue
    for action in ('/{job_id}/cancel', '/{job_id}/retry', '/{job_id}/redrive', '/metrics'):
        assert action in api

def test_http_scan_is_durable_and_idempotent():
    source = (ROOT / "backend/app/organizer.py").read_text(encoding="utf-8")
    assert 'request.headers.get("Idempotency-Key"' in source
    assert "background_tasks.add_task" not in source
    assert "_scan_worker(session_id, payload.project_id, source.id)" not in source
    workspace = (ROOT / "backend/app/api/workspace.py").read_text(encoding="utf-8")
    assert "_analysis_in_progress" not in workspace
    assert "return queue_workspace_snapshot(project_id, external_id, db, user)" in workspace

def test_logs_do_not_include_job_exception_or_payload():
    source = (ROOT / "backend/app/jobs/worker.py").read_text(encoding="utf-8")
    assert "log.exception" not in source
    assert "dict(job.payload" in source  # passed to handler, never logged
    assert 'exc.__class__.__name__' in source
