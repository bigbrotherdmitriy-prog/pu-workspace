"""Real SQLite authority + A05 lineage; fixture grants never replace DB mandate."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.core.v54_authority import AuthorityResolver, PILOT_SCOPE
from app.core.v54_permissions import SourceEvidenceError, object_ref
from app.core.v54_refs import VersionPin
from app.jobs.queue import execution_owner
from app.local_upload_staging import UploadScope, configure_local_upload_runtime, run_local_upload_job
from app.models.materialization import Materialization
from app.models.project_member import ProjectMember
from app.models.v54_authority import AuthorityState
from app.models.v54_pilot import ConnectionIdentity, Evidence, SourceCurrent, SourceReference, SourceVersion
from app.source_evidence.local_source import require_local_upload_source
from test_v54_local_upload_a05_wiring import wired, _stage, _claimed  # noqa: F401


@pytest.fixture
def local_source_world(wired):
    engine, sessions, runtime, processor, backend, path = wired
    now = datetime.now(timezone.utc)
    with sessions.begin() as db:
        db.add(ProjectMember(project_id=4, user_id=2, role="owner"))
        db.add(AuthorityState(organization_id=1, project_id=4, principal_kind="user", principal_id="2",
            scope=PILOT_SCOPE, membership_role="owner", permissions=["metadata", "fragment", "write", "observe", "audit"],
            state="active", authority_epoch=1, record_version=1, valid_until=now + timedelta(hours=2), updated_at=now))
    original_factory = backend.authority_factory
    def real_authority(db, upload_scope):
        authority = original_factory(db, upload_scope)
        return replace(authority, policy=replace(authority.policy, grants=frozenset(), authority=AuthorityResolver()))
    backend.authority_factory = real_authority
    yield wired


def binding_args(db, backend, staging_id):
    _, scope = backend._service(db, UploadScope(2, 4))
    original = db.get(Materialization, str(UUID(hex=staging_id)))
    return dict(scope=scope, source_ref=object_ref(scope, "source", original.source_id),
                source_version_pin=VersionPin(ref=object_ref(scope, "source_version", original.source_version_id),
                                              version_kind="revision", value=1))


def test_real_local_chain_requires_no_mailbox_and_returns_only_metadata(local_source_world):
    _, sessions, _, _, backend, _ = local_source_world
    queued = _stage(sessions)
    with sessions.begin() as db:
        args = binding_args(db, backend, queued.staging_id)
        result = require_local_upload_source(db, **args)
        assert result.source_ref == args["source_ref"] and result.source_version_pin == args["source_version_pin"]
        assert result.original_state == "DERIVED" and result.owner_id == 2
        assert result.size == len(b"synthetic confidential body") and len(result.checksum_sha256) == 64
        assert result.authority_epoch == 1 and result.binding_epoch == 1
        assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize("change", ["source", "mandate", "current", "original", "member", "project", "user", "deleted_mandate",
                                    "new_mandate", "identity", "version", "original_evidence", "moved_member", "moved_original"])
def test_pending_security_changes_deny_without_refresh_flush_or_rollback(local_source_world, monkeypatch, change):
    from app.models.project import Project
    from app.models.user import User
    _, sessions, _, _, backend, _ = local_source_world
    queued = _stage(sessions)
    with sessions() as db:
        args = binding_args(db, backend, queued.staging_id)
        original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        if change == "source":
            row, field, value = db.get(SourceReference, original.source_id), "availability", "deleted"
        elif change in {"mandate", "deleted_mandate"}:
            row, field, value = db.scalar(select(AuthorityState)), "state", "revoked"
        elif change == "current":
            row, field, value = db.get(SourceCurrent, original.source_id), "version_id", "00000000-0000-0000-0000-000000000001"
        elif change == "original":
            row, field, value = original, "state", "EXPIRED"
        elif change == "moved_original":
            row, field, value = original, "parent_id", "00000000-0000-0000-0000-000000000001"
        elif change == "identity":
            source = db.get(SourceReference, original.source_id)
            row, field, value = db.get(ConnectionIdentity, source.identity_id), "state", "revoked"
        elif change == "version":
            row, field, value = db.get(SourceVersion, original.source_version_id), "revision", 2
        elif change == "original_evidence":
            row, field, value = db.get(Evidence, original.evidence_id), "revision", 2
        elif change == "new_mandate":
            row = AuthorityState(organization_id=1, project_id=4, principal_kind="user", principal_id="2")
            db.add(row)
            field, value = "state", "revoked"
        elif change == "moved_member":
            row, field, value = db.scalar(select(ProjectMember)), "project_id", 999
        elif change == "member":
            row, field, value = db.scalar(select(ProjectMember)), "role", "viewer"
        elif change == "project":
            row, field, value = db.get(Project, 4), "archived_at", datetime.now(timezone.utc)
        else:
            row, field, value = db.get(User, 2), "is_admin", True
        if change == "deleted_mandate":
            db.delete(row)
            value = getattr(row, field)
        else:
            setattr(row, field, value)
        before = (set(db.new), set(db.dirty), set(db.deleted))
        monkeypatch.setattr(db, "flush", lambda *_a, **_k: pytest.fail("read-only helper flushed"))
        monkeypatch.setattr(db, "rollback", lambda *_a, **_k: pytest.fail("read-only helper rolled back"))
        monkeypatch.setattr(db, "scalar", lambda *_a, **_k: pytest.fail("pending guard must precede refresh queries"))
        with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
            require_local_upload_source(db, **args)
        assert getattr(row, field) == value
        assert (set(db.new), set(db.dirty), set(db.deleted)) == before


def test_pending_child_rows_and_unrelated_project_are_not_original_authority(local_source_world):
    from app.models.project import Project
    _, sessions, _, _, backend, _ = local_source_world
    queued = _stage(sessions)
    with sessions() as db:
        args = binding_args(db, backend, queued.staging_id)
        original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        db.add_all([Project(id=999, name="unrelated", organization_id=1),
                    Materialization(id="00000000-0000-0000-0000-000000000001", source_id=original.source_id, parent_id=original.id),
                    Evidence(id="00000000-0000-0000-0000-000000000002", source_id=original.source_id)])
        pending = set(db.new)
        require_local_upload_source(db, **args)
        assert set(db.new) == pending and not db.dirty and not db.deleted


@pytest.mark.parametrize("change", ["mandate_revoked", "membership_changed", "permission_missing", "expired",
                                    "source_revoked", "provider_spoof", "wrong_checksum_shape", "copy_allowed"])
def test_local_metadata_denies_without_live_authority_or_exact_lineage(local_source_world, change):
    _, sessions, _, _, backend, _ = local_source_world
    queued = _stage(sessions)
    with sessions.begin() as db:
        args = binding_args(db, backend, queued.staging_id)
        original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        source = db.get(SourceReference, original.source_id)
        if change == "mandate_revoked":
            db.execute(update(AuthorityState).values(state="revoked"))
        elif change == "membership_changed":
            db.execute(update(ProjectMember).values(role="viewer"))
        elif change == "permission_missing":
            db.execute(update(AuthorityState).values(permissions=["fragment"]))
        elif change == "expired":
            db.execute(update(Materialization).where(Materialization.id == original.id).values(retention_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        elif change == "source_revoked":
            db.execute(update(SourceReference).where(SourceReference.id == source.id).values(availability="revoked"))
        elif change == "provider_spoof":
            db.execute(update(ConnectionIdentity).where(ConnectionIdentity.id == source.identity_id).values(provider="google_drive"))
        elif change == "wrong_checksum_shape":
            db.execute(update(SourceVersion).where(SourceVersion.id == original.source_version_id).values(integrity=[]))
        else:
            db.execute(update(Materialization).where(Materialization.id == original.id).values(copy_allowed=True))
    with sessions.begin() as db:
        with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
            require_local_upload_source(db, **args)
        assert not db.new and not db.dirty and not db.deleted


def test_synthetic_grants_do_not_substitute_for_an_explicit_db_mandate(wired):
    _, sessions, _, _, backend, _ = wired
    queued = _stage(sessions)
    with sessions.begin() as db:
        with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
            require_local_upload_source(db, **binding_args(db, backend, queued.staging_id))


def test_wrong_exact_version_is_denied_without_changes(local_source_world):
    _, sessions, _, _, backend, _ = local_source_world
    queued = _stage(sessions)
    with sessions.begin() as db:
        args = binding_args(db, backend, queued.staging_id)
        args["source_version_pin"] = args["source_version_pin"].model_copy(update={"value": 2})
        with pytest.raises(SourceEvidenceError):
            require_local_upload_source(db, **args)
        assert not db.new and not db.dirty and not db.deleted


def test_completed_purged_original_requires_explicit_metadata_only_opt_in(local_source_world):
    _, sessions, _, _, backend, _ = local_source_world
    queued = _stage(sessions)
    claim = _claimed(sessions, "local-metadata-test")
    with execution_owner(claim[0], claim[1], attempt=claim[2], locked_at=claim[3]):
        run_local_upload_job({"staging_id": queued.staging_id})
    with sessions.begin() as db:
        args = binding_args(db, backend, queued.staging_id)
        with pytest.raises(SourceEvidenceError):
            require_local_upload_source(db, **args)
        result = require_local_upload_source(db, **args, allow_purged_original=True)
        assert result.original_state == "PURGED"


def test_local_metadata_does_not_open_ciphertext(local_source_world, monkeypatch):
    _, sessions, runtime, _, backend, _ = local_source_world
    queued = _stage(sessions)
    monkeypatch.setattr(runtime.storage, "read_chunks", lambda *_a, **_k: pytest.fail("metadata read opened bytes"))
    with sessions.begin() as db:
        require_local_upload_source(db, **binding_args(db, backend, queued.staging_id), lock=False)


@pytest.mark.parametrize("change", ["manifest_pin_kind", "representation_pin_kind", "representation_source",
                                    "representation_version", "representation_extra", "representation_expiry"])
def test_original_manifest_and_representation_require_full_exact_pins(local_source_world, change):
    from copy import deepcopy
    _, sessions, _, _, backend, _ = local_source_world
    queued = _stage(sessions)
    with sessions.begin() as db:
        args = binding_args(db, backend, queued.staging_id)
        original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        proof = db.get(Evidence, original.evidence_id)
        if change == "manifest_pin_kind":
            manifest = deepcopy(original.manifest)
            manifest["evidence_pin"]["version_kind"] = "record_version"
            db.execute(update(Materialization).where(Materialization.id == original.id).values(manifest=manifest))
        else:
            representation = deepcopy(proof.representation_ref)
            if change == "representation_pin_kind":
                representation["evidence_pin"]["version_kind"] = "record_version"
            elif change == "representation_source":
                representation["source_ref"]["type"] = "evidence"
            elif change == "representation_version":
                representation["source_version_pin"]["value"] = 2
            elif change == "representation_extra":
                representation["untrusted_extra"] = True
            else:
                representation["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
            db.execute(update(Evidence).where(Evidence.id == proof.id).values(representation_ref=representation))
    with sessions.begin() as db:
        with pytest.raises(SourceEvidenceError, match="^resource_unavailable$"):
            require_local_upload_source(db, **args)
