"""Synthetic staged XLSX -> existing durable source/evidence integration."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID

import pytest
from sqlalchemy import null, select, update

from app.jobs.queue import execution_owner, recover_expired
from app.local_upload_staging import (LocalUploadBusinessProcessor, UploadCandidate, UploadScope,
    LocalUploadUnavailable, configure_local_upload_runtime, stage_and_enqueue, run_local_upload_job)
from app.models.materialization import Materialization
from app.models.v54_pilot import Evidence, EvidenceAssessment, SourceVersion, SourceReference
from app.models.job import BackgroundJob
from app.models.v54_authority import AuthorityState
from app.core.v54_permissions import SourceEvidenceError
from app.staging.local_upload import LocalUploadRetentionAuthority
from app.source_evidence.xlsx_ingestion import XlsxEvidenceIngestion
from app.source_evidence.fragment_reader import read_fragment, FragmentLimits
from app.source_evidence.materialization import MaterializedFragmentStore
from app.source_evidence.product import ProductEvidenceResolver
from app.core.v54_permissions import object_ref
from app.core.v54_refs import VersionPin
from test_mvp1_local_source_authority import local_source_world  # noqa: F401
from test_v54_local_upload_a05_wiring import wired, _claimed  # noqa: F401
from test_mvp1_xlsx_cell_evidence import workbook, MIME


@pytest.fixture
def xlsx_world(local_source_world):
    engine, sessions, runtime, _, backend, path = local_source_world
    backend.max_file_bytes = 1024 * 1024
    backend.retention_authority = LocalUploadRetentionAuthority(
        service_principal="xlsx-retention", scopes=frozenset({(1, 4)}),
        allowed_residencies=frozenset({backend.residency}), allowed_keks=frozenset({backend.kek}))
    processor = LocalUploadBusinessProcessor(xlsx_ingestion=XlsxEvidenceIngestion(backend))
    runtime = replace(runtime, processor=processor, max_file_bytes=backend.max_file_bytes,
                      allowed_mime_types=runtime.allowed_mime_types | {MIME})
    configure_local_upload_runtime(runtime)
    return sessions, runtime, backend, path


def stage_xlsx(world, *, data=None, key="xlsx-synthetic"):
    sessions, runtime, _, _ = world
    data = data or workbook([("Plan", 7, "actual.xml",
        '<row r="2"><c r="A2"><f>1+1</f><v>2</v></c><c r="C2"><f>1+2</f></c></row>')])
    with sessions() as db:
        db.begin()
        queued = stage_and_enqueue(db, runtime=runtime, scope=UploadScope(2, 4),
            candidate=UploadCandidate("synthetic.xlsx", MIME, data), request_key=key, index=0)
    return queued, data


def run_xlsx(world, queued, worker="xlsx-worker"):
    claim = _claimed(world[0], worker)
    with execution_owner(claim[0], claim[1], attempt=claim[2], locked_at=claim[3]):
        return run_local_upload_job({"staging_id": queued.staging_id})


def cells(db):
    return [row for row in db.scalars(select(Evidence)) if row.locator.get("kind") == "sheet_cell"]


def test_staged_local_xlsx_persists_formula_cache_evidence_on_original_version(xlsx_world):
    queued, data = stage_xlsx(xlsx_world)
    result = run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
        version = db.get(SourceVersion, original.source_version_id)
        rows = cells(db)
        assert len(rows) == 3
        assert {(row.locator["range_a1"], row.locator["value_kind"]) for row in rows} == {
            ("A2", "formula"), ("A2", "cached_value"), ("C2", "formula")}
        assert {row.source_version_id for row in rows} == {version.id}
        assert version.integrity[0] == {"algorithm": "sha256", "value": hashlib.sha256(data).hexdigest()}
        assert original.state == "PURGED"
        assert result["processed"] == 1


def read_cell(db, backend, row):
    service, scope = backend._service(db, UploadScope(2, 4))
    return read_fragment(db, scope=scope,
        evidence_pin=VersionPin(ref=object_ref(scope, "evidence", row.id), version_kind="revision", value=1),
        resolver=ProductEvidenceResolver(clock=backend.clock),
        store=MaterializedFragmentStore(db, scope=scope, lifecycle=service), clock=backend.clock,
        limits=FragmentLimits(max_bytes=256 * 1024))


def test_exact_local_fragments_read_after_original_cleanup_without_mailbox(xlsx_world):
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        db.begin()
        values = [read_cell(db, xlsx_world[2], row) for row in cells(db)]
        assert {json.loads(value.fragment)["value"] for value in values} == {"1+1", "2", "1+2"}
        assert all(value.effective_status == "unverified" and value.confidence is None for value in values)
        assert all(json.loads(value.fragment)["cache_freshness"] == "not_verified" for value in values)
        assert all(json.loads(value.fragment)["formula_recalculated"] is False for value in values)


def retry_claim(world, queued):
    with world[0].begin() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(
            lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    with world[0]() as db:
        assert recover_expired(db) == 1


def test_mid_child_write_failure_keeps_registry_unavailable_and_replays_same_ids(xlsx_world, monkeypatch):
    queued, _ = stage_xlsx(xlsx_world)
    storage = xlsx_world[1].storage
    write = storage.write
    calls = []
    def fail_second(object_id, *args, **kwargs):
        calls.append(object_id)
        if len(calls) == 1:
            with xlsx_world[0]() as check:
                children = list(check.scalars(select(Materialization).where(Materialization.parent_id.is_not(None))))
                assert len(children) == 3 and all(row.state == "WRITING" and row.active_fence for row in children)
                assert all(row.representation_ref is None for row in cells(check))
        if len(calls) == 2:
            raise RuntimeError("synthetic I/O failure")
        return write(object_id, *args, **kwargs)
    monkeypatch.setattr(storage, "write", fail_second)
    with pytest.raises(SourceEvidenceError, match="^xlsx_evidence_unavailable$"):
        run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        before = {row.id: (row.object_id, row.active_fence) for row in db.scalars(select(Materialization).where(Materialization.parent_id.is_not(None)))}
        assert len(before) == 3 and all(row.representation_ref is None for row in cells(db))
        assert all(row.availability == "unknown" for row in db.scalars(select(EvidenceAssessment).where(EvidenceAssessment.evidence_id.in_(row.id for row in cells(db)))))
    monkeypatch.setattr(storage, "write", write)
    retry_claim(xlsx_world, queued)
    assert run_xlsx(xlsx_world, queued, "replay")["processed"] == 1
    with xlsx_world[0]() as db:
        after = {row.id: row.object_id for row in db.scalars(select(Materialization).where(Materialization.parent_id.is_not(None)))}
        assert after == {key: value[0] for key, value in before.items()}
        assert len(cells(db)) == 3 and all(read_cell(db, xlsx_world[2], row) for row in cells(db))


@pytest.mark.parametrize("change", ["version", "checksum", "authority"])
def test_failed_exact_preflight_has_no_child_effects(xlsx_world, change):
    from app.organizer_engine.content import extract_text_result
    queued, data = stage_xlsx(xlsx_world)
    claim = _claimed(xlsx_world[0], "preflight")
    with execution_owner(claim[0], claim[1], attempt=claim[2], locked_at=claim[3]):
        with xlsx_world[0]() as db:
            record = xlsx_world[1].lifecycle.load_for_processing(db, staging_id=queued.staging_id, job_id=queued.job_id, claim=claim)
            db.commit()
            if change == "version":
                record = replace(record, source_version_id="00000000-0000-0000-0000-000000000001")
            elif change == "checksum":
                record = replace(record, checksum="0" * 64)
            else:
                db.execute(update(AuthorityState).values(state="revoked")); db.commit()
            with pytest.raises(SourceEvidenceError):
                xlsx_world[1].processor.xlsx_ingestion.publish(db, record=record, content=data,
                    extraction=extract_text_result(data, MIME, "synthetic.xlsx"))
    with xlsx_world[0]() as db:
        assert not cells(db)
        assert len(list(db.scalars(select(Materialization)))) == 1


@pytest.mark.parametrize("limit", ["MAX_FRAGMENTS", "MAX_FRAGMENT_BYTES", "MAX_TOTAL_BYTES"])
def test_fragment_budgets_deny_whole_batch_before_admission(xlsx_world, monkeypatch, limit):
    import app.source_evidence.xlsx_ingestion as ingestion
    queued, _ = stage_xlsx(xlsx_world)
    monkeypatch.setattr(ingestion, limit, 1)
    with pytest.raises(SourceEvidenceError):
        run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        assert not cells(db)


def test_published_children_survive_business_failure_and_idempotent_replay(xlsx_world, monkeypatch):
    queued, _ = stage_xlsx(xlsx_world)
    # The bridge commit is before independently committing legacy business helpers.
    import app.document_engine as indexing
    original_index = indexing.index_documents
    monkeypatch.setattr(indexing, "index_documents", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("business crash")))
    with pytest.raises(RuntimeError, match="business crash"):
        run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        before = {row.id: row.representation_ref for row in cells(db)}
        assert len(before) == 3 and all(before.values())
    monkeypatch.setattr(indexing, "index_documents", original_index)
    retry_claim(xlsx_world, queued)
    run_xlsx(xlsx_world, queued, "business-replay")
    with xlsx_world[0]() as db:
        assert {row.id: row.representation_ref for row in cells(db)} == before


@pytest.mark.parametrize("change", ["authority", "provider", "copy", "pending_child"])
def test_local_fragment_denies_invalid_authority_lineage_or_pending_child(xlsx_world, monkeypatch, change):
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        row = cells(db)[0]
        child = db.get(Materialization, row.representation_ref["representation_id"])
        if change == "authority":
            db.execute(update(AuthorityState).values(state="revoked")); db.commit()
        elif change == "provider":
            db.execute(update(SourceReference).values(namespace="provider")); db.commit()
        elif change == "copy":
            db.execute(update(Materialization).where(Materialization.id == child.id).values(copy_allowed=True)); db.commit()
        else:
            child.copy_allowed = True
        monkeypatch.setattr(xlsx_world[1].storage, "read_chunks", lambda *_a, **_k: pytest.fail("denied read opened bytes"))
        with pytest.raises(SourceEvidenceError):
            read_cell(db, xlsx_world[2], row)


def test_ciphertext_publish_then_process_crash_reuses_exact_file_and_registry(xlsx_world, monkeypatch):
    class ProcessCrash(BaseException):
        pass
    queued, _ = stage_xlsx(xlsx_world)
    storage = xlsx_world[1].storage
    write = storage.write
    published = []
    def crash_after_publish(object_id, *args, **kwargs):
        descriptor = write(object_id, *args, **kwargs)
        published.append(descriptor)
        raise ProcessCrash()
    monkeypatch.setattr(storage, "write", crash_after_publish)
    with pytest.raises(ProcessCrash):
        run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        children = list(db.scalars(select(Materialization).where(Materialization.parent_id.is_not(None))))
        assert len(children) == 3 and all(child.state == "WRITING" for child in children)
        assert all(row.representation_ref is None for row in cells(db))
    assert len(published) == 1
    saved_file = next(xlsx_world[3].rglob(f"{published[0].object_id}.enc"))
    ciphertext = saved_file.read_bytes()
    monkeypatch.setattr(storage, "write", write)
    retry_claim(xlsx_world, queued)
    run_xlsx(xlsx_world, queued, "after-crash")
    assert saved_file.read_bytes() == ciphertext
    assert len(list(xlsx_world[3].rglob("*.enc"))) == 3
    with xlsx_world[0]() as db:
        assert len(cells(db)) == 3 and all(read_cell(db, xlsx_world[2], row) for row in cells(db))


def test_cached_child_cannot_hide_persisted_copy_permission_change(xlsx_world, monkeypatch):
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        row = cells(db)[0]
        child = db.get(Materialization, row.representation_ref["representation_id"])
        db.execute(update(Materialization).where(Materialization.id == child.id).values(copy_allowed=True).execution_options(synchronize_session=False))
        assert child.copy_allowed is False
        monkeypatch.setattr(xlsx_world[1].storage, "read_chunks", lambda *_a, **_k: pytest.fail("stale child opened bytes"))
        with pytest.raises(SourceEvidenceError):
            read_cell(db, xlsx_world[2], row)


@pytest.mark.parametrize("state", ["ADMITTED", "WRITING", "SEALED", "DERIVED", "EXPIRED"])
def test_existing_retention_hook_cleans_all_child_states_without_user_grants(xlsx_world, state):
    from app.local_upload_staging import recover_local_upload_retention
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    now = datetime.now(timezone.utc)
    with xlsx_world[0].begin() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(status="completed"))
        db.execute(update(AuthorityState).values(state="revoked"))
        values = {"state": state}
        if state in {"ADMITTED", "WRITING"}:
            values.update(format_version=None, chunk_size=None, wrapped_dek=None, manifest=null(), sealed_at=None, derived_at=None)
        if state == "ADMITTED":
            values.update(writing_at=None)
        if state in {"WRITING", "SEALED"}:
            values.update(active_fence="a" * 32)
        if state == "SEALED":
            values.update(derived_at=None)
        if state == "EXPIRED":
            values.update(expired_at=now)
        db.execute(update(Materialization).where(Materialization.parent_id.is_not(None)).values(**values))
    xlsx_world[2].clock = lambda: now + timedelta(minutes=31)
    assert recover_local_upload_retention(limit=2) == 2
    assert recover_local_upload_retention(limit=2) == 1
    assert recover_local_upload_retention(limit=2) == 0
    assert not list(xlsx_world[3].rglob("*.enc"))
    with xlsx_world[0]() as db:
        assert all(child.state == "PURGED" and child.wrapped_dek is None for child in db.scalars(select(Materialization)))
        assert len(cells(db)) == 3  # immutable evidence is tombstoned by registry, not deleted


def test_child_retention_does_not_race_running_parent_and_retries_delete_failure(xlsx_world, monkeypatch):
    from app.local_upload_staging import recover_local_upload_retention
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    xlsx_world[2].clock = lambda: datetime.now(timezone.utc) + timedelta(minutes=31)
    assert recover_local_upload_retention(limit=5) == 0
    with xlsx_world[0].begin() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(status="completed"))
    delete = xlsx_world[1].storage.delete
    monkeypatch.setattr(xlsx_world[1].storage, "delete", lambda *_a, **_k: (_ for _ in ()).throw(OSError("synthetic delete failure")))
    assert recover_local_upload_retention(limit=5) == 0
    with xlsx_world[0]() as db:
        assert all(child.state == "EXPIRED" for child in db.scalars(select(Materialization).where(Materialization.parent_id.is_not(None))))
    monkeypatch.setattr(xlsx_world[1].storage, "delete", delete)
    assert recover_local_upload_retention(limit=5) == 3
    assert not list(xlsx_world[3].rglob("*.enc"))


@pytest.mark.parametrize("change", [{"worker_id": "new-owner"}, {"payload": {"staging_id": "0" * 32}},
                                     {"cancelled_at": datetime.now(timezone.utc)}])
def test_each_ingestion_fence_reloads_cached_job_ownership(xlsx_world, change):
    queued, _ = stage_xlsx(xlsx_world)
    claim = _claimed(xlsx_world[0], "old-owner")
    with execution_owner(claim[0], claim[1], attempt=claim[2], locked_at=claim[3]):
        with xlsx_world[0]() as db:
            record = xlsx_world[1].lifecycle.load_for_processing(db, staging_id=queued.staging_id, job_id=queued.job_id, claim=claim)
            cached = db.get(BackgroundJob, queued.job_id)
            db.commit()
            xlsx_world[1].processor.xlsx_ingestion._live(db, record)
            db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(**change).execution_options(synchronize_session=False))
            assert cached.worker_id == "old-owner"
            with pytest.raises(SourceEvidenceError):
                xlsx_world[1].processor.xlsx_ingestion._live(db, record)


def test_fragment_expiring_during_real_ciphertext_read_is_not_returned(xlsx_world, monkeypatch):
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    now = [datetime.now(timezone.utc)]
    xlsx_world[2].clock = lambda: now[0]
    read = xlsx_world[1].storage.read_chunks
    def cross_deadline(*args, **kwargs):
        yield from read(*args, **kwargs)
        now[0] += timedelta(hours=1)
    monkeypatch.setattr(xlsx_world[1].storage, "read_chunks", cross_deadline)
    with xlsx_world[0]() as db:
        with pytest.raises(SourceEvidenceError):
            read_cell(db, xlsx_world[2], cells(db)[0])


def test_expired_claim_during_child_io_cannot_publish_evidence(xlsx_world, monkeypatch):
    import app.staging.local_upload as lifecycle
    queued, _ = stage_xlsx(xlsx_world)
    write = xlsx_world[1].storage.write
    real_now = lifecycle.queue_now
    def expire_after_write(*args, **kwargs):
        result = write(*args, **kwargs)
        monkeypatch.setattr(lifecycle, "queue_now", lambda: real_now() + timedelta(hours=1))
        return result
    monkeypatch.setattr(xlsx_world[1].storage, "write", expire_after_write)
    with pytest.raises((SourceEvidenceError, LocalUploadUnavailable)):
        run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        assert len(cells(db)) == 3 and all(row.representation_ref is None for row in cells(db))
        assert all(row.state == "WRITING" for row in db.scalars(select(Materialization).where(Materialization.parent_id.is_not(None))))
    monkeypatch.setattr(lifecycle, "queue_now", real_now)
    monkeypatch.setattr(xlsx_world[1].storage, "write", write)
    retry_claim(xlsx_world, queued)
    assert run_xlsx(xlsx_world, queued, "after-expiry")["processed"] == 1


def test_retention_does_not_treat_a_provider_source_as_local_upload(xlsx_world):
    from app.local_upload_staging import recover_local_upload_retention
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    with xlsx_world[0].begin() as db:
        db.execute(update(BackgroundJob).where(BackgroundJob.id == queued.job_id).values(status="completed"))
        db.execute(update(SourceReference).values(namespace="provider"))
    xlsx_world[2].clock = lambda: datetime.now(timezone.utc) + timedelta(hours=1)
    assert recover_local_upload_retention(limit=5) == 0
    assert len(list(xlsx_world[3].rglob("*.enc"))) == 3
    with xlsx_world[0]() as db:
        assert all(row.state == "DERIVED" for row in db.scalars(select(Materialization).where(Materialization.parent_id.is_not(None))))


def test_ids_only_job_and_audit_do_not_embed_formula_cache_or_original_bytes(xlsx_world):
    from app.models.audit_log import AuditLog
    from app.models.v54_pilot import AuditExtension
    data = workbook([("SecretPlan", 7, "actual.xml", '<row r="1"><c r="A1"><f>314159+271828</f><v>585987</v></c></row>')])
    queued, _ = stage_xlsx(xlsx_world, data=data)
    run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        assert db.get(BackgroundJob, queued.job_id).payload == {"staging_id": queued.staging_id}
        rendered = repr([(row.action, row.details) for row in db.scalars(select(AuditLog))])
        rendered += repr([(row.subject_id, row.subject_pin) for row in db.scalars(select(AuditExtension))])
        for forbidden in ("314159+271828", "585987", "synthetic.xlsx", hashlib.sha256(data).hexdigest()):
            assert forbidden not in rendered
        assert all(row.copy_allowed is False for row in db.scalars(select(Materialization)))
        assert len({row.extractor["configuration_digest"] for row in cells(db)}) == 1


@pytest.mark.parametrize("change", ["project", "tenant", "evidence_revision", "source_pin_revision", "noncompleted_purge"])
def test_retained_local_child_requires_exact_scope_revision_and_completed_purge(xlsx_world, monkeypatch, change):
    queued, _ = stage_xlsx(xlsx_world)
    run_xlsx(xlsx_world, queued)
    with xlsx_world[0]() as db:
        row = cells(db)[0]
        service, scope = xlsx_world[2]._service(db, UploadScope(2, 4))
        pin = VersionPin(ref=object_ref(scope, "evidence", row.id), version_kind="revision", value=1)
        if change == "project":
            project = scope.project.model_copy(update={"id": scope.project.id.model_copy(update={"value": "999"})})
            scope = scope.model_copy(update={"project": project})
        elif change == "tenant":
            scope = scope.model_copy(update={"tenant": scope.tenant.model_copy(update={"value": "999"})})
        elif change == "evidence_revision":
            pin = pin.model_copy(update={"value": 2})
        elif change == "source_pin_revision":
            from copy import deepcopy
            representation = deepcopy(row.representation_ref)
            representation["source_version_pin"]["value"] = 2
            db.execute(update(Evidence).where(Evidence.id == row.id).values(representation_ref=representation))
        else:
            original = db.get(Materialization, str(UUID(hex=queued.staging_id)))
            manifest = {**original.manifest, "outcome": "cancelled"}
            db.execute(update(Materialization).where(Materialization.id == original.id).values(manifest=manifest))
        monkeypatch.setattr(xlsx_world[1].storage, "read_chunks", lambda *_a, **_k: pytest.fail("invalid scope/version opened bytes"))
        with pytest.raises(SourceEvidenceError):
            read_fragment(db, scope=scope, evidence_pin=pin,
                resolver=ProductEvidenceResolver(clock=xlsx_world[2].clock),
                store=MaterializedFragmentStore(db, scope=scope, lifecycle=service),
                clock=xlsx_world[2].clock, limits=FragmentLimits(max_bytes=256 * 1024))
