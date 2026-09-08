"""Real encrypted local lifecycle; candidate fairness is not delete authority."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import re
from threading import Event
from time import monotonic, sleep
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.v54_authority import AuthorityResolver, PILOT_SCOPE
from app.core.v54_permissions import SourceEvidenceError
from app.local_upload_staging import LocalUploadLifecycleAdapter, LocalUploadRuntime, UploadCandidate, UploadScope, stage_and_enqueue
from app.models.job import BackgroundJob
from app.models.materialization import Materialization
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import Evidence, SourceReference
from app.schema import CURRENT_SCHEMA_REVISION
from app.source_evidence.xlsx_ingestion import XlsxEvidenceIngestion
from app.staging.contracts import KekRef
from app.staging.filesystem import FilesystemStagingStorage
from app.staging.lifecycle import LifecycleAuthority
from app.staging.local_upload import A05LocalUploadLifecycle, LocalUploadRetentionAuthority
from test_mvp1_xlsx_durable_evidence import xlsx_world, stage_xlsx, run_xlsx  # noqa: F401
from test_mvp1_local_source_authority import local_source_world  # noqa: F401
from test_v54_local_upload_a05_wiring import Keys, Processor, wired  # noqa: F401
from test_v54_local_upload_a05_postgres import safe_url
from test_v54_source_evidence_pilot import policy as source_policy
from v54_pilot_fixture import seed


def _expired_batches(world, count=3):
    queued = []
    for index in range(count):
        upload, _ = stage_xlsx(world, key=f"retention-batch-{index}")
        run_xlsx(world, upload, worker=f"batch-{index}")
        queued.append(upload)
    with world[0].begin() as db:
        db.execute(update(BackgroundJob).values(status="completed"))
    world[2].clock = lambda: datetime.now(timezone.utc) + timedelta(minutes=31)
    return [str(UUID(hex=row.staging_id)) for row in queued]


@pytest.mark.parametrize("excluded", ["provider", "extractor", "nonterminal"])
def test_unsupported_prefix_does_not_starve_scoped_local_children(xlsx_world, excluded):
    originals = _expired_batches(xlsx_world)
    with xlsx_world[0].begin() as db:
        sources = list(db.scalars(select(Materialization.source_id).where(Materialization.id.in_(originals[:2]))))
        if excluded == "provider":
            db.execute(update(SourceReference).where(SourceReference.id.in_(sources)).values(namespace="unsupported"))
        elif excluded == "extractor":
            db.execute(update(Evidence).where(Evidence.source_id.in_(sources), Evidence.extractor["name"].as_string() == "xlsx_cells")
                       .values(extractor={"name": "another_extractor", "version": "1"}))
        else:
            keys = list(db.scalars(select(SourceReference.external_id).where(SourceReference.id.in_(sources))))
            db.execute(update(BackgroundJob).where(BackgroundJob.idempotency_key.in_(["local-upload:" + key for key in keys]))
                       .values(status="running"))
    ingestion = xlsx_world[1].processor.xlsx_ingestion
    assert ingestion.recover_retention(xlsx_world[0], limit=1) == 1
    assert ingestion.recover_retention(xlsx_world[0], limit=1) == 1
    assert ingestion.recover_retention(xlsx_world[0], limit=1) == 1
    with xlsx_world[0]() as db:
        assert all(row.state == "DERIVED" for row in db.scalars(select(Materialization).where(
            Materialization.parent_id.in_(originals[:2]))))
    assert len(list(xlsx_world[3].rglob("*.enc"))) == 6


def test_denied_prefix_advances_bounded_cursor_without_deleting_denied_rows(xlsx_world):
    originals = _expired_batches(xlsx_world)
    with xlsx_world[0].begin() as db:
        denied = list(db.scalars(select(Materialization).where(Materialization.parent_id.in_(originals[:2]))))
        denied_ids = [row.id for row in denied]
        # Well-scoped local candidates, but their exact child identity is broken.
        # Never follow this malformed handle to deletion.
        for index, row in enumerate(denied):
            db.execute(update(Materialization).where(Materialization.id == row.id)
                       .values(object_id=f"{index + 1:032x}"))
    ingestion = xlsx_world[1].processor.xlsx_ingestion
    results = [ingestion.recover_retention(xlsx_world[0], limit=1) for _ in range(5)]
    assert sum(results) == 3
    with xlsx_world[0]() as db:
        assert all(row.state == "DERIVED" for row in db.scalars(select(Materialization).where(
            Materialization.id.in_(denied_ids))))
    assert len(list(xlsx_world[3].rglob("*.enc"))) == 6


def test_original_recovery_never_visits_child_rows(xlsx_world, monkeypatch):
    _expired_batches(xlsx_world, count=1)
    visited = []
    original = xlsx_world[2]._retention_binding
    def record(db, identity):
        visited.append(identity)
        return original(db, identity)
    monkeypatch.setattr(xlsx_world[2], "_retention_binding", record)
    assert xlsx_world[2].recover_retention(xlsx_world[0], limit=5) == 0
    assert visited == []  # original is already purged; only children remain


def test_original_retention_locks_project_before_materialization(xlsx_world):
    upload, _ = stage_xlsx(xlsx_world)
    identity = str(UUID(hex=upload.staging_id))
    with xlsx_world[0].begin() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == upload.job_id).values(status="failed"))
    locks = []
    with xlsx_world[0]() as db:
        def observe(state):
            if getattr(state.statement, "_for_update_arg", None) is not None:
                locks.append(str(state.statement))
        event.listen(db, "do_orm_execute", observe)
        xlsx_world[2]._retention_binding(db, identity)
    assert "FROM projects" in locks[0]
    assert "FROM v54_materializations" in locks[1]


def test_original_retention_preserves_pending_binding_changes_on_denial(xlsx_world):
    upload, _ = stage_xlsx(xlsx_world)
    identity = str(UUID(hex=upload.staging_id))
    with xlsx_world[0]() as db:
        row = db.get(Materialization, identity)
        changed = row.retention_until + timedelta(hours=1)
        row.retention_until = changed
        with pytest.raises(SourceEvidenceError):
            xlsx_world[2]._retention_binding(db, identity)
        assert row.retention_until == changed and row in db.dirty


def test_restart_cursor_is_only_a_bounded_hint_and_repaired_prefix_is_revisited(xlsx_world, monkeypatch):
    originals = _expired_batches(xlsx_world)
    with xlsx_world[0].begin() as db:
        rows = list(db.scalars(select(Materialization).where(Materialization.parent_id.in_(originals[:2]))))
        actual_handles = {row.id: row.object_id for row in rows}
        for index, row in enumerate(rows):
            db.execute(update(Materialization).where(Materialization.id == row.id).values(object_id=f"{index+1:032x}"))
    attempted = set()
    require = LocalUploadRetentionAuthority.require
    def observe(self, db, row):
        if getattr(row, "parent_id", None) is not None:
            attempted.add(row.id)
        return require(self, db, row)
    monkeypatch.setattr(LocalUploadRetentionAuthority, "require", observe)
    ingestion = xlsx_world[1].processor.xlsx_ingestion
    assert ingestion.recover_retention(xlsx_world[0], limit=1) == 0
    assert len(attempted) == 4
    # Recreating runtime discards only scheduling progress, never durable state.
    ingestion = XlsxEvidenceIngestion(xlsx_world[2])
    attempted.clear()
    assert ingestion.recover_retention(xlsx_world[0], limit=1) == 0
    assert len(attempted) == 4
    assert sum(ingestion.recover_retention(xlsx_world[0], limit=1) for _ in range(4)) == 3
    # A repaired candidate before the cursor is retried after bounded wrap.
    with xlsx_world[0].begin() as db:
        for identity, handle in actual_handles.items():
            db.execute(update(Materialization).where(Materialization.id == identity).values(object_id=handle))
    assert sum(ingestion.recover_retention(xlsx_world[0], limit=1) for _ in range(8)) == 6
    assert not list(xlsx_world[3].rglob("*.enc"))


def test_cursor_cannot_override_changed_retention_capability(xlsx_world):
    _expired_batches(xlsx_world)
    ingestion = xlsx_world[1].processor.xlsx_ingestion
    assert ingestion.recover_retention(xlsx_world[0], limit=1) == 1
    previous = xlsx_world[2].retention_authority
    xlsx_world[2].retention_authority = LocalUploadRetentionAuthority(
        service_principal=previous.service_principal, scopes=frozenset({(1, 999)}),
        allowed_residencies=previous.allowed_residencies, allowed_keks=previous.allowed_keks)
    assert ingestion.recover_retention(xlsx_world[0], limit=1) == 0
    assert len(list(xlsx_world[3].rglob("*.enc"))) == 8


@pytest.fixture
def pg_original(tmp_path, monkeypatch):
    # Existing allowlisted local-upload test URL; never DATABASE_URL or production.
    url = make_url(safe_url())
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    schema = "v7_retention_test_" + uuid4().hex
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
        monkeypatch.delenv("DATABASE_URL", raising=False)
        command.upgrade(config, CURRENT_SCHEMA_REVISION)
        engine = create_engine(isolated, hide_parameters=True, connect_args={"connect_timeout": 5})
        sessions = sessionmaker(engine, expire_on_commit=False)
        now = datetime.now(timezone.utc)
        with sessions.begin() as db:
            assert list(db.scalars(text("SELECT version_num FROM alembic_version"))) == [CURRENT_SCHEMA_REVISION]
            seed(db)
            db.add(ProjectMember(project_id=4, user_id=2, role="owner"))
            db.add(AuthorityState(organization_id=1, project_id=4, principal_kind="user", principal_id="2",
                scope=PILOT_SCOPE, membership_role="owner", permissions=["metadata", "fragment", "write", "observe", "audit"],
                state="active", authority_epoch=1, record_version=1, valid_until=now + timedelta(hours=2), updated_at=now))
        policy = replace(source_policy(), grants=frozenset(), authority=AuthorityResolver(), valid_until=now + timedelta(hours=2))
        authority = LifecycleAuthority(policy=policy, allowed_residencies=frozenset({"local-test"}),
            allowed_keks=frozenset({KekRef("local-upload", "v1")}), max_retention=timedelta(hours=1),
            derive_allowed=True, retention_owner=True)
        storage = FilesystemStagingStorage(tmp_path / "ciphertext", Keys(), chunk_size=16)
        backend = A05LocalUploadLifecycle(storage=storage, authority_factory=lambda db, scope: authority,
            clock=lambda: datetime.now(timezone.utc), residency="local-test", kek=KekRef("local-upload", "v1"), max_file_bytes=1024)
        backend.retention_authority = LocalUploadRetentionAuthority(service_principal="v7-retention",
            scopes=frozenset({(1, 4)}), allowed_residencies=frozenset({"local-test"}),
            allowed_keks=frozenset({KekRef("local-upload", "v1")}))
        runtime = LocalUploadRuntime(storage=storage, lifecycle=LocalUploadLifecycleAdapter(backend),
            processor=Processor(), session_factory=sessions, kek=KekRef("local-upload", "v1"), max_file_bytes=1024,
            allowed_mime_types=frozenset({"text/plain"}), retention=timedelta(minutes=30))
        with sessions() as db:
            db.begin()
            queued = stage_and_enqueue(db, runtime=runtime, scope=UploadScope(2, 4),
                candidate=UploadCandidate("retention.txt", "text/plain", b"synthetic confidential body"),
                request_key="v7-pg-retention", index=0)
        with sessions.begin() as db:
            db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(status="failed"))
        yield engine, sessions, backend, str(UUID(hex=queued.staging_id))
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            assert re.fullmatch(r"v7_retention_test_[0-9a-f]{32}", schema)
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_pg_original_retention_waits_on_project_without_locking_materialization(pg_original):
    engine, sessions, backend, identity = pg_original
    started = Event()
    pids = []
    def contender():
        with sessions() as db:
            pids.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            row, _, _ = backend._retention_binding(db, identity)
            db.commit()
            return row.id
    with sessions() as reader, ThreadPoolExecutor(max_workers=1) as pool:
        reader.scalar(select(Project).where(Project.id == 4).with_for_update())
        future = pool.submit(contender)
        try:
            assert started.wait(3)
            deadline = monotonic() + 3
            blocked = False
            with engine.connect() as observer:
                while monotonic() < deadline:
                    if observer.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pids[0]}):
                        blocked = True
                        break
                    sleep(.02)
            assert blocked, "retention did not contend on the actual project lock"
            reader.execute(text("SET LOCAL lock_timeout='1s'"))
            # Before the fix the waiter held this row and waited for our Project:
            # this acquisition deadlocked/timed out. It now succeeds immediately.
            assert reader.scalar(select(Materialization.id).where(Materialization.id == identity).with_for_update()) == identity
        finally:
            reader.rollback()
        assert future.result(timeout=8) == identity
