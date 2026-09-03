"""Executable foundation contracts. SQLite != PostgreSQL concurrency proof."""
from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models
from app.database import Base
from app.core.v54_dto import ActionEnvelope, DeadlineClaimInput, canonical_hash, canonical_json, parse_envelope_json
from app.core.v54_interfaces import AuditAppend, PilotGate, Resolution, require_resolution
from app.core.v54_refs import ObjectRef, TaggedId, VersionPin
from app.core.v54_transactions import append_audit
from app.models.audit_log import AuditLog
from app.models.v54_pilot import (
    ConnectionIdentity, MailConnection, SourceReference, SourceVersion, Evidence, EvidenceAssessment,
    DeadlineClaim, ContextRelation, PilotAction, ActionRevision, ActionApproval, ActionReceipt,
    PendingDispatch, AuditExtension,
)
from app.schema import CURRENT_SCHEMA_REVISION
from v54_pilot_fixture import NOW, DOC_FIXTURE, envelopes, pin, ref, scope, seed, uid

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize("kind,value", [
    ("int", "0"), ("int", "-1"), ("int", "+1"), ("int", "01"),
    ("int", "1.0"), ("int", "9223372036854775808"), ("int", True), ("int", 1),
    ("uuid", "00000000000040008000000000000001"), ("uuid", "ABCDEFAB-0000-4000-8000-000000000001"),
    ("uuid", 1), ("int", None), ("other", "1"),
])
def test_tagged_id_rejects_ambiguous_identity(kind, value):
    with pytest.raises(ValidationError):
        TaggedId(kind=kind, value=value)


def test_reference_registry_and_tenant_checks():
    assert ObjectRef.model_validate(ref("project", 4)).id.kind == "int"
    for value in [
        ref("project", uid(4)), ref("organization", 2), ref("unregistered", uid(1)),
        {**ref("project", 4), "admin": True}, {**ref("project", 4), "namespace": "google"},
    ]:
        with pytest.raises(ValidationError):
            ObjectRef.model_validate(value)


@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1", 2**53])
def test_pin_positive_strict_version(value):
    with pytest.raises(ValidationError):
        VersionPin.model_validate(pin("source_version", uid(14), value))


def test_version_semantics_not_provider_revision():
    for value in [pin("source_version", uid(14), 2), pin("evidence", uid(16), version_kind="record_version"),
                  pin("project", 4), pin("source", uid(12))]:
        with pytest.raises(ValidationError):
            VersionPin.model_validate(value)


def test_all_integrated_fixture_refs_and_pins_parse_without_replacement():
    d = json.loads(DOC_FIXTURE.read_text(encoding="utf8"))
    def walk(value):
        if isinstance(value, dict):
            if set(value) == {"namespace", "type", "tenant_id", "id"}:
                ObjectRef.model_validate(value)
            elif set(value) == {"ref", "version_kind", "value"}:
                VersionPin.model_validate(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(d)


@pytest.mark.parametrize("envelope", envelopes())
def test_confirm_envelope_keeps_canonical_bytes(envelope):
    parsed = ActionEnvelope.model_validate(envelope)
    assert parsed.model_dump(mode="json") == envelope
    assert canonical_hash(envelope) == canonical_hash(parsed.model_dump(mode="json"))


@pytest.mark.parametrize("mutation", ["auto", "external", "float", "cross_tenant", "unknown", "duplicate_pin", "wrong_target", "bool_version"])
def test_envelope_rejects_unsafe_or_ambiguous_mutations(mutation):
    value = envelopes()[0]
    if mutation == "auto":
        value["autonomy"] = "AUTO"
    elif mutation == "external":
        value["payload"]["publish_external"] = True
    elif mutation == "float":
        value["revision"] = 1.0
    elif mutation == "cross_tenant":
        value["payload"]["assignee_ref"]["tenant_id"]["value"] = "2"
    elif mutation == "unknown":
        value["execute"] = True
    elif mutation == "duplicate_pin":
        value["evidence"] *= 2
    elif mutation == "wrong_target":
        value["target"]["ref"] = ref("task", 7)
    else:
        value["action_type_version"] = True
    with pytest.raises((ValueError, ValidationError)):
        ActionEnvelope.model_validate(value)


def test_duplicate_json_keys_noncanonical_numbers_and_unicode_rejected():
    with pytest.raises(ValueError):
        parse_envelope_json('{"revision":1,"revision":2}')
    for value in [float("nan"), 1.2, 2**53, {"nonascii-ю": 1}, "\ud800"]:
        with pytest.raises((ValueError, UnicodeError)):
            canonical_json(value)


def resolved(**changes):
    values = dict(pin=pin("evidence", uid(16)), actor=ref("user", 2), project=ref("project", 4),
                  operation="dispatch", acl="allow", version="current", freshness="fresh",
                  availability="available", verification="verified", policy_known=True,
                  retention_known=True, residency_allowed=True, valid_until=NOW + timedelta(seconds=30),
                  authority_epoch=1, binding_epoch=1)
    values.update(changes)
    return Resolution(**values)


@pytest.mark.parametrize("change", [
    {"acl":"unknown"}, {"acl":"deny"}, {"version":"unknown"}, {"version":"changed"},
    {"freshness":"unknown"}, {"freshness":"stale"}, {"availability":"unavailable"},
    {"verification":"unverified"}, {"policy_known":False}, {"retention_known":False},
    {"residency_allowed":False}, {"valid_until":None}, {"valid_until":NOW},
    {"authority_epoch":None}, {"binding_epoch":None}, {"actor":ref("user", 3)},
    {"project":ref("project", 9)}, {"operation":"metadata"}, {"pin":pin("evidence", uid(99))},
    {"pin":pin("evidence", uid(16), tenant=2)},
])
def test_resolver_fails_closed_for_unknown_expired_or_mismatched_context(change):
    with pytest.raises(ValueError, match="resource_unavailable"):
        require_resolution(resolved(**change), scope=scope(), pin=VersionPin.model_validate(pin("evidence", uid(16))),
                           operation="dispatch", now=NOW)


def test_resolution_accepts_explicit_facts_without_changing_approval():
    p = VersionPin.model_validate(pin("evidence", uid(16)))
    for time in [NOW, NOW + timedelta(seconds=10)]:
        require_resolution(resolved(), scope=scope(), pin=p, operation="dispatch", now=time)


@pytest.mark.parametrize("mode,action", [("AUTO","task.internal.create"), ("CONFIRM","message.external.send"),
                                        ("ASSIST","task.internal.create"), ("CONFIRM","finance.pay")])
def test_pilot_gate_cannot_enable_auto_or_external(mode, action):
    gate = PilotGate(synthetic_scope_authorized=True, roles_known=True, retention_known=True,
                     valid_until=NOW + timedelta(seconds=30))
    with pytest.raises(ValueError, match="pilot_disabled"):
        gate.require_confirm(mode=mode, action_type=action, now=NOW)


def test_no_enabled_policy_defaults():
    with pytest.raises(ValueError):
        PilotGate().require_confirm(mode="CONFIRM", action_type="task.internal.create", now=NOW)
    assert Resolution(pin=pin("evidence", uid(16)), actor=ref("user", 2), project=ref("project", 4),
                      operation="metadata").acl == "unknown"


def test_claim_extraction_cannot_set_review_or_omit_evidence():
    values = dict(anchor=ref("deadline_claim", uid(17)), revision=1, message=ref("message", 6),
                  due_date="2026-09-10", timezone="Europe/Moscow", evidence=[pin("evidence", uid(16))])
    DeadlineClaimInput(**values)
    for change in [{"verification":"confirmed"}, {"evidence":[]}, {"due_date":"next Friday"}]:
        with pytest.raises(ValueError):
            DeadlineClaimInput(**{**values, **change})


def test_synthetic_seed_preserves_existing_pk_and_pending_is_not_execution(db):
    seed(db)
    assert db.scalar(select(PilotAction.business_state)) == "AWAITING_POLICY"
    assert db.scalar(select(PendingDispatch.pending)) is True
    assert db.scalar(select(PendingDispatch.job_id)) is None
    assert db.scalar(select(func.count()).select_from(app.models.Task)) == 0
    assert db.get(app.models.Message, 6).context_version == 1
    assert db.get(app.models.Message, 6).analysis_required is False
    assert db.get(app.models.Task, 6) is None


@pytest.mark.parametrize("bad_source,bad_version,bad_tenant", [(12,15,1),(13,14,1),(13,15,2)])
def test_db_rejects_cross_source_version_or_tenant(db, bad_source, bad_version, bad_tenant):
    seed(db)
    db.add(Evidence(id=uid(50), organization_id=bad_tenant, source_id=uid(bad_source),
                    source_version_id=uid(bad_version), locator={"kind":"whole_object"},
                    extractor={"name":"fixture"}, extracted_at=NOW))
    with pytest.raises(IntegrityError):
        db.flush()


def test_db_rejects_project_of_another_tenant(db):
    seed(db)
    db.add(SourceReference(id=uid(50), organization_id=1, origin_project_id=9,
                           identity_id=uid(10), namespace="synthetic-mailbox", external_id="other",
                           external_id_kind="stable_id", object_kind="message", canonical_locator={}))
    with pytest.raises(IntegrityError):
        db.flush()


def test_identity_namespace_dedup_distinguishes_accounts(db):
    seed(db)
    db.add(ConnectionIdentity(id=uid(30), organization_id=1, provider="synthetic", account_key="account-b"))
    db.flush()
    db.add(SourceReference(id=uid(31), organization_id=1, origin_project_id=4, identity_id=uid(30),
                           namespace="synthetic-mailbox", external_id="synthetic-mail-1",
                           external_id_kind="stable_id", object_kind="message", canonical_locator={}))
    db.flush()
    assert db.scalar(select(func.count()).select_from(SourceReference)) == 3
    db.add(SourceReference(id=uid(32), organization_id=1, origin_project_id=4, identity_id=uid(30),
                           namespace="synthetic-mailbox", external_id="synthetic-mail-1",
                           external_id_kind="stable_id", object_kind="message", canonical_locator={}))
    with pytest.raises(IntegrityError):
        db.flush()


@pytest.mark.parametrize("model,key,field,value", [
    (SourceVersion,uid(14),"provider_revision","rewritten"),
    (Evidence,uid(16),"source_version_id",uid(14)),
    (DeadlineClaim,(uid(17),1),"timezone","UTC"),
    (ActionRevision,(uid(20),1),"envelope_hash","0"*64),
    (ConnectionIdentity,uid(10),"account_key","different-account"),
])
def test_assertions_cannot_be_rewritten_via_orm(db, model, key, field, value):
    seed(db)
    row = db.get(model,key)
    setattr(row,field,value)
    with pytest.raises(ValueError, match="immutable_pilot_assertion"):
        db.flush()


def test_assessment_recheck_does_not_modify_evidence_or_seal(db):
    seed(db)
    old_hash = db.get(ActionRevision,(uid(20),1)).envelope_hash
    assessment = db.get(EvidenceAssessment, uid(16))
    assessment.record_version += 1
    assessment.checked_at = NOW + timedelta(seconds=10)
    db.flush()
    assert db.get(ActionRevision,(uid(20),1)).envelope_hash == old_hash
    assert db.get(Evidence,uid(16)).revision == 1


def test_duplicate_business_intent_cannot_use_another_action_id(db):
    seed(db)
    db.add(PilotAction(id=uid(50),organization_id=1,project_id=4,message_id=6,
                       claim_id=uid(17),action_type="task.internal.create"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_approval_cannot_bind_different_hash(db):
    seed(db)
    db.add(ActionApproval(id=uid(50),organization_id=1,action_id=uid(20),revision=1,
                          envelope_hash="0"*64,command_key="different-grant",approver_id=3,
                          authority_epoch=1,state="GRANTED",granted_at=NOW,expires_at=NOW+timedelta(minutes=1)))
    with pytest.raises(IntegrityError):
        db.flush()


def test_command_key_cannot_be_rebound_to_new_revision(db):
    seed(db)
    envelope = envelopes()[0]
    envelope["revision"] = 2
    db.add(ActionRevision(action_id=uid(20),revision=2,organization_id=1,claim_id=uid(17),claim_revision=1,
                          policy_id=uid(22),policy_revision=1,envelope=envelope,envelope_hash=canonical_hash(envelope),
                          command_key=envelope["idempotency_key"],requested_by=2,created_at=NOW))
    with pytest.raises(IntegrityError):
        db.flush()


def test_receipt_is_unique_per_business_action_not_per_job(db):
    seed(db)
    db.add_all([app.models.BackgroundJob(id=1,kind="synthetic",payload={}),
                app.models.BackgroundJob(id=2,kind="synthetic",payload={})])
    db.flush()
    def receipt(n, job):
        return ActionReceipt(id=uid(n),organization_id=1,action_id=uid(20),revision=1,
                             envelope_hash=canonical_hash(envelopes()[0]),approval_id=uid(23),
                             job_id=job,fence=1,outcome="NOT_APPLIED",target_ref=None,recorded_at=NOW)
    db.add(receipt(25,1))
    db.flush()
    db.add(receipt(26,2))
    with pytest.raises(IntegrityError):
        db.flush()


def test_pending_dispatch_cannot_bind_an_unapproved_seal(db):
    seed(db)
    pending = db.get(PendingDispatch,uid(20))
    pending.envelope_hash = "0" * 64
    with pytest.raises(IntegrityError):
        db.flush()


def test_no_new_revision_after_business_dispatch(db):
    seed(db)
    action = db.get(PilotAction,uid(20))
    action.business_state = "UNKNOWN"
    db.flush()
    envelope = envelopes()[0]
    envelope["revision"] = 2
    envelope["idempotency_key"] = "synthetic-new-key"
    db.add(ActionRevision(action_id=uid(20),revision=2,organization_id=1,claim_id=uid(17),claim_revision=1,
                          policy_id=uid(22),policy_revision=1,envelope=envelope,envelope_hash=canonical_hash(envelope),
                          command_key=envelope["idempotency_key"],requested_by=2,created_at=NOW))
    with pytest.raises(ValueError,match="seal_binding_mismatch"):
        db.flush()


def test_ordinary_delete_does_not_erase_evidence(db):
    seed(db)
    db.delete(db.get(Evidence,uid(16)))
    with pytest.raises(ValueError,match="pilot_retention_writer_required"):
        db.flush()


def test_context_target_cross_tenant_is_rejected_before_insert(db):
    seed(db)
    row = relation(18)
    row.target_ref = ref("project",9,tenant=2)
    row.expected_target = pin("project",9,tenant=2,version_kind="record_version")
    db.add(row)
    with pytest.raises(ValueError,match="resource_unavailable"):
        db.flush()


def test_message_cannot_silently_switch_origin_to_an_attachment(db):
    seed(db)
    message = db.get(app.models.Message,6)
    message.source_reference_id = uid(13)
    with pytest.raises(ValueError,match="message_origin_scope_mismatch"):
        db.flush()


def relation(n):
    return ContextRelation(id=uid(n),organization_id=1,lineage_id=uid(n),revision=1,message_id=6,
                           relation_type="communication.project",target_ref=ref("project",4),
                           scope_ref=ref("mail_connection",uid(11)),expected_target=pin("project",4,version_kind="record_version"),
                           expected_context_version=1,evidence_pins=[pin("evidence",uid(16))],
                           provenance={"kind":"synthetic"},state="confirmed",applicability="current",
                           confirmed_by=3,confirmed_at=NOW)


def test_one_confirmed_primary_context_per_message(db):
    seed(db)
    db.add(relation(18))
    db.flush()
    db.add(relation(50))
    with pytest.raises(IntegrityError):
        db.flush()


def test_audit_is_one_existing_log_and_rollback_is_owned_by_caller(db):
    seed(db)
    db.commit()  # fixture commit, NOT mutation-helper commit
    with pytest.raises(RuntimeError, match="simulate"):
        with db.begin():
            append_audit(db,scope=scope(),event=AuditAppend(subject=ref("message",6),sequence=1,event="CONTEXT_CONFIRMED"),
                         authorize=lambda session, request, subject: True)
            assert db.scalar(select(func.count()).select_from(AuditLog)) == 1
            raise RuntimeError("simulate T2 failure")
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0
    assert db.scalar(select(func.count()).select_from(AuditExtension)) == 0


@pytest.mark.parametrize("permission", [False,None,"allow",1])
def test_audit_has_no_default_or_truthy_authorization(db, permission):
    seed(db)
    with pytest.raises(ValueError):
        append_audit(db,scope=scope(),event=AuditAppend(subject=ref("message",6),sequence=1,event="CONTEXT_CONFIRMED"),
                     authorize=lambda *_: permission)
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def migration_config(buffer=None):
    cfg = Config(str(BACKEND/"alembic.ini"), output_buffer=buffer)
    cfg.set_main_option("script_location",str(BACKEND/"migrations"))
    return cfg


def run_downgrade_on_connection(connection):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = BACKEND / "migrations/versions/a54f001c0a01_v54_pilot_foundation.py"
    spec = importlib.util.spec_from_file_location("pilot_migration",path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.downgrade()


def test_downgrade_refuses_to_erase_pilot_history(db):
    seed(db)
    with pytest.raises(RuntimeError,match="Pilot data exists"):
        run_downgrade_on_connection(db.connection())
    assert db.scalar(select(func.count()).select_from(Evidence)) == 1


def test_single_head_and_postgresql_offline_migration(monkeypatch):
    heads = ScriptDirectory.from_config(migration_config()).get_heads()
    assert heads == [CURRENT_SCHEMA_REVISION] == ["a54f001c0a01"]
    # Explicit synthetic URL, offline only: never inherit DATABASE_URL.
    monkeypatch.setenv("DATABASE_URL","postgresql+psycopg://synthetic:synthetic@127.0.0.1/puw_v54_test_offline")
    buf = StringIO()
    command.upgrade(migration_config(buf),"f360a1b2c3d4:head",sql=True)
    sql = buf.getvalue()
    assert "CREATE TABLE v54_sources" in sql and "fk_v54_evidence_observation" in sql
    assert "UPDATE alembic_version" in sql
    assert "DROP TABLE" not in sql and "INSERT INTO v54" not in sql


def test_postgresql_upgrade_downgrade_only_on_explicit_empty_test_db(monkeypatch):
    url = os.getenv("PUW_V54_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CONDITIONAL: no explicit isolated PostgreSQL test database")
    from sqlalchemy.engine import make_url
    from sqlalchemy import inspect
    parsed = make_url(url)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in {"localhost","127.0.0.1","::1"} or (
        os.getenv("GITHUB_ACTIONS") == "true" and parsed.host == "postgres")
    assert parsed.database and parsed.database.startswith("puw_v54_test_")
    engine = create_engine(url)
    assert not inspect(engine).get_table_names(), "Refuse nonempty database"
    monkeypatch.setenv("DATABASE_URL",url)
    try:
        command.upgrade(migration_config(),"head")
        with Session(engine) as session:
            seed(session)
            with pytest.raises(RuntimeError,match="Pilot data exists"):
                run_downgrade_on_connection(session.connection())
            session.rollback()
        command.downgrade(migration_config(),"f360a1b2c3d4")
        command.upgrade(migration_config(),"head")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_SCHEMA_REVISION
    finally:
        engine.dispose()
