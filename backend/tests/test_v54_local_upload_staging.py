from __future__ import annotations

import base64
import hashlib
import logging
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.local_upload_staging as local_staging
from app.api import local_upload as local_api
from app.local_upload_staging import (
    ALLOWED_JOB_KEYS,
    CleanupDecision,
    EnqueuedUpload,
    LocalUploadAdmissionDenied,
    LocalUploadBusinessProcessor,
    LocalUploadCancelled,
    LocalUploadConflict,
    LocalUploadLifecycleAdapter,
    LocalUploadRuntime,
    LocalUploadUnavailable,
    MaterializedUpload,
    UploadCandidate,
    UploadReservation,
    UploadScope,
    admit_candidate,
    cleanup_finalized_upload,
    run_local_upload_job,
    stage_and_enqueue,
)
from app.models.job import BackgroundJob
from app.staging import FilesystemStagingStorage, KekRef
from app.staging.contracts import StagingIntegrityError


class StaticResolver:
    def resolve(self, reference: str, version: str) -> bytes:
        if (reference, version) != ("local-upload", "v1"):
            raise KeyError("unknown_kek")
        return b"k" * 32


class SyntheticLifecycle:
    """Durable-port double: state lives independently from runtime instances."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.requests: dict[str, str] = {}
        self.finalized: list[tuple[str, str]] = []
        self.cleanup_by_outcome = {
            "completed": CleanupDecision(True),
            "cancelled": CleanupDecision(True),
            "failed": CleanupDecision(False),
        }

    def _reservation(self, row: dict) -> UploadReservation:
        return UploadReservation(
            staging_id=row["staging_id"], object_id=row["object_id"],
            fence=row["fence"], fingerprint=row["fingerprint"],
            state=row["state"], descriptor=row.get("descriptor"),
            job_id=row.get("job_id"),
        )

    def reserve(
        self, session, *, scope, request_key, object_id, fence, fingerprint,
        display_name, mime_type, checksum, size, expires_at,
    ) -> UploadReservation:
        existing_id = self.requests.get(request_key)
        if existing_id is not None:
            return self._reservation(self.rows[existing_id])
        staging_id = hashlib.sha256(request_key.encode("ascii")).hexdigest()[:32]
        row = {
            "staging_id": staging_id, "scope": scope, "request_key": request_key,
            "object_id": object_id, "fence": fence, "fingerprint": fingerprint,
            "display_name": display_name, "mime_type": mime_type,
            "checksum": checksum, "size": size,
            "expires_at": expires_at, "state": "reserved",
        }
        self.rows[staging_id] = row
        self.requests[request_key] = staging_id
        return self._reservation(row)

    def publish(self, session, *, scope, staging_id, descriptor, checksum, size):
        row = self.rows[staging_id]
        assert row["scope"] == scope
        row.update(descriptor=descriptor, checksum=checksum, size=size, state="published")
        return self._reservation(row)

    def bind_job(self, session, *, scope, staging_id, job_id):
        row = self.rows[staging_id]
        assert row["scope"] == scope
        if row.get("job_id") not in (None, job_id):
            raise LocalUploadConflict("job_binding_conflict")
        row.update(job_id=job_id, state="queued")
        return self._reservation(row)

    def load_for_processing(self, session, *, staging_id, job_id, claim=None):
        row = self.rows[staging_id]
        if row.get("job_id") != job_id:
            raise LocalUploadConflict("materialization_binding_conflict")
        return MaterializedUpload(
            staging_id=staging_id, scope=row["scope"],
            display_name=row["display_name"], mime_type=row["mime_type"],
            checksum=row["checksum"], size=row["size"],
            descriptor=row["descriptor"], job_id=job_id,
        )

    def finalize(self, session, *, scope, staging_id, job_id, outcome, claim=None, result=None):
        row = self.rows[staging_id]
        assert row["scope"] == scope and row["job_id"] == job_id
        row["state"] = outcome
        self.finalized.append((staging_id, outcome))
        return self.cleanup_by_outcome[outcome]

    def complete_cleanup(self, session, **kwargs):
        return None


class SyntheticProcessor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or {
            "processed": 1, "skipped": 0, "tasks": 2,
            "risks": 0, "decisions": 1, "drafts": 0, "documents": [17],
        }
        self.error = error
        self.calls: list[tuple] = []

    def process(self, session, *, record, content, operation_key):
        self.calls.append((record, content, operation_key))
        if self.error is not None:
            raise self.error
        return self.result


class SyntheticSession:
    def __init__(self, events=None, fail_commit: bool | int = False) -> None:
        self.events = events if events is not None else []
        self.fail_commit = fail_commit

    def commit(self):
        self.events.append("commit")
        if (self.fail_commit is True
                or type(self.fail_commit) is int
                and self.fail_commit == self.events.count("commit")):
            raise RuntimeError("SECRET SQL filename=C:/private.txt key=raw")

    def rollback(self):
        self.events.append("rollback")


def make_runtime(
    tmp_path: Path, lifecycle=None, processor=None, session: SyntheticSession | None = None,
) -> LocalUploadRuntime:
    backend = lifecycle or SyntheticLifecycle()
    worker_session = session or SyntheticSession()
    return LocalUploadRuntime(
        storage=FilesystemStagingStorage(tmp_path, StaticResolver(), chunk_size=32),
        lifecycle=LocalUploadLifecycleAdapter(backend),
        processor=processor or SyntheticProcessor(),
        session_factory=lambda: nullcontext(worker_session),
        kek=KekRef("local-upload", "v1"), max_file_bytes=1024,
        allowed_mime_types=frozenset({"text/plain", "application/pdf"}),
    )


def enqueue_once(monkeypatch, captured: list[dict]):
    def fake_enqueue(session, kind, payload, **kwargs):
        captured.append({"kind": kind, "payload": payload, **kwargs})
        return SimpleNamespace(id=71, status="queued")

    monkeypatch.setattr(local_staging, "enqueue", fake_enqueue)


def stage_secret(tmp_path, monkeypatch, *, processor=None):
    lifecycle = SyntheticLifecycle()
    runtime = make_runtime(tmp_path, lifecycle, processor)
    monkeypatch.setattr(local_staging, "_runtime", runtime)
    captured: list[dict] = []
    enqueue_once(monkeypatch, captured)
    secret = b"TOP SECRET local document body"
    candidate = UploadCandidate("contract.txt", "text/plain", secret)
    queued = stage_and_enqueue(
        SyntheticSession(), runtime=runtime, scope=UploadScope(11, 23), candidate=candidate,
        request_key="request-1", index=0,
    )
    return runtime, lifecycle, captured, queued, secret


def job_payload(captured):
    return captured[0]["payload"]


def install_claim(monkeypatch, job_id=71):
    monkeypatch.setattr(
        local_staging, "current_execution_claim",
        lambda: (job_id, "worker-1", 1, datetime.now(timezone.utc)),
    )


def test_admission_normalizes_filename_mime_and_enforces_scope_and_limits():
    candidate = admit_candidate(
        r"C:\private\folder\Report.TXT", " Text/Plain; charset=utf-8 ", b"abc",
        max_file_bytes=3, allowed_mime_types=frozenset({"text/plain"}),
    )
    assert candidate == UploadCandidate("Report.TXT", "text/plain", b"abc")
    with pytest.raises(LocalUploadAdmissionDenied, match="unsupported_mime_type"):
        admit_candidate("a.exe", "application/octet-stream", b"a", max_file_bytes=3,
                        allowed_mime_types=frozenset({"text/plain"}))
    with pytest.raises(LocalUploadAdmissionDenied, match="file_too_large"):
        admit_candidate("a.txt", "text/plain", b"abcd", max_file_bytes=3,
                        allowed_mime_types=frozenset({"text/plain"}))
    with pytest.raises(LocalUploadAdmissionDenied, match="invalid_owner_scope"):
        UploadScope(0, 23)


def test_stage_encrypts_and_job_payload_is_metadata_only(tmp_path, monkeypatch):
    runtime, _, captured, queued, secret = stage_secret(tmp_path, monkeypatch)
    assert queued == EnqueuedUpload(queued.staging_id, 71, "queued")
    assert captured[0]["kind"] == "local_upload.process"
    assert job_payload(captured) == {"staging_id": queued.staging_id}
    assert set(job_payload(captured)) == ALLOWED_JOB_KEYS == {"staging_id"}
    assert set(job_payload(captured)).isdisjoint({
        "content", "content_base64", "path", "filename", "display_name",
        "key", "kek", "wrapped_dek", "idempotency_key", "checksum", "size",
        "mime_type", "owner_id", "project_id",
    })
    assert secret not in b"".join(path.read_bytes() for path in tmp_path.rglob("*.enc"))
    assert captured[0]["idempotency_key"].startswith("local-upload:")
    assert runtime.storage


def test_existing_background_job_durably_persists_only_opaque_staging_id(db_session, tmp_path):
    db = db_session
    lifecycle = SyntheticLifecycle()
    runtime = make_runtime(tmp_path, lifecycle)
    queued = stage_and_enqueue(
        db, runtime=runtime, scope=UploadScope(11, 23),
        candidate=UploadCandidate("contract.txt", "text/plain", b"synthetic body"),
        request_key="durable-request", index=0,
    )
    db.expire_all()
    job = db.get(BackgroundJob, queued.job_id)
    assert job is not None
    assert job.kind == "local_upload.process"
    assert job.status == "queued"
    assert job.payload == {"staging_id": queued.staging_id}
    assert lifecycle.rows[queued.staging_id]["job_id"] == job.id


def test_same_request_is_idempotent_and_changed_content_conflicts(tmp_path, monkeypatch):
    runtime, _, captured, first, _ = stage_secret(tmp_path, monkeypatch)
    same = stage_and_enqueue(
        SyntheticSession(), runtime=runtime, scope=UploadScope(11, 23),
        candidate=UploadCandidate("contract.txt", "text/plain", b"TOP SECRET local document body"),
        request_key="request-1", index=0,
    )
    assert same == EnqueuedUpload(first.staging_id, first.job_id, "already_queued")
    assert len(captured) == 1
    with pytest.raises(LocalUploadConflict, match="idempotency_conflict"):
        stage_and_enqueue(
            SyntheticSession(), runtime=runtime, scope=UploadScope(11, 23),
            candidate=UploadCandidate("contract.txt", "text/plain", b"changed"),
            request_key="request-1", index=0,
        )


def test_worker_verifies_binding_processes_and_deletes_after_durable_finalize(tmp_path, monkeypatch):
    processor = SyntheticProcessor()
    runtime, lifecycle, captured, queued, secret = stage_secret(
        tmp_path, monkeypatch, processor=processor,
    )
    install_claim(monkeypatch)
    row = lifecycle.rows[queued.staging_id]
    record = lifecycle.load_for_processing(
        object(), staging_id=queued.staging_id, job_id=queued.job_id,
    )
    result = run_local_upload_job(job_payload(captured))
    assert result == {
        "staging_id": queued.staging_id, "processed": 1, "skipped": 0,
        "tasks": 2, "risks": 0, "decisions": 1, "drafts": 0,
        "documents": [17],
    }
    assert processor.calls[0][1] == secret
    assert processor.calls[0][2] == f"local-upload:{queued.staging_id}"
    assert lifecycle.finalized == [(queued.staging_id, "completed")]
    assert not list(tmp_path.rglob("*.enc"))
    assert cleanup_finalized_upload(runtime, record, CleanupDecision(True)) is True


def test_worker_detects_ciphertext_tamper_retains_for_failure_policy_and_logs_no_content(
    tmp_path, monkeypatch, caplog,
):
    runtime, lifecycle, captured, queued, secret = stage_secret(tmp_path, monkeypatch)
    install_claim(monkeypatch)
    encrypted = next(tmp_path.rglob("*.enc"))
    data = bytearray(encrypted.read_bytes())
    data[-1] ^= 1
    encrypted.write_bytes(data)
    with caplog.at_level(logging.WARNING, logger="pu.local_upload_staging"):
        with pytest.raises(StagingIntegrityError):
            run_local_upload_job(job_payload(captured))
    assert lifecycle.finalized == [(queued.staging_id, "failed")]
    assert encrypted.exists()
    assert secret.decode() not in caplog.text
    assert "contract.txt" not in caplog.text
    assert "processing_failure" in caplog.text


def test_worker_rejects_tampered_lifecycle_metadata_before_plaintext_read(tmp_path, monkeypatch):
    processor = SyntheticProcessor()
    _, lifecycle, captured, queued, _ = stage_secret(tmp_path, monkeypatch, processor=processor)
    install_claim(monkeypatch)
    lifecycle.rows[queued.staging_id]["display_name"] = "C:/private/report.txt"
    with pytest.raises(LocalUploadConflict, match="materialization_binding_conflict"):
        run_local_upload_job(job_payload(captured))
    assert not processor.calls
    assert not lifecycle.finalized


def test_worker_rejects_result_that_could_persist_content_or_keys(tmp_path, monkeypatch, caplog):
    unsafe = SyntheticProcessor(result={
        "processed": 1, "skipped": 0, "tasks": 0, "risks": 0,
        "decisions": 0, "drafts": 0, "documents": [], "content": "secret",
    })
    _, lifecycle, captured, queued, _ = stage_secret(tmp_path, monkeypatch, processor=unsafe)
    install_claim(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="pu.local_upload_staging"):
        with pytest.raises(local_staging.LocalUploadStagingError, match="unsafe_processor_result"):
            run_local_upload_job(job_payload(captured))
    assert lifecycle.finalized == [(queued.staging_id, "failed")]
    assert "secret" not in caplog.text


def test_cancellation_finalizes_and_cleans_ciphertext(tmp_path, monkeypatch):
    processor = SyntheticProcessor(error=LocalUploadCancelled("cancelled"))
    _, lifecycle, captured, queued, _ = stage_secret(tmp_path, monkeypatch, processor=processor)
    lifecycle.cleanup_by_outcome["cancelled"] = CleanupDecision(True)
    install_claim(monkeypatch)
    assert run_local_upload_job(job_payload(captured)) == {
        "cancelled": True, "staging_id": queued.staging_id,
    }
    assert lifecycle.finalized == [(queued.staging_id, "cancelled")]
    assert not list(tmp_path.rglob("*.enc"))


def test_handler_requires_real_queue_claim_and_exact_opaque_payload(tmp_path, monkeypatch):
    _, _, captured, _, _ = stage_secret(tmp_path, monkeypatch)
    monkeypatch.setattr(local_staging, "current_execution_claim", lambda: None)
    with pytest.raises(local_staging.LocalUploadUnavailable, match="missing_job_claim"):
        run_local_upload_job(job_payload(captured))
    install_claim(monkeypatch)
    with pytest.raises(LocalUploadAdmissionDenied, match="unsafe_job_payload"):
        run_local_upload_job({**job_payload(captured), "path": "private/file.txt"})


def test_future_a05_adapter_fails_closed_for_missing_partial_and_throwing_backends(
    caplog,
):
    secret = "SECRET filename=C:/private/report.txt key=raw"

    class ThrowingBackend:
        def reserve(self, *args, **kwargs):
            raise RuntimeError(secret)

    for backend in (None, object(), ThrowingBackend()):
        adapter = LocalUploadLifecycleAdapter(backend)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(
                LocalUploadUnavailable, match="^local_upload_lifecycle_unavailable$",
            ):
                adapter.reserve(object())
    assert secret not in caplog.text
    assert "private/report.txt" not in caplog.text
    assert "key=raw" not in caplog.text


def test_future_a05_adapter_rejects_malformed_results_and_cleanup_decisions():
    class MalformedReservationBackend:
        def reserve(self, *args, **kwargs):
            return {"staging_id": "0" * 32}

    with pytest.raises(LocalUploadUnavailable, match="local_upload_lifecycle_unavailable"):
        LocalUploadLifecycleAdapter(MalformedReservationBackend()).reserve(object())

    class MalformedCleanupBackend:
        def finalize(self, *args, **kwargs):
            return CleanupDecision(delete_ciphertext=1)

    with pytest.raises(LocalUploadUnavailable, match="local_upload_lifecycle_unavailable"):
        LocalUploadLifecycleAdapter(MalformedCleanupBackend()).finalize(object())


def test_runtime_rejects_unadapted_lifecycle(tmp_path):
    with pytest.raises(TypeError, match="invalid_lifecycle_port"):
        LocalUploadRuntime(
            storage=FilesystemStagingStorage(tmp_path, StaticResolver(), chunk_size=32),
            lifecycle=SyntheticLifecycle(),
            processor=SyntheticProcessor(), session_factory=lambda: nullcontext(SyntheticSession()),
            kek=KekRef("local-upload", "v1"), max_file_bytes=1024,
        )


def test_job_binding_commit_failure_is_stable_and_rolls_back(tmp_path, monkeypatch, caplog):
    lifecycle = SyntheticLifecycle()
    runtime = make_runtime(tmp_path, lifecycle)
    captured = []
    enqueue_once(monkeypatch, captured)
    session = SyntheticSession(fail_commit=3)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(
            LocalUploadUnavailable, match="^local_upload_job_binding_unavailable$",
        ):
            stage_and_enqueue(
                session, runtime=runtime, scope=UploadScope(11, 23),
                candidate=UploadCandidate("private.txt", "text/plain", b"secret body"),
                request_key="request-commit", index=0,
            )
    assert session.events == ["commit", "commit", "commit", "rollback"]
    assert "private.txt" not in caplog.text
    assert "secret body" not in caplog.text


def test_queue_failure_is_content_free_and_does_not_bind_job(tmp_path, monkeypatch, caplog):
    lifecycle = SyntheticLifecycle()
    runtime = make_runtime(tmp_path, lifecycle)
    secret = "SECRET body path=C:/private/report.txt key=raw"

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(local_staging, "enqueue", fail_enqueue)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(LocalUploadUnavailable, match="^local_upload_queue_unavailable$"):
            stage_and_enqueue(
                SyntheticSession(), runtime=runtime, scope=UploadScope(11, 23),
                candidate=UploadCandidate("report.txt", "text/plain", secret.encode()),
                request_key="request-queue-failure", index=0,
            )
    row = next(iter(lifecycle.rows.values()))
    assert row.get("job_id") is None
    assert secret not in caplog.text
    assert "private/report.txt" not in caplog.text


def test_finalization_is_committed_before_ciphertext_cleanup(tmp_path, monkeypatch):
    events = []
    session = SyntheticSession(events)
    lifecycle = SyntheticLifecycle()
    runtime = make_runtime(tmp_path, lifecycle, session=session)
    monkeypatch.setattr(local_staging, "_runtime", runtime)
    original_delete = runtime.storage.delete

    def observed_delete(object_id):
        events.append("delete")
        return original_delete(object_id)

    monkeypatch.setattr(runtime.storage, "delete", observed_delete)
    captured = []
    enqueue_once(monkeypatch, captured)
    secret = b"commit-before-delete"
    queued = stage_and_enqueue(
        SyntheticSession(), runtime=runtime, scope=UploadScope(11, 23),
        candidate=UploadCandidate("proof.txt", "text/plain", secret),
        request_key="request-order", index=0,
    )
    install_claim(monkeypatch, queued.job_id)
    run_local_upload_job(job_payload(captured))
    assert events == ["commit", "commit", "commit", "delete", "commit"]


def test_worker_rejects_descriptor_key_rebinding_before_decrypt(tmp_path, monkeypatch):
    processor = SyntheticProcessor()
    _, lifecycle, captured, queued, _ = stage_secret(tmp_path, monkeypatch, processor=processor)
    install_claim(monkeypatch)
    row = lifecycle.rows[queued.staging_id]
    row["descriptor"] = replace(row["descriptor"], kek=KekRef("unexpected", "v2"))
    with pytest.raises(LocalUploadConflict, match="materialization_binding_conflict"):
        run_local_upload_job(job_payload(captured))
    assert not processor.calls
    assert not lifecycle.finalized


def test_api_authorizes_before_decode_or_staging(monkeypatch):
    batch = local_api.LocalBatch(
        project_id=23,
        files=[local_api.LocalFile(path="private.txt", content_base64="%%%")],
    )
    marker = RuntimeError("denied")
    monkeypatch.setattr(local_api, "require_project_role", lambda *args: (_ for _ in ()).throw(marker))
    monkeypatch.setattr(
        local_api, "get_local_upload_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("staging touched before authorization")),
    )
    with pytest.raises(RuntimeError, match="denied"):
        local_api.analyze_local_folder(batch, db=object(), user=SimpleNamespace(id=11))


def test_api_admits_whole_batch_and_returns_only_opaque_job_references(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    admitted = []
    staged = []
    monkeypatch.setattr(local_api, "require_project_role", lambda *args: None)
    monkeypatch.setattr(local_api, "get_local_upload_runtime", lambda: runtime)

    def fake_stage(session, *, runtime, scope, candidate, request_key, index):
        admitted.append(candidate)
        staged.append((scope, request_key, index))
        return EnqueuedUpload(f"{index + 1:032x}", index + 1, "queued")

    monkeypatch.setattr(local_api, "stage_and_enqueue", fake_stage)
    batch = local_api.LocalBatch(project_id=23, files=[
        local_api.LocalFile(
            path=r"C:\private\first.txt", mime_type="text/plain",
            content_base64=base64.b64encode(b"first body").decode("ascii"),
        ),
        local_api.LocalFile(
            path="folder/second.pdf", mime_type="application/pdf",
            content_base64=base64.b64encode(b"%PDF synthetic").decode("ascii"),
        ),
    ])
    response = local_api.analyze_local_folder(
        batch, db=object(), user=SimpleNamespace(id=11), idempotency_key="client-request",
    )
    assert [item.display_name for item in admitted] == ["first.txt", "second.pdf"]
    assert staged == [
        (UploadScope(11, 23), "client-request", 0),
        (UploadScope(11, 23), "client-request", 1),
    ]
    assert response["status"] == "queued"
    assert response["jobs"] == [
        {"job_id": 1, "staging_id": f"{1:032x}", "status": "queued"},
        {"job_id": 2, "staging_id": f"{2:032x}", "status": "queued"},
    ]
    serialized = repr(response)
    assert "first body" not in serialized
    assert "private" not in serialized
    assert "client-request" not in serialized


def test_api_rejects_invalid_base64_size_and_mime_with_stable_errors(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    monkeypatch.setattr(local_api, "require_project_role", lambda *args: None)
    monkeypatch.setattr(local_api, "get_local_upload_runtime", lambda: runtime)
    invalid = local_api.LocalBatch(
        project_id=23, files=[local_api.LocalFile(path="secret.txt", content_base64="%%%")],
    )
    with pytest.raises(HTTPException) as error:
        local_api.analyze_local_folder(invalid, db=object(), user=SimpleNamespace(id=11))
    assert (error.value.status_code, error.value.detail) == (422, "invalid_file_content")

    unsupported = local_api.LocalBatch(project_id=23, files=[local_api.LocalFile(
        path="secret.exe", mime_type="application/octet-stream",
        content_base64=base64.b64encode(b"binary").decode("ascii"),
    )])
    with pytest.raises(HTTPException) as error:
        local_api.analyze_local_folder(unsupported, db=object(), user=SimpleNamespace(id=11))
    assert (error.value.status_code, error.value.detail) == (422, "unsupported_mime_type")


def test_api_rejects_whole_batch_before_decode_or_staging(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    monkeypatch.setattr(local_api, "require_project_role", lambda *args: None)
    monkeypatch.setattr(local_api, "get_local_upload_runtime", lambda: runtime)
    monkeypatch.setattr(local_api, "MAX_BATCH_BYTES", 5)
    monkeypatch.setattr(
        local_api, "stage_and_enqueue",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("staging touched")),
    )
    batch = local_api.LocalBatch(project_id=23, files=[
        local_api.LocalFile(path="one.txt", content_base64=base64.b64encode(b"abc").decode()),
        local_api.LocalFile(path="two.txt", content_base64=base64.b64encode(b"def").decode()),
    ])
    with pytest.raises(HTTPException) as error:
        local_api.analyze_local_folder(batch, db=object(), user=SimpleNamespace(id=11))
    assert (error.value.status_code, error.value.detail) == (422, "batch_too_large")


@pytest.mark.parametrize(
    ("raised", "expected_detail"),
    [
        (LocalUploadUnavailable("local_upload_lifecycle_unavailable"),
         "local_upload_lifecycle_unavailable"),
        (local_staging.LocalUploadStagingError("SECRET path=C:/private key=raw"),
         "local_upload_staging_unavailable"),
    ],
)
def test_api_maps_infrastructure_failures_to_content_free_503(
    tmp_path, monkeypatch, raised, expected_detail,
):
    runtime = make_runtime(tmp_path)
    monkeypatch.setattr(local_api, "require_project_role", lambda *args: None)
    monkeypatch.setattr(local_api, "get_local_upload_runtime", lambda: runtime)
    monkeypatch.setattr(
        local_api, "stage_and_enqueue",
        lambda *args, **kwargs: (_ for _ in ()).throw(raised),
    )
    batch = local_api.LocalBatch(project_id=23, files=[local_api.LocalFile(
        path="secret.txt", mime_type="text/plain",
        content_base64=base64.b64encode(b"secret body").decode(),
    )])
    with pytest.raises(HTTPException) as error:
        local_api.analyze_local_folder(batch, db=object(), user=SimpleNamespace(id=11))
    assert (error.value.status_code, error.value.detail) == (503, expected_detail)
    assert "private" not in error.value.detail
    assert "key=raw" not in error.value.detail


def test_api_does_not_echo_unexpected_value_error_text(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    monkeypatch.setattr(local_api, "require_project_role", lambda *args: None)
    monkeypatch.setattr(local_api, "get_local_upload_runtime", lambda: runtime)
    monkeypatch.setattr(
        local_api, "stage_and_enqueue",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("SECRET path=C:/private/report.txt key=raw"),
        ),
    )
    batch = local_api.LocalBatch(project_id=23, files=[local_api.LocalFile(
        path="secret.txt", mime_type="text/plain",
        content_base64=base64.b64encode(b"secret body").decode(),
    )])
    with pytest.raises(HTTPException) as error:
        local_api.analyze_local_folder(batch, db=object(), user=SimpleNamespace(id=11))
    assert (error.value.status_code, error.value.detail) == (422, "invalid_file_content")


def test_business_processor_has_no_external_notification_or_provider_side_effect():
    import inspect

    source = inspect.getsource(LocalUploadBusinessProcessor.process)
    assert "notify_telegram" not in source
    assert "integrations" not in source
    assert "content_base64" not in source
