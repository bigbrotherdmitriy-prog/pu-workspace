"""The scheduler must not race the backend's Alembic bootstrap."""

from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from app.core import readiness as readiness_module
from app.models.organizer import OrganizerSession


ROOT = Path(__file__).resolve().parents[2]


def test_organizer_sessions_is_the_current_migrated_table():
    initial = (ROOT / "backend/migrations/versions/4d7fb326d458_initial_schema.py").read_text(encoding="utf-8")
    assert OrganizerSession.__tablename__ == "organizer_sessions"
    assert "op.create_table('organizer_sessions'" in initial


def test_legacy_scheduler_waits_for_backend_migration_and_preflight():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    backend = compose.split("  backend:\n", 1)[1].split("  worker:\n", 1)[0]
    worker = compose.split("  worker:\n", 1)[1].split("  scheduler:\n", 1)[0]
    scheduler = compose.split("  scheduler:\n", 1)[1].split("  telegram-relay:\n", 1)[0]
    assert "alembic -c alembic.ini upgrade head && python -m scripts.preflight --startup && uvicorn" in backend
    assert "healthcheck:" in backend and "127.0.0.1:8000/health" in backend
    assert "      backend:\n        condition: service_healthy" in worker
    assert "      backend:\n        condition: service_healthy" in scheduler


def test_startup_preflight_waits_for_schema_but_not_unstarted_services(monkeypatch):
    monkeypatch.setenv("PU_BACKGROUND_EXECUTION", "durable")
    monkeypatch.setenv("APP_SECRET_KEY", "a" * 32)
    monkeypatch.setenv("BOOTSTRAP_TOKEN", "b" * 24)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    database = create_engine("sqlite+pysqlite:///:memory:")
    with database.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": readiness_module.CURRENT_SCHEMA_REVISION},
        )
    monkeypatch.setattr(readiness_module, "engine", database)

    class EmptySession:
        def scalars(self, _query):
            return []

    @contextmanager
    def empty_session():
        yield EmptySession()

    monkeypatch.setattr(readiness_module, "SessionLocal", empty_session)
    startup = readiness_module.readiness_report(include_live_services=False)
    runtime = readiness_module.readiness_report()

    assert startup["ready"] is True
    assert startup["checks"]["schema"]["ok"] is True
    assert "durable_workers" not in startup["checks"]
    assert "durable_scheduler" not in startup["checks"]
    assert runtime["ready"] is False
    assert runtime["checks"]["durable_workers"]["ok"] is False
    assert runtime["checks"]["durable_scheduler"]["ok"] is False

    monkeypatch.setenv("APP_SECRET_KEY", "")
    assert readiness_module.readiness_report(include_live_services=False)["ready"] is False
    database.dispose()
