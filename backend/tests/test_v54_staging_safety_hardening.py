"""Regression coverage for the Wave 3 local-upload rollout blockers."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import StringIO
import logging
import os
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

import app.local_upload_staging as local_staging
from app.jobs.queue import execution_owner
from app.local_upload_staging import (
    LocalUploadRuntime, LocalUploadUnavailable, configure_local_upload_runtime,
    recover_local_upload_retention, run_local_upload_job,
)
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.job import BackgroundJob
from app.models.materialization import Materialization
from app.models.v54_pilot import AuditExtension
from app.schema import CURRENT_SCHEMA_REVISION
from app.staging.local_upload import LocalUploadRetentionAuthority
from app.staging.contracts import KekRef
from app.document_engine import index_documents
from app.core.integration_types import StorageObject
from test_v54_local_upload_a05_wiring import Processor, _claimed, _stage, wired


BACKEND = Path(__file__).resolve().parents[1]


def _safe_result(documents=()):
    return {
        "processed": 1, "skipped": 0, "tasks": 0, "risks": 0,
        "decisions": 0, "drafts": 0, "documents": list(documents),
    }


class LoseLeaseBeforeCommit:
    """Simulate an old attempt reaching a legacy helper after lease recovery."""

    def __init__(self, engine):
        self.engine = engine

    def process(self, session, *, record, content, operation_key):
        with self.engine.begin() as connection:
            connection.execute(update(BackgroundJob).where(
                BackgroundJob.id == record.job_id,
            ).values(
                worker_id="worker-new", attempts=BackgroundJob.attempts + 1,
                locked_at=datetime.now(timezone.utc) + timedelta(seconds=1),
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ))
        document = Document(
            project_id=record.scope.project_id,
            external_id=f"local:{record.staging_id}",
            source="local_upload", name="redacted.txt", mime_type="text/plain",
        )
        session.add(document)
        session.commit()
        return _safe_result([document.id])


def _install_retention_authority(backend, *, scopes=frozenset({(1, 4)})):
    backend.retention_authority = LocalUploadRetentionAuthority(
        service_principal="staging-retention",
        scopes=scopes,
        allowed_residencies=frozenset({"local-test"}),
        allowed_keks=frozenset({KekRef("local-upload", "v1")}),
    )


def _expire_failed_upload(wired):
    _, sessions, runtime, _, backend, _ = wired
    queued = _stage(sessions)
    configure_local_upload_runtime(replace(
        runtime, processor=Processor(error=RuntimeError("private body")),
    ))
    claim = _claimed(sessions, "worker-fail")
    with execution_owner(claim[0], claim[1], attempt=claim[2], locked_at=claim[3]):
        with pytest.raises(RuntimeError):
            run_local_upload_job({"staging_id": queued.staging_id})
    with sessions.begin() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(
            status="dead_letter", worker_id=None, locked_at=None,
            lease_expires_at=None,
        ))
        expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.execute(update(Materialization).where(
            Materialization.id == str(UUID(hex=queued.staging_id)),
        ).values(retention_until=expired))
    backend.clock = lambda: datetime.now(timezone.utc)
    _install_retention_authority(backend)
    configure_local_upload_runtime(replace(
        runtime, lifecycle=local_staging.LocalUploadLifecycleAdapter(backend),
    ))
    return queued


def test_every_business_commit_is_fenced_by_exact_live_attempt(wired):
    engine, sessions, runtime, _, _, _ = wired
    queued = _stage(sessions)
    configure_local_upload_runtime(replace(runtime, processor=LoseLeaseBeforeCommit(engine)))
    claim = _claimed(sessions, "worker-old")

    with execution_owner(claim[0], claim[1], attempt=claim[2], locked_at=claim[3]):
        with pytest.raises(LocalUploadUnavailable):
            run_local_upload_job({"staging_id": queued.staging_id})

    with sessions() as db:
        assert db.scalar(select(Document).where(
            Document.project_id == 4,
            Document.source == "local_upload",
            Document.external_id == f"local:{queued.staging_id}",
        )) is None


def test_database_rejects_duplicate_local_document_identity(wired):
    _, sessions, _, _, _, _ = wired
    with sessions() as db:
        db.add_all([
            Document(project_id=4, external_id="local:stable", source="local_upload", name="a"),
            Document(project_id=4, external_id="local:stable", source="local_upload", name="b"),
        ])
        with pytest.raises(IntegrityError):
            db.flush()


def test_local_document_identity_does_not_reuse_another_provider(wired):
    _, sessions, _, _, _, _ = wired
    with sessions() as db:
        db.add(Document(
            project_id=4, external_id="local:stable", source="google_drive",
            name="provider.txt",
        ))
        db.commit()
        indexed = index_documents(db, 4, [StorageObject(
            id="local:stable", name="local.txt", mime_type="text/plain",
            parent_id=None, content_text="synthetic local content",
        )], "local_upload")
        assert indexed[0].source == "local_upload"
        assert db.scalar(select(Document).where(
            Document.project_id == 4, Document.external_id == "local:stable",
            Document.source == "google_drive",
        )).name == "provider.txt"


def test_service_retention_purges_dead_letter_without_user_authority(wired):
    _, sessions, _, _, backend, tmp_path = wired
    queued = _expire_failed_upload(wired)
    backend.authority_factory = lambda *_: (_ for _ in ()).throw(
        AssertionError("retention must not impersonate the former user"),
    )

    assert recover_local_upload_retention(limit=10) == 1
    assert not list((tmp_path / "ciphertext").rglob("*.enc"))

    with sessions() as db:
        row = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        assert row.state == "PURGED"
        assert row.manifest == {
            "schema_version": "v54.materialization.tombstone.1",
            "outcome": "dead_letter",
        }
        service_events = list(db.scalars(select(AuditExtension).where(
            AuditExtension.subject_id == row.id,
            AuditExtension.service_principal == "staging-retention",
        )))
        assert [event.actor_id for event in service_events] == [None, None]
        assert len(service_events) == 2
        rendered = repr(list(db.scalars(select(AuditLog)))) + repr(service_events)
        assert "private body" not in rendered


def test_retention_recovery_resumes_from_durable_expired_after_delete_failure(wired, monkeypatch):
    _, sessions, runtime, _, _, tmp_path = wired
    queued = _expire_failed_upload(wired)
    original_delete = runtime.storage.delete
    calls = 0

    def fail_once(object_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("private path must not escape")
        return original_delete(object_id)

    monkeypatch.setattr(runtime.storage, "delete", fail_once)
    assert recover_local_upload_retention(limit=10) == 0
    with sessions() as db:
        assert db.get(Materialization, str(UUID(hex=queued.staging_id))).state == "EXPIRED"
    assert list((tmp_path / "ciphertext").rglob("*.enc"))

    assert recover_local_upload_retention(limit=10) == 1
    with sessions() as db:
        assert db.get(Materialization, str(UUID(hex=queued.staging_id))).state == "PURGED"
    assert not list((tmp_path / "ciphertext").rglob("*.enc"))


def test_retention_service_denies_unconfigured_project_scope(wired):
    _, sessions, _, _, backend, tmp_path = wired
    queued = _expire_failed_upload(wired)
    _install_retention_authority(backend, scopes=frozenset({(1, 999)}))

    assert recover_local_upload_retention(limit=10) == 0
    with sessions() as db:
        assert db.get(Materialization, str(UUID(hex=queued.staging_id))).state == "DERIVED"
    assert list((tmp_path / "ciphertext").rglob("*.enc"))


def test_a08_is_single_head_and_renders_safety_constraints(monkeypatch):
    output = StringIO()
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == [CURRENT_SCHEMA_REVISION]
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline",
    )
    local_logger = logging.getLogger("pu.local_upload_staging")
    was_disabled = local_logger.disabled
    try:
        command.upgrade(config, "a54f001c0a07:a54f001c0a08", sql=True)
    finally:
        local_logger.disabled = was_disabled
    rendered = output.getvalue()
    assert "uq_documents_local_upload_identity" in rendered
    assert "service_principal" in rendered
    assert "ck_v54_audit_actor_origin" in rendered


def test_a08_offline_downgrade_retains_service_audit_guard(monkeypatch):
    output = StringIO()
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline",
    )

    command.downgrade(config, "a54f001c0a08:a54f001c0a07", sql=True)

    rendered = output.getvalue()
    guard = "Service retention audit records require explicit archival"
    assert guard in rendered
    assert rendered.index(guard) < rendered.index("DROP COLUMN service_principal")
    assert "DROP INDEX uq_documents_local_upload_identity" in rendered


def test_postgresql_a08_constraints_are_present():
    value = os.getenv("PUW_V54_PROVIDER_MIGRATION_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: staging-safety PostgreSQL URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_v54_test_") and not parsed.query
    engine = create_engine(value, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
        indexes = {item["name"]: item for item in inspect(engine).get_indexes("documents")}
        assert indexes["uq_documents_local_upload_identity"]["unique"] is True
        columns = {item["name"]: item for item in inspect(engine).get_columns("v54_audit_extensions")}
        assert columns["actor_id"]["nullable"] is True
        assert columns["service_principal"]["nullable"] is True
        checks = {item["name"] for item in inspect(engine).get_check_constraints("v54_audit_extensions")}
        assert "ck_v54_audit_actor_origin" in checks
    finally:
        engine.dispose()
