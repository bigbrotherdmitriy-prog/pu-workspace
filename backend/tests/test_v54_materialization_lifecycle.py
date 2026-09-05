from dataclasses import replace
from datetime import timedelta
from io import BytesIO

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models
from app.database import Base
from app.core.v54_permissions import SourceEvidenceError
from app.models.audit_log import AuditLog
from app.models.materialization import Materialization
from app.models.v54_pilot import AuditExtension, Evidence
from app.source_evidence.materialization import MaterializedFragmentStore
from app.source_evidence.facade import SourceEvidenceFacade
from app.source_evidence.fragment_reader import FragmentLimits, FragmentStoreRequest, read_fragment
from app.staging.contracts import KekRef
from app.staging.filesystem import FilesystemStagingStorage, new_fence
from app.staging.lifecycle import LifecycleAuthority, MaterializationLifecycle
from test_v54_source_evidence_pilot import P, R, policy as source_policy, prepared, scope
from v54_pilot_fixture import NOW, seed


class Keys:
    def resolve(self, reference, version):
        if (reference, version) != ("kms/materialization", "v7"):
            raise KeyError("unknown")
        return b"k" * 32


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed(session)
        yield session
        session.rollback()
    engine.dispose()


@pytest.fixture
def lifecycle(db, tmp_path):
    prepared(db)
    clock = [NOW]
    policy = source_policy()
    policy = replace(policy, grants=policy.grants | frozenset({(2, "fragment")}))
    authority = LifecycleAuthority(
        policy=policy, allowed_residencies=frozenset({"eu-test"}),
        allowed_keks=frozenset({KekRef("kms/materialization", "v7")}),
        max_retention=timedelta(hours=1), derive_allowed=True, retention_owner=True,
    )
    service = MaterializationLifecycle(
        authority, FilesystemStagingStorage(tmp_path / "ciphertext", Keys(), chunk_size=8),
        lambda: clock[0],
    )
    return service, clock


def admitted(service, db, **changes):
    proof = db.get(Evidence, "00000000-0000-4000-8000-000000000060")
    values = dict(
        scope=scope(), evidence=P("evidence", "00000000-0000-4000-8000-000000000060"),
        source_version=P("source_version", proof.source_version_id),
        residency="eu-test", retention_until=NOW + timedelta(minutes=30),
        kek=KekRef("kms/materialization", "v7"), allow_derive=True,
    )
    values.update(changes)
    return service.admit(db, **values)


def sealed(service, db, payload=b"safe synthetic fragment"):
    first = admitted(service, db)
    fence = new_fence()
    writing = service.begin_write(db, scope=scope(), materialization=first, fence=fence)
    result = service.seal(
        db, scope=scope(), materialization=writing, fence=fence, source=BytesIO(payload),
        max_bytes=1024, kind="extracted_text", media_type="text/plain; charset=utf-8",
    )
    return service.derive(db, scope=scope(), materialization=result)


def test_full_lifecycle_exact_binding_manifest_and_safe_audit(db, lifecycle):
    service, _ = lifecycle
    derived = sealed(service, db)
    row = db.get(Materialization, derived.ref.id.value)
    assert row.state == "DERIVED" and row.record_version == 4
    assert row.evidence_id.endswith("060")
    assert row.manifest["storage"]["object_id"] == row.object_id
    assert set(row.manifest) == {
        "schema_version", "storage", "evidence_pin", "source_ref",
        "source_version_pin", "kind", "media_type",
    }
    assert service.read(db, scope=scope(), materialization=derived, max_bytes=1024) == b"safe synthetic fragment"
    assert db.get(Evidence, row.evidence_id).representation_ref["handle"] == row.object_id
    logs = list(db.scalars(select(AuditLog).where(AuditLog.entity_type == "materialization")))
    assert len(logs) == 4 and all(value.details is None and value.entity_id is None for value in logs)
    extensions = list(db.scalars(select(AuditExtension).where(
        AuditExtension.subject_type == "materialization")))
    rendered = repr([(x.subject_id, x.sequence, x.correlation_id) for x in extensions])
    assert "safe synthetic fragment" not in rendered and all(x.relation_refs == [] for x in extensions)


def test_every_materialization_capability_defaults_to_deny(db, lifecycle):
    service, _ = lifecycle
    denied = MaterializationLifecycle(
        LifecycleAuthority(policy=service.authority.policy), service.storage, service.clock,
    )
    with pytest.raises(SourceEvidenceError, match="resource_unavailable"):
        admitted(denied, db)
    pin = admitted(service, db, allow_derive=False)
    fence = new_fence()
    writing = service.begin_write(db, scope=scope(), materialization=pin, fence=fence)
    sealed_pin = service.seal(
        db, scope=scope(), materialization=writing, fence=fence, source=BytesIO(b"x"),
        max_bytes=1, kind="quote", media_type="text/plain",
    )
    with pytest.raises(SourceEvidenceError):
        service.derive(db, scope=scope(), materialization=sealed_pin)


def test_owner_project_tenant_and_stale_cas_are_non_disclosing(db, lifecycle):
    service, _ = lifecycle
    first = admitted(service, db)
    for bad_scope in (scope(3), scope().model_copy(update={"project": R("project", 9)})):
        with pytest.raises(SourceEvidenceError, match="resource_unavailable"):
            service.begin_write(db, scope=bad_scope, materialization=first, fence=new_fence())
    fence = new_fence()
    service.begin_write(db, scope=scope(), materialization=first, fence=fence)
    with pytest.raises(SourceEvidenceError):
        service.begin_write(db, scope=scope(), materialization=first, fence=fence)


def test_seal_is_recoverable_after_database_rollback_with_same_fence(db, lifecycle):
    service, _ = lifecycle
    first = admitted(service, db)
    fence = new_fence()
    writing = service.begin_write(db, scope=scope(), materialization=first, fence=fence)
    db.commit()
    db.begin()
    service.seal(
        db, scope=scope(), materialization=writing, fence=fence, source=BytesIO(b"recoverable"),
        max_bytes=100, kind="quote", media_type="text/plain",
    )
    db.rollback()  # ciphertext exists; durable row is still WRITING
    db.begin()
    recovered = service.seal(
        db, scope=scope(), materialization=writing, fence=fence, source=BytesIO(b"ignored"),
        max_bytes=100, kind="quote", media_type="text/plain",
    )
    assert db.get(Materialization, recovered.ref.id.value).state == "SEALED"


def test_fragment_adapter_rechecks_handle_pins_and_acl(db, lifecycle):
    service, _ = lifecycle
    derived = sealed(service, db, b"fragment")
    row = db.get(Materialization, derived.ref.id.value)
    manifest = row.manifest
    request = FragmentStoreRequest(
        representation_id=row.id, handle=row.object_id,
        evidence_pin=manifest["evidence_pin"], source_ref=manifest["source_ref"],
        source_version_pin=manifest["source_version_pin"], kind=manifest["kind"],
        media_type=manifest["media_type"], max_bytes=32,
    )
    payload = MaterializedFragmentStore(db, scope=scope(), lifecycle=service).read(request)
    assert payload.fragment == b"fragment"
    with pytest.raises(SourceEvidenceError):
        MaterializedFragmentStore(db, scope=scope(3), lifecycle=service).read(request)
    with pytest.raises(SourceEvidenceError):
        MaterializedFragmentStore(db, scope=scope(), lifecycle=service).read(
            request.model_copy(update={"handle": "0" * 32}))


def test_fragment_reader_uses_current_exact_materialization(db, lifecycle):
    service, _ = lifecycle
    derived = sealed(service, db, b"fragment through reader")
    result = read_fragment(
        db, scope=scope(), evidence_pin=P("evidence", "00000000-0000-4000-8000-000000000060"),
        resolver=SourceEvidenceFacade(service.authority.policy, lambda: NOW),
        store=MaterializedFragmentStore(db, scope=scope(), lifecycle=service),
        clock=lambda: NOW, limits=FragmentLimits(max_bytes=100),
    )
    assert result.fragment == "fragment through reader"
    assert result.representation_id == derived.ref.id.value


def test_copy_is_separately_denied_even_when_fragment_read_is_allowed(db, lifecycle):
    service, _ = lifecycle
    derived = sealed(service, db)
    with pytest.raises(SourceEvidenceError):
        service.read(db, scope=scope(), materialization=derived, max_bytes=100, for_copy=True)


def test_expire_purge_tombstone_survives_retries_and_cannot_be_revived(db, lifecycle):
    service, clock = lifecycle
    derived = sealed(service, db)
    clock[0] = NOW + timedelta(minutes=31)
    expired = service.expire(db, scope=scope(), materialization=derived)
    purged = service.purge(db, scope=scope(), materialization=expired)
    row = db.get(Materialization, purged.ref.id.value)
    assert row.state == "PURGED"
    assert row.manifest == {"schema_version": "v54.materialization.tombstone.1"}
    assert row.wrapped_dek is row.chunk_size is row.format_version is None
    with pytest.raises(SourceEvidenceError):
        service.read(db, scope=scope(), materialization=purged, max_bytes=100)
    with pytest.raises(SourceEvidenceError):
        service.begin_write(db, scope=scope(), materialization=purged, fence=new_fence())
    db.delete(row)
    with pytest.raises(ValueError, match="materialization_tombstone_required"):
        db.flush()


def test_expired_writing_is_recovered_without_leaving_active_fence(db, lifecycle):
    service, clock = lifecycle
    first = admitted(service, db)
    writing = service.begin_write(db, scope=scope(), materialization=first, fence=new_fence())
    clock[0] = NOW + timedelta(minutes=31)
    expired = service.expire(db, scope=scope(), materialization=writing)
    row = db.get(Materialization, expired.ref.id.value)
    assert row.state == "EXPIRED" and row.active_fence is None


def test_purge_is_recoverable_after_database_rollback(db, lifecycle):
    service, clock = lifecycle
    derived = sealed(service, db)
    clock[0] = NOW + timedelta(minutes=31)
    expired = service.expire(db, scope=scope(), materialization=derived)
    db.commit()
    db.begin()
    service.purge(db, scope=scope(), materialization=expired)
    db.rollback()  # ciphertext deletion completed, row remains EXPIRED
    db.begin()
    purged = service.purge(db, scope=scope(), materialization=expired)
    assert db.get(Materialization, purged.ref.id.value).state == "PURGED"


def test_exact_source_version_binding_and_database_shape_fail_closed(db, lifecycle):
    service, _ = lifecycle
    with pytest.raises(SourceEvidenceError):
        admitted(service, db, source_version=P(
            "source_version", "00000000-0000-4000-8000-000000000015"))
    first = admitted(service, db)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(update(Materialization).where(
                Materialization.id == first.ref.id.value).values(state="PURGED"))


def test_evidence_representation_binding_is_one_way(db, lifecycle):
    service, _ = lifecycle
    derived = sealed(service, db)
    evidence = db.get(Evidence, "00000000-0000-4000-8000-000000000060")
    evidence.representation_ref = {"replacement": derived.ref.id.value}
    with pytest.raises(ValueError, match="immutable_pilot_assertion"):
        db.flush()
