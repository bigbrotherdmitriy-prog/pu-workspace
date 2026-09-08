"""Actual migrated PostgreSQL locks; opt-in owned database, never a live URL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.v54_authority import AuthorityResolver, PILOT_SCOPE
from app.local_upload_staging import LocalUploadLifecycleAdapter, LocalUploadRuntime, UploadScope, configure_local_upload_runtime
from app.models.management import Meeting, Obligation, ObligationHistory
from app.models.materialization import Materialization
from app.models.meeting_source_binding import MeetingSourceBinding
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import EvidenceAssessment
from app.mvp3.lifecycle import ManagementConflict, ManagementDenied
from app.mvp3.meeting_digest import MeetingProposalService
from app.mvp3.meeting_source_binding import MeetingSourceBindingService
from app.schema import CURRENT_SCHEMA_REVISION
from app.staging.contracts import KekRef
from app.staging.filesystem import FilesystemStagingStorage
from app.staging.lifecycle import LifecycleAuthority
from app.staging.local_upload import A05LocalUploadLifecycle
from test_mvp3_meeting_source_binding import _bind, _propose
from test_v54_local_upload_a05_wiring import Keys, Processor, _stage
from test_v54_source_evidence_pilot import policy
from v54_pilot_fixture import seed


def _owned_url(value):
    try:
        url = make_url(value)
    except Exception:
        raise ValueError("owned_mvp3_postgres_required") from None
    hosts = {"localhost", "127.0.0.1", "::1", "db"}
    if os.getenv("GITHUB_ACTIONS") == "true":
        hosts.add("postgres")
    if (url.get_backend_name() != "postgresql" or url.host not in hosts or url.query
            or re.fullmatch(r"puw_mvp3_test_[a-z0-9_]+", url.database or "") is None):
        raise ValueError("owned_mvp3_postgres_required")
    return url


@pytest.fixture
def pg_meeting(tmp_path, monkeypatch):
    value = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: owned MVP3 PostgreSQL not configured")
    url = _owned_url(value)
    schema = "meeting_binding_test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    engine = None
    created = False
    try:
        with admin.begin() as db:
            assert db.scalar(text("SELECT current_database()")) == url.database
            assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        isolated = url.update_query_dict({"options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000"})
        backend_path = Path(__file__).resolve().parents[1]
        config = Config(str(backend_path / "alembic.ini"))
        config.set_main_option("script_location", str(backend_path / "migrations"))
        config.set_main_option("sqlalchemy.url", isolated.render_as_string(hide_password=False).replace("%", "%%"))
        # Prevent env.py from replacing the already validated owned URL.
        monkeypatch.delenv("DATABASE_URL", raising=False)
        command.upgrade(config, CURRENT_SCHEMA_REVISION)
        engine = create_engine(isolated, hide_parameters=True, connect_args={"connect_timeout": 5})
        sessions = sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        with sessions.begin() as db:
            assert list(db.scalars(text("SELECT version_num FROM alembic_version"))) == ["a54f001c0a21"]
            seed(db)
            db.add(ProjectMember(project_id=4, user_id=2, role="owner"))
            db.add(AuthorityState(organization_id=1, project_id=4, principal_kind="user", principal_id="2",
                scope=PILOT_SCOPE, membership_role="owner", permissions=["metadata", "fragment", "write", "observe", "audit"],
                state="active", authority_epoch=1, record_version=1, valid_until=now + timedelta(hours=2), updated_at=now))
        source_policy = replace(policy(), grants=frozenset(), authority=AuthorityResolver(), valid_until=now + timedelta(hours=2))
        authority = LifecycleAuthority(policy=source_policy, allowed_residencies=frozenset({"local-test"}),
            allowed_keks=frozenset({KekRef("local-upload", "v1")}), max_retention=timedelta(hours=1),
            derive_allowed=True, retention_owner=True)
        storage = FilesystemStagingStorage(tmp_path / "ciphertext", Keys(), chunk_size=16)
        def authority_factory(db, upload_scope):
            assert upload_scope == UploadScope(2, 4)
            return authority
        backend = A05LocalUploadLifecycle(storage=storage, authority_factory=authority_factory,
            clock=lambda: datetime.now(timezone.utc), residency="local-test", kek=KekRef("local-upload", "v1"), max_file_bytes=1024)
        runtime = LocalUploadRuntime(storage=storage, lifecycle=LocalUploadLifecycleAdapter(backend),
            processor=Processor(), session_factory=sessions, kek=KekRef("local-upload", "v1"), max_file_bytes=1024,
            allowed_mime_types=frozenset({"text/plain"}), retention=timedelta(minutes=30))
        configure_local_upload_runtime(runtime)
        queued = _stage(sessions)
        with sessions.begin() as db:
            original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
            meeting = Meeting(project_id=4, created_by_user_id=2, title="Isolated PG protocol", status="completed", minutes="Human protocol")
            db.add(meeting)
            db.add(EvidenceAssessment(organization_id=1, evidence_id=original.evidence_id,
                freshness="fresh", availability="available", verification="unverified",
                checked_at=now, valid_until=now + timedelta(minutes=10)))
            db.flush()
            ids = dict(meeting_id=meeting.id, source_id=original.source_id,
                source_version_id=original.source_version_id, evidence_id=original.evidence_id)
        yield engine, sessions, ids
    finally:
        configure_local_upload_runtime(None)
        if engine is not None:
            engine.dispose()
        if created:
            # Only this invocation's validated UUID schema, in its owned DB.
            assert re.fullmatch(r"meeting_binding_test_[0-9a-f]{32}", schema)
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize("scenario", ["duplicate_bind", "duplicate_confirm", "bind_vs_stale_edit", "edit_vs_confirm"])
def test_pg_meeting_binding_serializes_actual_commands(pg_meeting, scenario):
    engine, sessions, ids = pg_meeting
    key = str(uuid4())
    if scenario in {"edit_vs_confirm", "duplicate_confirm"}:
        with sessions.begin() as db:
            proposal = _propose(db, ids, _bind(db, ids))
    started = Event()
    child_pid = []
    def competing():
        with sessions() as db:
            child_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                if scenario == "duplicate_bind":
                    result = _bind(db, ids, command=key)
                elif scenario == "bind_vs_stale_edit":
                    result = MeetingSourceBindingService().edit(db, project_id=4, meeting_id=ids["meeting_id"],
                        actor_user_id=2, expected_version=1, minutes="Stale overwrite", status="completed")
                else:
                    result = MeetingProposalService().confirm(db, project_id=4, actor_user_id=2, entity_type="obligation",
                        entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
                db.commit()
                return result
            except (ManagementConflict, ManagementDenied) as exc:
                db.rollback()
                return type(exc).__name__
    with sessions() as first, ThreadPoolExecutor(max_workers=1) as pool:
        if scenario == "edit_vs_confirm":
            MeetingSourceBindingService().edit(first, project_id=4, meeting_id=ids["meeting_id"], actor_user_id=2,
                expected_version=2, minutes="Changed protocol", status="completed")
        elif scenario == "duplicate_confirm":
            bound = MeetingProposalService().confirm(first, project_id=4, actor_user_id=2, entity_type="obligation",
                entity_id=proposal["entity_id"], expected_version=1, create_internal_task=True)
        else:
            bound = _bind(first, ids, command=key)
        future = pool.submit(competing)
        try:
            assert started.wait(3)
            deadline = monotonic() + 3
            blocked = False
            with engine.connect() as observer:
                while monotonic() < deadline:
                    if observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": child_pid[0]}):
                        blocked = True
                        break
                    sleep(.02)
            assert blocked, "competing command never reached a PostgreSQL row lock"
            first.commit()
            result = future.result(timeout=10)
        finally:
            first.rollback()
        assert result == (bound if scenario in {"duplicate_bind", "duplicate_confirm"} else
            "ManagementConflict" if scenario == "bind_vs_stale_edit" else "ManagementDenied")
    with sessions() as db:
        assert len(db.scalars(select(MeetingSourceBinding)).all()) == 1
        assert len(db.scalars(select(Task)).all()) == (1 if scenario == "duplicate_confirm" else 0)
        if scenario == "duplicate_confirm":
            assert len(db.scalars(select(ObligationHistory)).all()) == 2
        if scenario == "edit_vs_confirm":
            assert db.get(Obligation, proposal["entity_id"]).status == "needs_confirmation"
            assert len(db.scalars(select(ObligationHistory)).all()) == 1
        else:
            assert db.get(Meeting, ids["meeting_id"]).record_version == 2


def test_pg_meeting_binding_append_only_is_enforced_by_database(pg_meeting):
    from sqlalchemy.exc import DBAPIError
    _, sessions, ids = pg_meeting
    with sessions.begin() as db:
        binding = _bind(db, ids)
    with sessions() as db:
        with pytest.raises(DBAPIError, match="meeting_source_binding_is_append_only"):
            db.execute(text("UPDATE meeting_source_bindings SET meeting_record_version=99 WHERE id=:id"), {"id": binding["binding_id"]})
        db.rollback()
        assert db.get(MeetingSourceBinding, binding["binding_id"]).meeting_record_version == 2


@pytest.mark.parametrize("url", ["postgresql://u:p@example.com/puw_mvp3_test_x", "postgresql://u:p@localhost/production",
    "postgresql://u:p@localhost/puw_mvp3_test_x?options=-csearch_path=public", "sqlite:///test.db"])
def test_meeting_pg_fixture_rejects_unowned_urls(url):
    with pytest.raises(ValueError, match="owned_mvp3_postgres_required"):
        _owned_url(url)
