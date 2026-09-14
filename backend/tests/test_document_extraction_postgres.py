"""Opt-in PostgreSQL proof for the LLM extraction pipeline: the bounded worker
pool (Вариант Б) makes real concurrent network-shaped calls while a real
Postgres-backed Session does all its DB reads/writes on the main thread only.
SQLite in-memory is not evidence of thread-safety under a real connection.
"""
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models
from app import document_extraction as de
from app.core.integration_types import StorageObject
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User


def _safe_url() -> str:
    value = os.getenv("PUW_LLM_EXTRACTION_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: PUW_LLM_EXTRACTION_DATABASE_URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_llm_test_") and not parsed.query
    return value


@pytest.fixture
def pg_session():
    engine = create_engine(_safe_url())
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()
    engine.dispose()


def _world(db):
    # Real, persistent database shared across test runs -- every row must be
    # unique, unlike the sqlite-in-memory fixture that resets per test.
    suffix = uuid4().hex
    user = User(name="Synthetic Owner", email=f"owner-{suffix}@example.test", is_admin=False)
    db.add(user)
    db.flush()
    org = Organization(name=f"Synthetic Org {suffix}")
    db.add(org)
    db.flush()
    project = Project(name="Synthetic Project", organization_id=org.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    db.commit()
    return user, project


def test_migration_columns_round_trip_on_real_postgres(pg_session):
    user, project = _world(pg_session)
    task = Task(
        project_id=project.id, assignee_user_id=user.id, created_by_user_id=user.id,
        title="t", status="assigned", priority="normal", source_file_id="f1",
        source_file_name="f1.txt", source_excerpt="e", source_excerpt_hash="h" * 64,
        confidence=0.9, source_type="document_analysis",
        amount="1500.50", amount_currency="RUB", amount_evidence_quote="1500.50 руб.",
        assignee_hint="Иванов", assignee_evidence_quote="Иванов обязан",
        extraction_method="llm",
    )
    pg_session.add(task)
    pg_session.commit()
    pg_session.refresh(task)
    assert str(task.amount) == "1500.50"
    assert task.amount_currency == "RUB"
    assert task.extraction_method == "llm"


def test_bounded_pool_makes_real_concurrent_calls_while_db_writes_stay_sequential(pg_session, monkeypatch):
    """The actual invariant the design depends on: worker threads never touch
    the SQLAlchemy Session; only the main thread commits."""
    user, project = _world(pg_session)
    monkeypatch.setenv("LLM_EXTRACTION_CONCURRENCY", "3")
    in_flight = {"current": 0, "max_seen": 0}
    lock = threading.Lock()
    session_thread_violations = []
    main_thread_id = threading.get_ident()

    def fake_extract_fields(text, filename, project_name, member_names):
        # A worker thread must never be the one holding/using pg_session.
        if threading.get_ident() == main_thread_id and int(os.getenv("LLM_EXTRACTION_CONCURRENCY", "1")) > 1:
            session_thread_violations.append(filename)
        with lock:
            in_flight["current"] += 1
            in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        time.sleep(0.1)  # shaped like a real network round trip
        with lock:
            in_flight["current"] -= 1
        return {"obligations": [], "response_candidates": [], "risks": [], "decisions": []}

    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)
    provider.extract_fields.side_effect = fake_extract_fields
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)

    files = [
        StorageObject(id=f"pg-f{i}", name=f"pg-file{i}.txt", mime_type="text/plain", parent_id="root",
                     content_text=f"Исполнитель должен подготовить документ {i}.")
        for i in range(9)
    ]
    started = time.monotonic()
    de.extract_for_files(pg_session, files, project.id)
    elapsed = time.monotonic() - started

    assert in_flight["max_seen"] == 3  # exactly the configured bound, not 1 and not 9
    assert elapsed < 9 * 0.1  # genuinely concurrent, not serialized
    assert all(f.llm_extraction_cache is not None for f in files)


def test_create_tasks_from_files_persists_correctly_after_pooled_extraction(pg_session, monkeypatch):
    from app.task_engine import create_tasks_from_files

    user, project = _world(pg_session)
    monkeypatch.setenv("LLM_EXTRACTION_CONCURRENCY", "4")

    def fake_extract_fields(text, filename, project_name, member_names):
        time.sleep(0.02)
        return {
            "obligations": [{
                "title": f"Задача из {filename}", "evidence_quote": "Исполнитель должен подготовить документ",
                "due_date": None, "due_date_evidence_quote": None,
                "assignee_hint": None, "assignee_evidence_quote": None,
                "amount": None, "amount_currency": None, "amount_evidence_quote": None,
                "confidence": "high",
            }],
            "response_candidates": [], "risks": [], "decisions": [],
        }

    provider = Mock()
    provider.health.return_value = SimpleNamespace(ready=True)
    provider.extract_fields.side_effect = fake_extract_fields
    monkeypatch.setattr(de, "configured_ai_provider", lambda: provider)

    files = [
        StorageObject(id=f"pg-task-f{i}", name=f"pg-task{i}.txt", mime_type="text/plain", parent_id="root",
                     content_text="Исполнитель должен подготовить документ.")
        for i in range(6)
    ]
    tasks = create_tasks_from_files(pg_session, project.id, None, files)
    assert len(tasks) == 6
    assert {t.source_file_id for t in tasks} == {f.id for f in files}
    assert all(t.extraction_method == "llm" for t in tasks)
