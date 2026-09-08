"""Actual process kill around the real meeting transaction; owned PostgreSQL only.

Mandatory CI nodes: test_pg_meeting_commit_fault[<case>] for every CASES value.
Uses existing Obligation.task_id/history as the business receipt, not ActionReceipt.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import multiprocessing as mp
import os
from pathlib import Path
import re
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.orm import sessionmaker

from app.core.v54_authority import AuthorityResolver, PILOT_SCOPE
from app.local_upload_staging import (LocalUploadBusinessProcessor, LocalUploadLifecycleAdapter,
    LocalUploadRuntime, UploadScope, configure_local_upload_runtime)
from app.models.management import Meeting, Obligation, ObligationHistory
from app.models.management_digest import ManagementProposalOrigin
from app.models.materialization import Materialization
from app.models.project_member import ProjectMember
from app.models.task import Task, TaskHistory
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import EvidenceAssessment
from app.mvp3.lifecycle import ManagementDenied
from app.mvp3.meeting_digest import MeetingProposalService
from app.mvp3.meeting_source_binding import MeetingSourceBindingService
from app.schema import CURRENT_SCHEMA_REVISION
from app.staging.contracts import KekRef
from app.staging.filesystem import FilesystemStagingStorage
from app.staging.lifecycle import LifecycleAuthority
from app.staging.local_upload import A05LocalUploadLifecycle, LocalUploadRetentionAuthority
from test_mvp1_xlsx_cell_evidence import MIME
from test_mvp1_xlsx_durable_evidence import stage_xlsx, run_xlsx, cells
from test_mvp3_meeting_binding_postgres import _owned_url
from test_mvp3_meeting_source_binding import _bind, _propose
from test_v54_local_upload_a05_wiring import Keys
from test_v54_source_evidence_pilot import policy
from v54_pilot_fixture import seed

CASES = ("before_commit", "after_commit", "revoked_authority", "stale_binding", "revoked_child")
SCHEMA = re.compile(r"meeting_fault_test_[0-9a-f]{32}")


def _counts(db, entity_id):
    row = db.get(Obligation, entity_id, populate_existing=True)
    return dict(tasks=db.scalar(select(func.count(Task.id))),
        task_history=db.scalar(select(func.count(TaskHistory.id))),
        confirmations=db.scalar(select(func.count(ObligationHistory.id)).where(
            ObligationHistory.obligation_id == entity_id, ObligationHistory.event == "transitioned")),
        obligation_history=db.scalar(select(func.count(ObligationHistory.id)).where(
            ObligationHistory.obligation_id == entity_id)),
        task_id=row.task_id, version=row.record_version)


def _checkpoint(value):
    """Allowlist child IPC. No exception text, payload, paths or source content."""
    if not isinstance(value, dict) or value.get("state") not in {"before_commit", "after_commit", "denied", "failed"}:
        raise AssertionError("unsafe_meeting_checkpoint")
    state = value["state"]
    if state in {"denied", "failed"}:
        if set(value) != {"state"}:
            raise AssertionError("unsafe_meeting_checkpoint")
    else:
        if set(value) != {"state", "task_id", "entity_id", "version"} or any(
            type(value[key]) is not int or value[key] <= 0 for key in ("task_id", "entity_id", "version")):
            raise AssertionError("unsafe_meeting_checkpoint")
    return dict(value)


def _confirm(db, entity_id):
    return MeetingProposalService().confirm(db, project_id=4, actor_user_id=2,
        entity_type="obligation", entity_id=entity_id, expected_version=1, create_internal_task=True)


def _child_confirm(url_value, schema, entity_id, boundary, output, stop):
    """No runtime fixture/clock/service double crosses the process boundary."""
    engine = None
    try:
        url = _owned_url(url_value)
        if SCHEMA.fullmatch(schema) is None or boundary not in {"before_commit", "after_commit", "replay"}:
            raise ValueError("owned_meeting_process_required")
        engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5,
            "options": f"-csearch_path={schema} -clock_timeout=8000 -cstatement_timeout=15000"})
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions() as db:
            result = _confirm(db, entity_id)
            db.flush()
            receipt = dict(task_id=result["task_id"], entity_id=result["entity_id"], version=result["record_version"])
            if boundary == "before_commit":
                output.send(_checkpoint(dict(state="before_commit", **receipt)))
                # IPC synchronization, not a timing guess. No application ACK.
                if not stop.wait(60):
                    raise TimeoutError("parent_boundary_not_received")
                return  # Uncommitted session rollback if parent did not kill.
            db.commit()
            output.send(_checkpoint(dict(state="after_commit", **receipt)))
            if boundary == "after_commit":
                if not stop.wait(60):
                    raise TimeoutError("parent_boundary_not_received")
    except ManagementDenied:
        output.send({"state": "denied"})
    except Exception:
        output.send({"state": "failed"})
    finally:
        if engine is not None:
            engine.dispose()
        output.close()


def _stop_owned(children):
    for child in children:
        if child.pid is None:
            continue
        if child.is_alive():
            child.kill()
        child.join(10)
        if child.is_alive():
            raise AssertionError("meeting_child_cleanup_failed")


def _start(ctx, children, pipes, url, schema, entity_id, boundary):
    receive, send = ctx.Pipe(duplex=False)
    stop = ctx.Event()
    process = ctx.Process(target=_child_confirm, args=(url, schema, entity_id, boundary, send, stop))
    children.append(process)
    pipes.extend((receive, send))
    process.start()
    send.close()
    assert receive.poll(40), "meeting_child_checkpoint_timeout"
    return process, _checkpoint(receive.recv())


@pytest.fixture
def pg_retained_meeting(tmp_path, monkeypatch):
    value = os.getenv("PUW_MVP3_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: owned MVP3 PostgreSQL not configured")
    url = _owned_url(value)
    schema = "meeting_fault_test_" + uuid4().hex
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5,
        "options": "-clock_timeout=8000 -cstatement_timeout=15000"})
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
        def authority_factory(db, scope):
            assert scope == UploadScope(2, 4)
            return authority
        lifecycle = A05LocalUploadLifecycle(storage=storage, authority_factory=authority_factory,
            clock=lambda: datetime.now(timezone.utc), residency="local-test", kek=KekRef("local-upload", "v1"),
            max_file_bytes=1024 * 1024, retention_authority=LocalUploadRetentionAuthority(
                service_principal="meeting-fault-retention", scopes=frozenset({(1, 4)}),
                allowed_residencies=frozenset({"local-test"}), allowed_keks=frozenset({KekRef("local-upload", "v1")})))
        runtime = LocalUploadRuntime(storage=storage, lifecycle=LocalUploadLifecycleAdapter(lifecycle),
            processor=LocalUploadBusinessProcessor(), session_factory=sessions, kek=KekRef("local-upload", "v1"),
            max_file_bytes=1024 * 1024, allowed_mime_types=frozenset({MIME}), retention=timedelta(minutes=30))
        configure_local_upload_runtime(runtime)
        world = (sessions, runtime, lifecycle, tmp_path)
        queued, _ = stage_xlsx(world)
        run_xlsx(world, queued)
        with sessions.begin() as db:
            original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
            assert original.state == "PURGED"
            retained = cells(db)
            assert len(retained) == 3
            proof = retained[0]
            meeting = Meeting(project_id=4, created_by_user_id=2, title="Synthetic fault protocol",
                              minutes="Synthetic recorded protocol", status="completed")
            db.add(meeting); db.flush()
            ids = dict(meeting_id=meeting.id, source_id=original.source_id,
                       source_version_id=original.source_version_id, evidence_id=proof.id,
                       child_id=proof.representation_ref["representation_id"])
            binding = _bind(db, ids)
            proposal = _propose(db, ids, binding)
            ids.update(entity_id=proposal["entity_id"], binding_id=binding["binding_id"])
        yield sessions, ids, url.render_as_string(hide_password=False), schema
    finally:
        configure_local_upload_runtime(None)
        if engine is not None:
            engine.dispose()
        if created:
            assert SCHEMA.fullmatch(schema)
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize("scenario", CASES)
def test_pg_meeting_commit_fault(pg_retained_meeting, scenario):
    sessions, ids, url, schema = pg_retained_meeting
    ctx = mp.get_context("spawn")
    children, pipes = [], []
    boundary = "after_commit" if scenario == "after_commit" else "before_commit"
    try:
        child, checkpoint = _start(ctx, children, pipes, url, schema, ids["entity_id"], boundary)
        assert checkpoint["state"] == boundary
        with sessions() as observer:
            before = _counts(observer, ids["entity_id"])
            assert before["tasks"] == before["task_history"] == before["confirmations"] == (1 if boundary == "after_commit" else 0)
            assert before["obligation_history"] == (2 if boundary == "after_commit" else 1)
        _stop_owned([child])
        assert child.exitcode not in (None, 0), "fault_child_did_not_die_at_boundary"
        with sessions() as observer:
            assert _counts(observer, ids["entity_id"]) == before
        if scenario in {"revoked_authority", "stale_binding", "revoked_child"}:
            with sessions.begin() as db:
                if scenario == "revoked_authority":
                    db.execute(update(AuthorityState).where(AuthorityState.project_id == 4).values(state="revoked",
                               authority_epoch=AuthorityState.authority_epoch + 1))
                elif scenario == "stale_binding":
                    MeetingSourceBindingService().edit(db, project_id=4, meeting_id=ids["meeting_id"], actor_user_id=2,
                        expected_version=2, minutes="Synthetic superseding protocol", status="completed")
                else:
                    db.execute(update(EvidenceAssessment).where(EvidenceAssessment.evidence_id == ids["evidence_id"])
                               .values(availability="unavailable"))
            replay, result = _start(ctx, children, pipes, url, schema, ids["entity_id"], "replay")
            assert result == {"state": "denied"}
            replay.join(10)
            assert replay.exitcode == 0
            with sessions() as db:
                assert _counts(db, ids["entity_id"]) == before
        else:
            replay, result = _start(ctx, children, pipes, url, schema, ids["entity_id"], "replay")
            assert result["state"] == "after_commit"
            replay.join(10)
            assert replay.exitcode == 0
            if scenario == "after_commit":
                assert result == checkpoint
            with sessions.begin() as db:
                receipt = _confirm(db, ids["entity_id"])
                assert receipt["task_id"] == result["task_id"] and receipt["record_version"] == result["version"]
                final = _counts(db, ids["entity_id"])
                assert final["tasks"] == final["task_history"] == final["confirmations"] == 1
                assert final["obligation_history"] == 2
                origin = db.scalar(select(ManagementProposalOrigin).where(ManagementProposalOrigin.entity_id == ids["entity_id"]))
                assert origin.meeting_source_binding_id == ids["binding_id"]
                assert origin.evidence_pins[0]["ref"]["id"]["value"] == ids["evidence_id"]
    finally:
        _stop_owned(children)
        for pipe in pipes:
            pipe.close()


@pytest.mark.parametrize("value", [None, {}, {"state": "failed", "message": "synthetic-secret"},
    {"state": "before_commit", "task_id": True, "entity_id": 1, "version": 3},
    {"state": "after_commit", "task_id": 1, "entity_id": 1, "version": -1}])
def test_meeting_fault_checkpoint_rejects_content_or_invalid_ids(value):
    with pytest.raises(AssertionError, match="^unsafe_meeting_checkpoint$"):
        _checkpoint(value)


def test_meeting_fault_checkpoint_is_content_free():
    assert _checkpoint({"state": "before_commit", "task_id": 1, "entity_id": 2, "version": 3}) == {
        "state": "before_commit", "task_id": 1, "entity_id": 2, "version": 3}
    assert _checkpoint({"state": "denied"}) == {"state": "denied"}


def test_meeting_fault_cleanup_only_kills_registered_started_children():
    calls = []
    class Child:
        def __init__(self, pid):
            self.pid, self.alive = pid, pid is not None
        def is_alive(self):
            return self.alive
        def kill(self):
            calls.append(("kill", self.pid)); self.alive = False
        def join(self, timeout):
            calls.append(("join", self.pid, timeout))
    _stop_owned([Child(None), Child(123)])
    assert calls == [("kill", 123), ("join", 123, 10)]


@pytest.mark.parametrize("url", ["postgresql://u:p@example.com/puw_mvp3_test_x",
    "postgresql://u:p@localhost/production", "sqlite:///test.db",
    "postgresql://u:p@localhost/puw_mvp3_test_x?options=-csearch_path=public"])
def test_fault_child_refuses_unowned_database_without_connecting(monkeypatch, url):
    messages = []
    class Output:
        def send(self, value):
            messages.append(_checkpoint(value))
        def close(self):
            pass
    monkeypatch.setattr("test_v7_meeting_commit_fault_postgres.create_engine",
                        lambda *a, **k: pytest.fail("unowned connection attempted"))
    _child_confirm(url, "meeting_fault_test_" + "0" * 32, 1, "replay", Output(), None)
    assert messages == [{"state": "failed"}]


def test_five_fault_nodes_are_explicit_and_use_spawn_and_real_confirm():
    import inspect
    assert CASES == ("before_commit", "after_commit", "revoked_authority", "stale_binding", "revoked_child")
    assert 'get_context("spawn")' in inspect.getsource(test_pg_meeting_commit_fault)
    assert "MeetingProposalService().confirm" in inspect.getsource(_confirm)
    child = inspect.getsource(_child_confirm)
    assert child.index('state="before_commit"') < child.index("db.commit()")
    assert child.index("db.commit()") < child.index('state="after_commit"')


def test_actual_spawn_ipc_rejects_non_postgres_without_runtime_claim():
    ctx = mp.get_context("spawn")
    children, pipes = [], []
    try:
        child, result = _start(ctx, children, pipes, "sqlite:///must-not-open.db",
                               "meeting_fault_test_" + "0" * 32, 1, "replay")
        child.join(10)
        assert result == {"state": "failed"} and child.exitcode == 0
    finally:
        _stop_owned(children)
        for pipe in pipes:
            pipe.close()
