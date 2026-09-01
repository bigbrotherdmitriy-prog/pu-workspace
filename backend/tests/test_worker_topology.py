from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_api_lifespan_does_not_start_background_threads():
    source = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    assert "start_gmail_automation()" not in source
    assert "start_ai_secretary_automation()" not in source
    assert "recover_incomplete_scans()" not in source


def test_compose_has_api_workers_durable_workers_and_scheduler():
    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "--workers $${API_WORKERS:-2}" in source
    assert "app.jobs.worker" in source
    assert "app.jobs.scheduler" in source
    assert "replicas: 2" in source
    assert "documents.ocr" in (ROOT / "backend/app/jobs/handlers.py").read_text(encoding="utf-8")


def test_postgres_claim_uses_skip_locked():
    source = (ROOT / "backend/app/jobs/queue.py").read_text(encoding="utf-8")
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "lease_expires_at" in source
