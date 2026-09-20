"""Opt-in PostgreSQL race gate for exact meeting source proposals."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.database import Base
from app.document_extraction import CombinedExtraction, ObligationCandidate
from app.models.management import Meeting, MeetingProposal
from app.models.materialization import Materialization
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.models.v54_pilot import ConnectionIdentity, Evidence, SourceCurrent, SourceReference, SourceVersion
from app.mvp3 import meeting_proposals as service


@pytest.fixture
def authority_pg_engine():
    raw = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not raw and os.getenv("PU_TEST_POSTGRES") == "1":
        raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL MVP3 meeting authority gate not configured")
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.host in {"localhost", "127.0.0.1", "::1", "postgres", "db"}
    assert url.database and "test" in url.database
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    base = create_engine(url, connect_args={"connect_timeout": 5})
    schema = "mvp3_meeting_authority_" + uuid4().hex
    with base.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with base.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def _setup(engine):
    now = datetime.now(timezone.utc)
    ids = {key: str(uuid4()) for key in ("identity", "source", "version", "evidence", "materialization")}
    staging_id = UUID(ids["materialization"]).hex
    with Session(engine) as db:
        org = Organization(name="PG meeting authority")
        manager = User(name="Manager", email=f"pg-meeting-{uuid4().hex}@example.test", is_admin=False)
        db.add_all([org, manager]); db.flush()
        project = Project(name="PG project", organization_id=org.id)
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=manager.id, role="manager"))
        meeting = Meeting(
            project_id=project.id, created_by_user_id=manager.id, title="PG meeting",
            minutes="Подготовить протокол до 25.09.2026", status="completed", record_version=2,
        )
        db.add(meeting); db.flush()
        identity = ConnectionIdentity(
            id=ids["identity"], organization_id=org.id, provider="local_upload",
            account_key="a" * 64, state="verified", binding_epoch=1,
            credential_generation=1, record_version=1, verified_at=now,
        )
        source = SourceReference(
            id=ids["source"], organization_id=org.id, origin_project_id=project.id,
            identity_id=ids["identity"], namespace="local-upload", external_id="b" * 64,
            external_id_kind="stable_id", incarnation=1, object_kind="file",
            canonical_locator={"kind": "opaque_id", "value": staging_id, "normalization_version": "1"},
            record_version=1, freshness="fresh", sync_state="current", availability="available",
            last_seen_at=now, last_checked_at=now, next_check_at=now + timedelta(hours=2),
            policy_pins={"access": {}, "retention": {}, "residency": {}},
            residency={"source_location": "test"},
        )
        version = SourceVersion(
            id=ids["version"], organization_id=org.id, source_id=ids["source"], revision=1,
            observation_key="b" * 64, provider_revision="c" * 64,
            consistency="digest_observed", observed_at=now,
            locator_at_observation={"kind": "local_upload", "staging_id": staging_id,
                                    "display_name": "minutes.txt", "media_type": "text/plain",
                                    "size": 64, "fence": "d" * 32},
            integrity=[{"algorithm": "sha256", "value": "e" * 64}],
        )
        evidence = Evidence(
            id=ids["evidence"], organization_id=org.id, source_id=ids["source"],
            source_version_id=ids["version"], revision=1,
            locator={"kind": "whole_object", "reason_code": "local_upload"},
            extractor={"name": "local_upload", "version": "1"}, extracted_at=now,
            confidence_kind="unknown", policy_pins=source.policy_pins,
        )
        materialization = Materialization(
            id=ids["materialization"], organization_id=org.id, project_id=project.id,
            owner_id=manager.id, source_id=ids["source"], source_version_id=ids["version"],
            evidence_id=ids["evidence"], parent_id=None, object_id="f" * 32,
            state="DERIVED", record_version=4, active_fence=None,
            kek_reference="test", kek_version="v1", format_version=1,
            chunk_size=1024, wrapped_dek="wrapped",
            manifest={"storage": {"object_id": "f" * 32}}, residency="test",
            retention_until=now + timedelta(hours=2), copy_allowed=False, derive_allowed=True,
            admitted_at=now, writing_at=now, sealed_at=now, derived_at=now,
        )
        db.add_all([identity, source]); db.flush()
        db.add(version); db.flush()
        db.add_all([SourceCurrent(
            source_id=ids["source"], organization_id=org.id, version_id=ids["version"],
        ), evidence]); db.flush()
        db.add(materialization)
        db.commit()
        return meeting.id, manager.id, {
            "source_id": ids["source"], "source_version_id": ids["version"],
            "evidence_id": ids["evidence"], "materialization_id": ids["materialization"],
        }


def _extraction():
    return CombinedExtraction(obligations=[ObligationCandidate(
        title="Подготовить протокол до 25.09.2026", excerpt="Подготовить протокол до 25.09.2026",
        due_date=datetime(2026, 9, 25).date(), due_date_evidence_quote="до 25.09.2026",
        assignee_hint=None, assignee_evidence_quote=None, amount=None,
        amount_currency=None, amount_evidence_quote=None, confidence=0.9,
        extraction_method="regex",
    )], extraction_method="regex")


def test_postgres_binding_and_confirmation_are_single_winner_cas(authority_pg_engine, monkeypatch):
    meeting_id, manager_id, source = _setup(authority_pg_engine)
    monkeypatch.setattr(service, "extract_for_text", lambda *_args, **_kwargs: _extraction())

    def bind(command_id):
        with Session(authority_pg_engine) as db:
            try:
                result = service.bind_current_source(
                    db, meeting_id=meeting_id, actor_user_id=manager_id,
                    expected_record_version=2, command_id=command_id, **source,
                )
                db.commit()
                return "ok", result
            except Exception as exc:  # observed after the competing transaction commits
                db.rollback()
                return type(exc).__name__, str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        binding_results = list(pool.map(bind, [str(uuid4()), str(uuid4())]))
    assert [row[0] for row in binding_results].count("ok") == 1
    assert any(row[1] == "record_version_conflict" for row in binding_results if row[0] != "ok")
    winning = next(row[1] for row in binding_results if row[0] == "ok")
    proposal_id = winning["proposals"][0]["id"]

    def confirm(command_id):
        with Session(authority_pg_engine) as db:
            try:
                result = service.confirm_proposal(
                    db, proposal_id=proposal_id, actor_user_id=manager_id,
                    expected_record_version=1, command_id=command_id,
                )
                db.commit()
                return "ok", result
            except Exception as exc:
                db.rollback()
                return type(exc).__name__, str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmation_results = list(pool.map(confirm, [str(uuid4()), str(uuid4())]))
    assert [row[0] for row in confirmation_results].count("ok") == 1
    assert any(row[1] == "already_confirmed" for row in confirmation_results if row[0] != "ok")
    with Session(authority_pg_engine) as db:
        assert db.scalar(select(MeetingProposal.status).where(MeetingProposal.id == proposal_id)) == "confirmed"
        assert db.query(Task).count() == 1
