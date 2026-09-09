"""Offline exact-native cache contracts; not Google export runtime proof."""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from io import BytesIO

import pytest
from sqlalchemy import func, select

from app.core.v54_permissions import SourceEvidenceError
from app.models.materialization import Materialization
from app.models.v54_pilot import ConnectionIdentity, Evidence, SourceCurrent, SourceReference, SourceVersion
from app.organizer_engine.drive import DriveClient
from app.staging.contracts import KekRef
from app.staging.filesystem import new_fence
from test_v54_materialization_lifecycle import db, lifecycle  # noqa: F401
from test_v54_source_evidence_pilot import P, scope
from v54_pilot_fixture import NOW, uid


def native_module():
    # Import in tests so the unimplemented contract is a normal regression failure.
    from app.integrations import native_export
    return native_export


@pytest.fixture
def cached(db, lifecycle, request):
    changes = getattr(request, "param", {})
    service, clock = lifecycle
    service.authority = replace(service.authority, copy_allowed=True)
    policy = service.authority.policy
    service.authority = replace(service.authority, policy=replace(
        policy, binding_epochs=policy.binding_epochs + ((uid(710), 1),)))
    policy = service.authority.policy
    db.add(ConnectionIdentity(
        id=uid(710), organization_id=1, provider="google_drive", account_key="synthetic-account",
        state="verified", binding_epoch=1, record_version=1, credential_generation=1, verified_at=NOW,
    ))
    db.flush()
    db.add(SourceReference(
        id=uid(711), organization_id=1, origin_project_id=4, identity_id=uid(710),
        namespace="synthetic-mailbox", external_id="original-native-id", external_id_kind="opaque",
        incarnation=1, object_kind="file", canonical_locator={"kind": "opaque"}, record_version=1,
        freshness="fresh", sync_state="current", availability="available", last_seen_at=NOW,
        last_checked_at=NOW, next_check_at=NOW + timedelta(minutes=5),
        policy_pins=policy.policy_pins(), residency={"source_location": "synthetic"},
    ))
    db.flush()
    payload = b"synthetic export bytes"
    observation = {"schema_version": "native-export.observation.1",
        "original_mime_type": "application/vnd.google-apps.document", "export_mime_type": "text/plain",
        "export_size": len(payload)}
    observation.update(changes.get("observation", {}))
    db.add(SourceVersion(
        id=uid(712), organization_id=1, source_id=uid(711), revision=1,
        observation_key="native-revision-17", provider_revision="17",
        consistency=changes.get("consistency", "revision_bound"), locator_at_observation=observation,
        integrity=changes.get("integrity", [{"algorithm": "sha256", "value": sha256(payload).hexdigest()}]),
        observed_at=NOW,
    ))
    db.flush()
    db.add(SourceCurrent(source_id=uid(711), organization_id=1, version_id=uid(712)))
    db.add(Evidence(
        id=uid(713), organization_id=1, source_id=uid(711), source_version_id=uid(712), revision=1,
        locator={"kind": "whole_object"}, extractor={"name": "synthetic"}, confidence_kind="unknown",
        extracted_at=NOW, policy_pins=policy.policy_pins(),
    ))
    db.flush()
    admitted = service.admit(
        db, scope=scope(), evidence=P("evidence", uid(713)), source_version=P("source_version", uid(712)),
        residency="eu-test", retention_until=NOW + timedelta(minutes=3),
        kek=KekRef("kms/materialization", "v7"), allow_copy=changes.get("copy_allowed", True), allow_derive=True,
    )
    fence = new_fence()
    writing = service.begin_write(db, scope=scope(), materialization=admitted, fence=fence)
    sealed = service.seal(db, scope=scope(), materialization=writing, fence=fence,
        source=BytesIO(payload), max_bytes=1024, kind="source_object", media_type="text/plain")
    pin = service.derive(db, scope=scope(), materialization=sealed)
    return service, clock, pin, payload


def read(db, cached, **changes):
    service, _, pin, _ = cached
    values = dict(db=db, scope=scope(), materialization=pin, source_version=P("source_version", uid(712)),
                  original_id="original-native-id", original_revision="17", export_mime_type="text/plain", max_bytes=1024)
    values.update(changes)
    return native_module().NativeExportCache(service).read(**values)


def test_existing_encrypted_materialization_hit_has_distinct_original_and_export_identity(db, cached):
    result = read(db, cached)
    assert result.content == cached[3]
    assert result.manifest.original_id == "original-native-id"
    assert result.manifest.original_revision == "17"
    assert result.manifest.export_sha256 == sha256(cached[3]).hexdigest()
    assert result.manifest.export_mime_type == "text/plain"
    assert result.manifest.materialization.source_version_pin == P("source_version", uid(712))
    before = db.scalar(select(func.count()).select_from(Materialization))
    assert read(db, cached) == result
    assert db.scalar(select(func.count()).select_from(Materialization)) == before


@pytest.mark.parametrize("changes", [
    {"original_id": "other"}, {"original_revision": "18"}, {"export_mime_type": "text/csv"},
    {"source_version": P("source_version", uid(999))}, {"max_bytes": 1}, {"scope": scope(3)},
])
def test_cache_identity_mismatch_rejected_before_bytes(db, cached, monkeypatch, changes):
    monkeypatch.setattr(cached[0].storage, "read_chunks", lambda *a, **kw: pytest.fail("cache bytes read"))
    with pytest.raises(SourceEvidenceError, match="resource_unavailable"):
        read(db, cached, **changes)


@pytest.mark.parametrize("reason", ["ttl", "copy", "current", "unavailable", "revoked"])
def test_no_fallback_after_expiry_or_policy_or_original_change(db, cached, monkeypatch, reason):
    service, clock, _, _ = cached
    if reason == "ttl": clock[0] = NOW + timedelta(minutes=3)
    if reason == "copy": service.authority = replace(service.authority, copy_allowed=False)
    if reason == "current":
        db.add(SourceVersion(id=uid(799), organization_id=1, source_id=uid(711), revision=1,
            observation_key="changed", provider_revision="18", consistency="revision_bound",
            locator_at_observation={}, integrity=[], observed_at=NOW))
        db.flush()
        db.get(SourceCurrent, uid(711)).version_id = uid(799)
    if reason == "unavailable": db.get(SourceReference, uid(711)).availability = "deleted"
    if reason == "revoked": db.get(ConnectionIdentity, uid(710)).state = "revoked"
    monkeypatch.setattr(service.storage, "read_chunks", lambda *a, **kw: pytest.fail("cache bytes read"))
    with pytest.raises(SourceEvidenceError):
        read(db, cached)


def test_exported_bytes_hash_mismatch_is_not_returned(db, cached, monkeypatch):
    monkeypatch.setattr(cached[0].storage, "read_chunks", lambda *a, **kw: iter([b"x" * len(cached[3])]))
    with pytest.raises(SourceEvidenceError, match="resource_unavailable"):
        read(db, cached)


@pytest.mark.parametrize("cached", [
    {"consistency": "digest_observed"}, {"consistency": "metadata_only"},
    {"observation": {"schema_version": "legacy"}}, {"observation": {"export_mime_type": "text/csv"}},
    {"observation": {"export_size": True}}, {"observation": {"export_size": 0}},
    {"integrity": []}, {"integrity": [{"algorithm": "md5", "value": "a" * 32}]},
    {"integrity": [{"algorithm": "sha256", "value": "bad"}]}, {"copy_allowed": False},
], indirect=True)
def test_unproven_legacy_or_mismatched_manifest_cannot_authorize_bytes(db, cached, monkeypatch):
    monkeypatch.setattr(cached[0].storage, "read_chunks", lambda *a, **kw: pytest.fail("cache bytes read"))
    with pytest.raises(SourceEvidenceError, match="resource_unavailable"):
        read(db, cached)


def test_read_crossing_ttl_does_not_return_expired_bytes(db, cached, monkeypatch):
    def delayed_read(*args, **kwargs):
        cached[1][0] = NOW + timedelta(minutes=3)
        yield cached[3]
    monkeypatch.setattr(cached[0].storage, "read_chunks", delayed_read)
    with pytest.raises(SourceEvidenceError, match="resource_unavailable"):
        read(db, cached)


def test_cache_miss_has_no_provider_fallback(db, cached):
    with pytest.raises(SourceEvidenceError, match="resource_unavailable"):
        read(db, cached, materialization=cached[2].model_copy(update={"ref":
            cached[2].ref.model_copy(update={"id": cached[2].ref.id.model_copy(update={"value": uid(888)})})}))
