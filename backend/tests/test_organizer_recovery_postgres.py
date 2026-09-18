"""Opt-in recovery acceptance on a disposable PostgreSQL schema.

Set PUW_ORGANIZER_TEST_DATABASE_URL to an isolated, pre-created local database
named puw_organizer_test_*. Never point this test at application data.
"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.organizer as organizer
from app.schema import CURRENT_SCHEMA_REVISION


def _test_url() -> str:
    value = os.getenv("PUW_ORGANIZER_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: isolated PostgreSQL DSN is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_organizer_test_") and not parsed.query
    return value


def test_recover_incomplete_scans_after_real_postgres_migration(monkeypatch):
    url = _test_url()
    schema = "organizer_recovery_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped_url, hide_parameters=True, connect_args={"connect_timeout": 5})
    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "migrations"))
    monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    monkeypatch.setattr(organizer, "SessionLocal", sessionmaker(bind=engine))
    try:
        command.upgrade(cfg, "head")
        assert "organizer_sessions" in inspect(engine).get_table_names()
        with engine.begin() as db:
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
            db.execute(text("INSERT INTO organizations(id,name) VALUES (9001,'Organizer test')"))
            db.execute(text("INSERT INTO projects(id,name,organization_id) VALUES (9001,'Organizer test',9001)"))
            for session_id, status, copy_id in (
                (9001, "queued", None), (9002, "scanning", None),
                (9003, "analyzing", "safe-copy"), (9004, "ready", None),
            ):
                db.execute(text("""
                    INSERT INTO organizer_sessions
                    (id,project_id,source_folder_id,source_folder_name,copy_folder_id,
                     status,progress,processed_item_count)
                    VALUES (:id,9001,:source,'Synthetic source',:copy,:status,70,2)
                """), {"id": session_id, "source": f"source-{session_id}",
                       "copy": copy_id, "status": status})

        assert organizer.recover_incomplete_scans() == 3
        with engine.connect() as db:
            rows = db.execute(text("""
                SELECT id,status,progress,processed_item_count FROM organizer_sessions ORDER BY id
            """)).all()
            assert rows == [
                (9001, "queued", 0, 0), (9002, "queued", 0, 0),
                (9003, "queued", 55, 0), (9004, "ready", 70, 2),
            ]
            jobs = db.execute(text("""
                SELECT idempotency_key,payload FROM background_jobs
                WHERE kind='organizer.scan' ORDER BY idempotency_key
            """)).all()
            assert len(jobs) == 3
            assert [key for key, _ in jobs] == [
                "organizer.scan:9001", "organizer.scan:9002", "organizer.scan:9003",
            ]
            assert {payload["session_id"] for _, payload in jobs} == {9001, 9002, 9003}

        # A second startup may see queued sessions, but must not duplicate jobs.
        assert organizer.recover_incomplete_scans() == 3
        with engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM background_jobs WHERE kind='organizer.scan'")) == 3
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
