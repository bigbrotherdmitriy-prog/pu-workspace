"""The scheduler must not race the backend's Alembic bootstrap."""

from pathlib import Path

from app.models.organizer import OrganizerSession


ROOT = Path(__file__).resolve().parents[2]


def test_organizer_sessions_is_the_current_migrated_table():
    initial = (ROOT / "backend/migrations/versions/4d7fb326d458_initial_schema.py").read_text(encoding="utf-8")
    assert OrganizerSession.__tablename__ == "organizer_sessions"
    assert "op.create_table('organizer_sessions'" in initial


def test_legacy_scheduler_waits_for_backend_migration_and_preflight():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    backend = compose.split("  backend:\n", 1)[1].split("  worker:\n", 1)[0]
    scheduler = compose.split("  scheduler:\n", 1)[1].split("  telegram-relay:\n", 1)[0]
    assert "alembic -c alembic.ini upgrade head && python -m scripts.preflight && uvicorn" in backend
    assert "healthcheck:" in backend and "127.0.0.1:8000/health" in backend
    assert "      backend:\n        condition: service_healthy" in scheduler
