"""Synthetic facade regression; SQLite results are not PostgreSQL runtime proof."""
from dataclasses import replace
from datetime import timedelta
import json
import traceback

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

import app.models
from app.database import Base
from app.core import v54_transactions
from app.core.v54_interfaces import ReviewCommand, require_resolution
from app.core.v54_permissions import SyntheticPolicy, SourceEvidenceError
from app.integrations.connection_identity import IdentityFacade
from app.models.v54_pilot import (
    ConnectionIdentity, MailConnection, SourceReference, SourceVersion, SourceCurrent,
    Evidence, EvidenceAssessment, AuditExtension, ActionRevision,
)
from app.source_evidence.facade import SourceEvidenceFacade
from v54_pilot_fixture import seed, scope as foundation_scope, uid, ref, pin, NOW
from app.core.v54_refs import ObjectRef, VersionPin

P = lambda kind, identity, version=1: VersionPin.model_validate(
    pin(kind, identity, version, version_kind="record_version" if kind == "source" else "revision"))
R = lambda kind, identity: ObjectRef.model_validate(ref(kind, identity))


def scope(actor=2):
    return foundation_scope().model_copy(update={"actor": R("user", actor), "correlation_id": uid(999)})


def policy():
    return SyntheticPolicy(
        tenant_id=1, project_id=4, pin=P("policy", uid(22)),
        grants=frozenset((actor, op) for actor, ops in [
            (2, ["identity", "write", "observe", "metadata", "dispatch", "audit"]),
            (3, ["metadata", "review", "dispatch", "audit"])] for op in ops),
        accounts=frozenset({"synthetic-account", "synthetic-other"}),
        namespaces=frozenset({"synthetic-mailbox", "synthetic-second"}),
        binding_epochs=((uid(10), 1),), valid_until=NOW+timedelta(hours=1),
        freshness_ttl=timedelta(minutes=5), authority_epoch=1,
        acl="allow", retention_known=True, residency_allowed=True, synthetic_only=True)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def fks(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
        yield session
        session.rollback()
    engine.dispose()


def register(service, db, external_id="synthetic-pilot-message", namespace="synthetic-mailbox", identity=None):
    return service.register_source(db, scope=scope(), identity=identity or R("connection_identity",uid(10)),
                                   namespace=namespace, external_id=external_id, object_kind="message")


def prepared(db, *, review=True):
    service = SourceEvidenceFacade(policy(), lambda: NOW)
    source = register(service,db)
    source, version = service.observe(db,scope=scope(),source=source,identity=R("connection_identity",uid(10)),
                                     namespace="synthetic-mailbox",observation_key="synthetic-run",provider_revision="synthetic-v1")
    evidence = service.create_evidence(db,scope=scope(),source=source.ref,version=version,evidence_id=uid(60))
    if review:
        service.review(db,scope=scope(3),command=ReviewCommand(subject=evidence,expected_record_version=1,decision="confirmed"))
    return service,source,version,evidence


def check(service, db, evidence, actor=2):
    return service.check_evidence_before_dispatch(db,scope=scope(actor),evidence=evidence)


def test_same_account_refresh_and_new_account_identity(db):
    fac = IdentityFacade(policy(),lambda:NOW)
    existing = fac.register(db,scope=scope(),account_key="synthetic-account")
    assert existing.id.value == uid(10)
    refreshed = fac.refresh(db,scope=scope(),identity=existing,account_key="synthetic-account",expected_version=1)
    assert refreshed == existing
    assert db.get(ConnectionIdentity,uid(10)).binding_epoch == 1
    new = fac.register(db,scope=scope(),account_key="synthetic-other")
    assert new != existing
    assert db.get(ConnectionIdentity,uid(10)).account_key == "synthetic-account"
    with pytest.raises(SourceEvidenceError):
        fac.refresh(db,scope=scope(),identity=existing,account_key="synthetic-other",expected_version=2)


def test_account_replacement_revokes_old_without_repointing_sources(db):
    service,source,version,evidence = prepared(db)
    replacement = IdentityFacade(policy(),lambda:NOW).replace_account(
        db,scope=scope(),identity=R("connection_identity",uid(10)),account_key="synthetic-other",expected_version=1)
    assert replacement.id.value != uid(10)
    assert db.get(SourceReference,source.ref.id.value).identity_id == uid(10)
    assert db.get(ConnectionIdentity,uid(10)).state == "revoked"
    with pytest.raises(SourceEvidenceError):
        check(service,db,evidence)


def test_external_ids_are_scoped_to_account_and_namespace(db):
    pol = policy()
    identity = IdentityFacade(pol,lambda:NOW).register(db,scope=scope(),account_key="synthetic-other")
    # Synthetic fixture only: MailConnection is owned by the other stream.
    db.add_all([MailConnection(id=uid(40),organization_id=1,identity_id=identity.id.value,namespace="synthetic-mailbox",state="active"),
                MailConnection(id=uid(41),organization_id=1,identity_id=uid(10),namespace="synthetic-second",state="active")])
    db.flush()
    pol = replace(pol,binding_epochs=pol.binding_epochs+((identity.id.value,1),))
    service = SourceEvidenceFacade(pol,lambda:NOW)
    a = register(service,db,"same-external-id")
    b = register(service,db,"same-external-id",identity=identity)
    c = register(service,db,"same-external-id",namespace="synthetic-second")
    assert len({a.ref.id.value,b.ref.id.value,c.ref.id.value}) == 3
    assert register(service,db,"same-external-id") == a


def test_source_observation_dedup_and_stale_cas(db):
    service,source,version,evidence = prepared(db)
    old = P("source",source.ref.id.value,1)
    same = service.observe(db,scope=scope(),source=old,identity=R("connection_identity",uid(10)),
                           namespace="synthetic-mailbox",observation_key="synthetic-run",provider_revision="synthetic-v1")
    assert same == (source,version)
    with pytest.raises(SourceEvidenceError,match="version_conflict"):
        service.observe(db,scope=scope(),source=old,identity=R("connection_identity",uid(10)),
                        namespace="synthetic-mailbox",observation_key="other-run",provider_revision="v2")
    assert db.get(SourceCurrent,source.ref.id.value).version_id == version.ref.id.value


def test_new_observation_invalidates_old_evidence(db):
    service,source,version,evidence = prepared(db)
    new_source,new_version = service.observe(db,scope=scope(),source=source,identity=R("connection_identity",uid(10)),
                                            namespace="synthetic-mailbox",observation_key="run2",provider_revision="synthetic-v2")
    assert new_version != version
    assert db.get(Evidence,evidence.ref.id.value).source_version_id == version.ref.id.value
    with pytest.raises(SourceEvidenceError):
        check(service,db,evidence)
    with pytest.raises(SourceEvidenceError):
        service.recheck_evidence(db,scope=scope(),evidence=evidence,expected_assessment_version=2,
                                 expected_source_version=new_source.value,observed_provider_revision="synthetic-v1")


@pytest.mark.parametrize("availability",["access_denied","provider_unavailable","deleted","unknown"])
def test_unavailable_source_blocks_pre_dispatch(db,availability):
    service,source,version,evidence = prepared(db)
    service.mark_unavailable(db,scope=scope(),source=source,availability=availability)
    with pytest.raises(SourceEvidenceError):
        check(service,db,evidence)


def test_freshness_only_update_preserves_assertion_and_approval_hash(db):
    service,source,version,evidence = prepared(db)
    before = db.get(ActionRevision,(uid(20),1)).envelope_hash
    later = SourceEvidenceFacade(policy(),lambda:NOW+timedelta(minutes=6))
    with pytest.raises(SourceEvidenceError):
        check(later,db,evidence)
    later.recheck_evidence(db,scope=scope(),evidence=evidence,expected_assessment_version=2,
                           expected_source_version=source.value,observed_provider_revision="synthetic-v1")
    assert check(later,db,evidence).verification == "verified"
    assert db.get(Evidence,evidence.ref.id.value).revision == 1
    assert db.get(SourceCurrent,source.ref.id.value).version_id == version.ref.id.value
    assert db.get(EvidenceAssessment,evidence.ref.id.value).record_version == 3
    assert db.get(ActionRevision,(uid(20),1)).envelope_hash == before


@pytest.mark.parametrize("change",[
    {"acl":"unknown"},{"acl":"deny"},{"retention_known":False},{"residency_allowed":False},
    {"valid_until":None},{"valid_until":NOW},{"freshness_ttl":None},{"authority_epoch":None},
    {"synthetic_only":False},{"grants":frozenset()},{"binding_epochs":()},
])
def test_unknown_policy_is_deny_not_a_production_default(db,change):
    _,source,version,evidence = prepared(db)
    service = SourceEvidenceFacade(replace(policy(),**change),lambda:NOW)
    result=service.resolve(db,scope=scope(),pin=evidence,operation="dispatch")
    assert result.pin == evidence and result.actor == scope().actor and result.project == scope().project
    with pytest.raises(ValueError):
        require_resolution(result,scope=scope(),pin=evidence,operation="dispatch",now=NOW)


def test_legacy_unresolved_and_missing_policy_are_not_inferred(db):
    service=SourceEvidenceFacade(policy(),lambda:NOW)
    # Foundation evidence has no source policy binding, despite "verified" fixture state.
    with pytest.raises(SourceEvidenceError):
        check(service,db,P("evidence",uid(16)))
    identity=db.get(ConnectionIdentity,uid(10))
    identity.credential_generation=None
    db.flush()
    with pytest.raises(SourceEvidenceError):
        register(service,db)


def test_unreviewed_evidence_does_not_authorize_dispatch(db):
    service,source,version,evidence = prepared(db,review=False)
    with pytest.raises(SourceEvidenceError):
        check(service,db,evidence)
    with pytest.raises(SourceEvidenceError):
        service.review(db,scope=scope(2),command=ReviewCommand(subject=evidence,expected_record_version=1,decision="confirmed"))
    service.review(db,scope=scope(3),command=ReviewCommand(subject=evidence,expected_record_version=1,decision="confirmed"))
    check(service,db,evidence)
    service.review(db,scope=scope(3),command=ReviewCommand(subject=evidence,expected_record_version=2,decision="rejected"))
    with pytest.raises(SourceEvidenceError):
        check(service,db,evidence)


def test_cross_tenant_source_version_and_wrong_binding(db):
    service,source,version,evidence = prepared(db)
    other=register(service,db,"synthetic-other-source")
    with pytest.raises(SourceEvidenceError):
        service.create_evidence(db,scope=scope(),source=other.ref,version=version,evidence_id=uid(62))
    foreign=VersionPin.model_validate(pin("evidence",evidence.ref.id.value,tenant=2))
    with pytest.raises(SourceEvidenceError):
        check(service,db,foreign)
    for namespace,identity in [("synthetic-second",R("connection_identity",uid(10))),
                               ("synthetic-mailbox",R("connection_identity",uid(77)))]:
        with pytest.raises(SourceEvidenceError):
            service.observe(db,scope=scope(),source=source,identity=identity,namespace=namespace,
                            observation_key="bad-run",provider_revision="v1")


def test_unknown_revision_cannot_be_verified(db):
    service=SourceEvidenceFacade(policy(),lambda:NOW)
    source=register(service,db)
    source,version=service.observe(db,scope=scope(),source=source,identity=R("connection_identity",uid(10)),
                                  namespace="synthetic-mailbox",observation_key="unknown",provider_revision=None)
    evidence=service.create_evidence(db,scope=scope(),source=source.ref,version=version,evidence_id=uid(60))
    with pytest.raises(SourceEvidenceError):
        service.review(db,scope=scope(3),command=ReviewCommand(subject=evidence,expected_record_version=1,decision="confirmed"))


def test_evidence_idempotency_and_immutable_insert(db):
    service,source,version,evidence=prepared(db)
    assert service.create_evidence(db,scope=scope(),source=source.ref,version=version,evidence_id=uid(60)) == evidence
    assert db.scalar(select(func.count()).select_from(Evidence).where(Evidence.id==uid(60))) == 1
    assert service.resolve(db,scope=scope(),pin=evidence,operation="fragment").acl == "deny"


def test_audit_failure_and_caller_rollback_are_atomic(db,monkeypatch):
    db.commit()  # caller-owned fixture setup
    initial_count=db.scalar(select(func.count()).select_from(SourceReference))
    db.rollback()
    with pytest.raises(RuntimeError,match="synthetic-audit-failure"):
        with db.begin():
            monkeypatch.setattr(v54_transactions,"append_audit",lambda *a,**kw: (_ for _ in ()).throw(RuntimeError("synthetic-audit-failure")))
            register(SourceEvidenceFacade(policy(),lambda:NOW),db)
    assert db.scalar(select(func.count()).select_from(SourceReference)) == initial_count


def test_helpers_never_commit_rollback_or_close(db,monkeypatch):
    with monkeypatch.context() as m:
        def forbidden(*a,**kw):
            raise AssertionError("transaction ownership violation")
        for name in ("commit","rollback","close"):
            m.setattr(db,name,forbidden)
        prepared(db)


def test_audit_and_errors_do_not_include_source_or_secrets(db):
    service=SourceEvidenceFacade(policy(),lambda:NOW)
    sensitive="synthetic-secret-and-source-content-marker"
    register(service,db,sensitive)
    audit_rows=db.scalars(select(app.models.AuditLog)).all()
    assert all(row.details is None for row in audit_rows)
    data=[{column.key:getattr(row,column.key) for column in AuditExtension.__table__.columns}
          for row in db.scalars(select(AuditExtension)).all()]
    assert sensitive not in json.dumps(data,default=str)
    with pytest.raises(SourceEvidenceError) as error:
        register(service,db,namespace=sensitive)
    assert sensitive not in "".join(traceback.format_exception(error.value))


def test_revoked_reviewer_permission_invalidates_verification(db):
    _, source, version, evidence = prepared(db)
    pol = policy()
    service = SourceEvidenceFacade(
        replace(pol, grants=pol.grants - {(3, "review")}), lambda: NOW)
    with pytest.raises(SourceEvidenceError):
        check(service, db, evidence)


def test_register_does_not_resolve_legacy_identity(db):
    db.get(ConnectionIdentity, uid(10)).credential_generation = None
    db.flush()
    with pytest.raises(SourceEvidenceError):
        IdentityFacade(policy(), lambda: NOW).register(
            db, scope=scope(), account_key="synthetic-account")


def test_shortened_freshness_policy_uses_last_check_not_now(db):
    _, source, version, evidence = prepared(db)
    service = SourceEvidenceFacade(
        replace(policy(), freshness_ttl=timedelta(seconds=30)),
        lambda: NOW + timedelta(minutes=1))
    with pytest.raises(SourceEvidenceError):
        check(service, db, evidence)


@pytest.mark.parametrize("change", ["mailbox", "epoch", "source_freshness", "assessment", "policy_pin"])
def test_revoked_unknown_and_stale_bindings_deny(db, change):
    service, source, version, evidence = prepared(db)
    if change == "mailbox":
        db.get(MailConnection, uid(11)).state = "revoked"
    elif change == "epoch":
        db.get(ConnectionIdentity, uid(10)).binding_epoch += 1
    elif change == "source_freshness":
        db.get(SourceReference, source.ref.id.value).freshness = "unknown"
    elif change == "assessment":
        db.get(EvidenceAssessment, evidence.ref.id.value).freshness = "stale"
    else:
        service = SourceEvidenceFacade(replace(policy(), pin=P("policy", uid(99))), lambda: NOW)
    db.flush()
    with pytest.raises(SourceEvidenceError):
        check(service, db, evidence)


def test_result_cannot_be_replayed_for_other_actor_project_pin_or_operation(db):
    service, source, version, evidence = prepared(db)
    result = check(service, db, evidence)
    for request_scope, requested, operation in [
        (scope(3), evidence, "dispatch"),
        (scope().model_copy(update={"project": R("project", 8)}), evidence, "dispatch"),
        (scope(), P("evidence", uid(88)), "dispatch"),
        (scope(), evidence, "metadata"),
    ]:
        with pytest.raises(ValueError, match="resource_unavailable"):
            require_resolution(result, scope=request_scope, pin=requested, operation=operation, now=NOW)
    with pytest.raises(SourceEvidenceError):
        service.check_evidence_before_dispatch(
            db, scope=scope().model_copy(update={"project": R("project", 8)}), evidence=evidence)


def test_stale_assessment_cas_and_immutable_assertion(db):
    service, source, version, evidence = prepared(db)
    record = db.get(Evidence, evidence.ref.id.value)
    original = {column.key: getattr(record, column.key) for column in Evidence.__table__.columns}
    with pytest.raises(SourceEvidenceError, match="version_conflict"):
        service.review(db, scope=scope(3), command=ReviewCommand(
            subject=evidence, expected_record_version=1, decision="rejected"))
    check(service, db, evidence)
    service.recheck_evidence(db, scope=scope(), evidence=evidence,
        expected_assessment_version=2, expected_source_version=source.value,
        observed_provider_revision="synthetic-v1")
    current = db.get(Evidence, evidence.ref.id.value)
    assert {c.key: getattr(current, c.key) for c in Evidence.__table__.columns} == original


def test_attachment_requires_exact_parent_account_namespace(db):
    service, source, version, evidence = prepared(db)
    attachment = service.register_source(
        db, scope=scope(), identity=R("connection_identity", uid(10)),
        namespace="synthetic-mailbox", external_id="synthetic-attachment",
        object_kind="attachment", parent=source.ref)
    assert db.get(SourceReference, attachment.ref.id.value).parent_source_id == source.ref.id.value
    with pytest.raises(SourceEvidenceError):
        service.register_source(
            db, scope=scope(), identity=R("connection_identity", uid(10)),
            namespace="synthetic-mailbox", external_id="invalid-nested-attachment",
            object_kind="attachment", parent=attachment.ref)
    with pytest.raises(SourceEvidenceError):
        register(service, db, namespace="synthetic-second")  # no matching MailConnection


def test_transaction_is_required_and_correlation_cannot_leak_text(db):
    service = SourceEvidenceFacade(policy(), lambda: NOW)
    with pytest.raises(SourceEvidenceError) as error:
        service.register_source(
            db, scope=scope().model_copy(update={"correlation_id": "synthetic-secret"}),
            identity=R("connection_identity", uid(10)), namespace="synthetic-mailbox",
            external_id="synthetic-source", object_kind="message")
    assert "synthetic-secret" not in "".join(traceback.format_exception(error.value))
    db.rollback()
    with pytest.raises(SourceEvidenceError):
        register(service, db)


def test_cas_preserves_unflushed_caller_work(db):
    service, source, version, evidence = prepared(db)
    caller_project = db.get(app.models.Project, 4)
    with db.no_autoflush:
        caller_project.name = "synthetic-caller-change"
        service.recheck_evidence(db, scope=scope(), evidence=evidence,
            expected_assessment_version=2, expected_source_version=source.value,
            observed_provider_revision="synthetic-v1")
    assert db.get(app.models.Project, 4).name == "synthetic-caller-change"
