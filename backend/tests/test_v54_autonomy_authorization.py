"""Synthetic A07 schema/runtime acceptance; no provider or production wiring."""
from datetime import timedelta
from io import StringIO
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

from app.action_trust.guards import TrustConflict, reference, revision
from app.autonomy_policy import AutonomyPolicyService, PolicyAssignmentCommand, PolicyRevokeCommand
from app.core.v54_authority import AuthorityResolver, PILOT_SCOPE
from app.core.v54_dto import ActionEnvelope, canonical_hash
from app.core.v54_interfaces import DispatchBinding
from app.models.job import BackgroundJob
from app.models.project_member import ProjectMember
from app.models.task import Task
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import ActionApproval, ActionReceipt, PendingDispatch, PilotAction
from app.pilot_task_mutation import InternalTaskMutation
from test_v54_action_trust_support import h  # noqa: F401 - shared synthetic fixture
from v54_pilot_fixture import NOW, uid


BACKEND = Path(__file__).resolve().parents[1]
HEAD = "a54f001c0a11"
OWNER_PERMISSIONS = ["action.freeze", "autonomy.policy.manage"]


def migration_config(output=None):
    config = Config(str(BACKEND / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def test_a07_is_single_head_and_offline_sql_has_exclusive_origins(monkeypatch):
    assert ScriptDirectory.from_config(migration_config()).get_heads() == [HEAD]
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline")
    output = StringIO()
    command.upgrade(migration_config(output), "a54f001c0a06:a54f001c0a07", sql=True)
    sql = output.getvalue()
    for value in (
        "HUMAN_APPROVAL", "SERVER_POLICY", "authorization_origin", "decision_hash",
        "action_hash", "payload_hash", "authority_epoch", "policy_hash",
        "fk_v54_pending_dispatch_server_policy", "fk_v54_receipts_server_policy",
        "fk_v54_pending_dispatch_sealed_action", "fk_v54_receipts_sealed_action",
        "ix_v54_pending_dispatch_authorization", "ix_v54_receipts_authorization",
    ):
        assert value in sql
    assert "INSERT INTO v54_approvals" not in sql


def _postgres_url():
    value = os.getenv("PUW_V54_PROVIDER_MIGRATION_DATABASE_URL")
    if not value:
        pytest.skip("CONDITIONAL: A07 migration PostgreSQL URL is not configured")
    parsed = make_url(value)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost", "127.0.0.1", "::1", "db", "postgres"}
    assert (parsed.database or "").startswith("puw_v54_test_") and not parsed.query
    return value


def test_postgresql_a07_round_trip_and_constraints(monkeypatch):
    url = _postgres_url()
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    config = migration_config()
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        for table in ("v54_pending_dispatch", "v54_receipts"):
            columns = {item["name"]: item for item in inspect(engine).get_columns(table)}
            assert columns["approval_id"]["nullable"] is True
            assert columns["authorization_origin"]["nullable"] is False
            checks = {item["name"] for item in inspect(engine).get_check_constraints(table)}
            fks = {item["name"] for item in inspect(engine).get_foreign_keys(table)}
            assert any(name.endswith("authorization_exclusive") for name in checks)
            assert any(name.endswith("server_policy") for name in fks)
            assert any(name.endswith("sealed_action") for name in fks)
        command.downgrade(config, "a54f001c0a06")
        assert "authorization_origin" not in {
            item["name"] for item in inspect(engine).get_columns("v54_pending_dispatch")
        }
        command.upgrade(config, HEAD)
    finally:
        engine.dispose()


def _install_auto(h):
    authority = AuthorityResolver(clock=lambda: NOW)
    service = AutonomyPolicyService(authority=authority, clock=lambda: NOW)
    h.db.add(ProjectMember(project_id=4, user_id=2, role="owner"))
    h.db.add(AuthorityState(
        organization_id=1, project_id=4, principal_kind="user", principal_id="2",
        scope=PILOT_SCOPE, membership_role="owner", permissions=OWNER_PERMISSIONS,
        state="active", authority_epoch=7, record_version=1,
        valid_until=NOW + timedelta(minutes=30), updated_at=NOW, updated_by_user_id=2,
    ))
    h.db.flush()
    view = service.assign(h.db, scope=h.requester, command=PolicyAssignmentCommand(
        expected_policy_id=None, expected_revision=0, expected_policy_hash=None,
        expected_authority_epoch=7, create_internal_task="AUTO",
        send_external_message="CONFIRM", valid_until=NOW + timedelta(minutes=20),
    ))
    h.trust.autonomy = service
    raw = h.envelope().model_dump(mode="json")
    raw.update(
        action_ref={**raw["action_ref"], "id": {"kind": "uuid", "value": uid(170)}},
        autonomy="AUTO", policy=view.policy.model_dump(mode="json"),
        policy_sha256=view.policy_sha256, idempotency_key="synthetic-auto-create-1",
    )
    envelope = ActionEnvelope.model_validate(raw)
    pin = h.trust.freeze(h.db, scope=h.requester, envelope=envelope)
    action = h.db.get(PilotAction, pin.ref.id.value)
    h.trust.request_dispatch(
        h.db, scope=h.requester, action=pin, approval=None,
        expected_record_version=action.record_version,
    )
    pending = h.db.get(PendingDispatch, pin.ref.id.value)
    assert pending.authorization_origin == "SERVER_POLICY" and pending.approval_id is None
    assert h.db.scalar(select(func.count()).select_from(ActionApproval).where(
        ActionApproval.action_id == pin.ref.id.value)) == 0
    return service, view, envelope, pin


def _binding(h, envelope, pin):
    pending = h.db.get(PendingDispatch, pin.ref.id.value)
    job = BackgroundJob(
        kind="v54.synthetic_task", status="running", payload={}, worker_id="synthetic-auto-worker",
        attempts=1, locked_at=NOW, lease_expires_at=NOW + timedelta(minutes=3),
        idempotency_key=envelope.idempotency_key,
    )
    h.db.add(job)
    h.db.flush()
    pending.job_id = job.id
    h.db.flush()
    return DispatchBinding(
        action=pin, **h.trust._pending_authorization(pending, h.requester),
        envelope_hash=pending.envelope_hash, command_key=envelope.idempotency_key,
        job=reference(h.requester, "background_job", job.id), worker_id=job.worker_id,
        job_attempt=job.attempts, locked_at=NOW,
    )


def test_scenario_b_auto_creates_one_internal_task_without_human_approval(h):
    with h.db.begin():
        h.claim()
        service, _, envelope, pin = _install_auto(h)
        original = h.db.get(PendingDispatch, pin.ref.id.value).decision_hash
        service.clock = lambda: NOW + timedelta(seconds=1)
        service.authority.clock = service.clock
        h.trust.request_dispatch(
            h.db, scope=h.requester, action=pin, approval=None,
            expected_record_version=h.db.get(PilotAction, pin.ref.id.value).record_version,
        )
        assert h.db.get(PendingDispatch, pin.ref.id.value).decision_hash == original
    with h.db.begin():
        binding = _binding(h, envelope, pin)
    with h.db.begin():
        mutation = InternalTaskMutation(guards=h.guards, trust=h.trust)
        receipt_ref = h.trust.execute(h.db, scope=h.requester, binding=binding, mutation=mutation)
        receipt = h.db.get(ActionReceipt, receipt_ref.id.value)
        assert receipt.authorization_origin == "SERVER_POLICY" and receipt.approval_id is None
        assert receipt.policy_hash == binding.policy_sha256
        assert h.db.scalar(select(func.count()).select_from(Task)) == 1
        assert h.db.scalar(select(func.count()).select_from(ActionApproval).where(
            ActionApproval.action_id == pin.ref.id.value)) == 0


@pytest.mark.parametrize("race", ["payload", "epoch", "revoke", "expiry"])
def test_auto_t2_blocks_altered_binding_authority_revoke_and_expiry(h, race):
    with h.db.begin():
        h.claim()
        service, view, envelope, pin = _install_auto(h)
    with h.db.begin():
        binding = _binding(h, envelope, pin)
    if race == "payload":
        binding = binding.model_copy(update={"payload_hash": "f" * 64})
    elif race == "epoch":
        with h.db.begin():
            state = h.db.scalar(select(AuthorityState).where(AuthorityState.principal_id == "2"))
            state.authority_epoch += 1
            state.record_version += 1
            state.updated_at = NOW + timedelta(seconds=1)
    elif race == "revoke":
        with h.db.begin():
            service.revoke(h.db, scope=h.requester, command=PolicyRevokeCommand(
                expected_policy_id=view.policy.ref.id.value, expected_revision=view.policy.value,
                expected_policy_hash=view.policy_sha256, expected_authority_epoch=7,
            ))
    else:
        h.access.time = NOW + timedelta(minutes=21)
        service.clock = lambda: NOW + timedelta(minutes=21)
        service.authority.clock = service.clock
    with pytest.raises(TrustConflict):
        with h.db.begin():
            h.trust.execute(
                h.db, scope=h.requester, binding=binding,
                mutation=InternalTaskMutation(guards=h.guards, trust=h.trust),
            )
    with h.db.begin():
        assert h.db.scalar(select(func.count()).select_from(Task)) == 0
        assert h.db.scalar(select(func.count()).select_from(ActionReceipt)) == 0
