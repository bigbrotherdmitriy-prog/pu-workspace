"""Independent bounded-wait and evidence edge cases; no network or Docker runs."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "readiness_diagnostics_edges", ROOT / "scripts/verify_image_static.py"
)
verifier = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(verifier)
REVISION = "a" * 40
IMAGE = "sha256:" + "b" * 64
ORIGIN = "https://example.test"
SECRET_SENTINEL = "TEST_SECRET_MUST_NOT_APPEAR_IN_DIAGNOSTICS"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration


@pytest.fixture
def clock(monkeypatch):
    value = FakeClock()
    # Replace only this module's clock, not pytest's process-wide time module.
    monkeypatch.setattr(verifier, "time", value)
    return value


class Response(io.BytesIO):
    def __init__(self, body, url, *, clock=None, read_delay=0):
        super().__init__(body)
        self.status = 200
        self.url = url
        self.headers = {"Content-Type": "application/javascript"}
        self.clock = clock
        self.read_delay = read_delay

    def geturl(self):
        return self.url

    def read1(self, size=-1):
        data = super().read1(size)
        if data and self.clock is not None:
            self.clock.now += self.read_delay
        return data


class Client:
    def __init__(self, bodies, *, clock=None, delays=None):
        self.bodies = iter(bodies)
        self.clock = clock
        self.delays = iter(delays or [])
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        body = next(self.bodies)
        raw = json.dumps(body).encode() if isinstance(body, dict) else body
        return Response(
            raw, request.full_url, clock=self.clock,
            read_delay=next(self.delays, 0),
        )


def status(**extras):
    return {"status": "ok", "release": REVISION, **extras}


def readiness(*failed, ready=None):
    checks = {
        name: {"ok": name not in failed, "required": True}
        for name in (
            "app_secret", "bootstrap_token", "token_encryption", "database",
            "schema", "durable_workers", "durable_scheduler",
        )
    }
    checks["dead_letter_queue"] = {"ok": False, "required": False}
    for name in failed:
        checks.setdefault(name, {"ok": False, "required": True})
    return {"ready": not failed if ready is None else ready, "checks": checks}


@pytest.mark.parametrize(
    "options",
    [
        {"attempts": 0}, {"attempts": 26}, {"attempts": True},
        {"attempts": 1.0}, {"interval": -0.01},
        {"interval": float("nan")}, {"interval": float("inf")},
        {"interval": float("-inf")}, {"wait_timeout": 0},
        {"wait_timeout": 181}, {"wait_timeout": float("nan")},
        {"wait_timeout": float("inf")}, {"wait_timeout": float("-inf")},
    ],
)
def test_wait_rejects_unbounded_or_invalid_limits_before_http(clock, options):
    client = Client([])
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness(ORIGIN, REVISION, opener=client, **options)
    assert error.value.code == "readiness_wait_invalid"
    assert client.calls == []
    assert clock.sleeps == []


def test_one_attempt_heartbeat_failure_never_sleeps_or_retries(clock):
    client = Client([status(), readiness("durable_workers")])
    evidence = []
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness(
            ORIGIN, REVISION, attempts=1, opener=client, diagnostics=evidence,
        )
    assert error.value.code == "readiness_wait_exhausted"
    assert len(client.calls) == 2
    assert clock.sleeps == []
    assert evidence[0]["failed_required"] == ["durable_workers"]
    assert evidence[0]["reason_code"] == "readiness_wait_exhausted"


def test_ready_body_arriving_after_deadline_is_not_accepted(clock):
    client = Client(
        [status(), readiness()], clock=clock, delays=[0, 11],
    )
    evidence = []
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness(
            ORIGIN, REVISION, wait_timeout=10,
            opener=client, diagnostics=evidence,
        )
    assert error.value.code == "readiness_wait_timeout"
    assert len(client.calls) == 2
    assert clock.now == 11
    assert clock.sleeps == []
    assert evidence[-1]["reason_code"] == "readiness_wait_timeout"


def test_each_http_timeout_is_shortened_to_remaining_wall_budget(clock):
    client = Client([status(), readiness()], clock=clock, delays=[7, 0])
    result = verifier.verify_readiness(
        ORIGIN, REVISION, wait_timeout=10, opener=client,
    )
    assert result["api/readiness"]["ready"] is True
    assert [timeout for _, timeout in client.calls] == [10, 3]
    assert clock.sleeps == []


def test_sleep_cannot_exceed_remaining_deadline_or_issue_late_http(clock):
    client = Client([status(), readiness("durable_scheduler")])
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness(
            ORIGIN, REVISION, interval=5, wait_timeout=2, opener=client,
        )
    assert error.value.code == "readiness_wait_timeout"
    assert clock.sleeps == [2]
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "other_failure", ["schema", "database", "app_secret", "token_encryption", "future_required"]
)
def test_mixed_required_failure_cannot_be_waited_away(clock, other_failure):
    client = Client([
        status(), readiness("durable_workers", other_failure),
        status(), readiness(),
    ])
    evidence = []
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness(ORIGIN, REVISION, opener=client, diagnostics=evidence)
    assert error.value.code == "readiness_required_failed"
    assert len(client.calls) == 2
    assert len(evidence) == 1
    assert clock.sleeps == []
    assert evidence[0]["unknown_failed_required_count"] == int(other_failure == "future_required")


def test_inconsistent_false_summary_with_no_failed_required_is_not_retried(clock):
    client = Client([status(), readiness(ready=False)])
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness(ORIGIN, REVISION, opener=client)
    assert error.value.code == "readiness_required_failed"
    assert len(client.calls) == 2
    assert clock.sleeps == []


def test_inconsistent_true_summary_with_failed_required_is_not_retried(clock):
    client = Client([status(), readiness("durable_workers", ready=True)])
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness(ORIGIN, REVISION, opener=client)
    assert error.value.code == "readiness_required_failed"
    assert len(client.calls) == 2
    assert clock.sleeps == []


def test_backend_sha_is_rechecked_before_each_readiness_attempt(clock):
    client = Client([
        status(), readiness("durable_workers"),
        {"status": "ok", "release": "c" * 40}, readiness(),
    ])
    evidence = []
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_readiness(
            ORIGIN, REVISION, interval=1, opener=client, diagnostics=evidence,
        )
    assert error.value.code == "release_mismatch"
    assert [url for url, _ in client.calls] == [
        ORIGIN + "/api/status", ORIGIN + "/api/readiness", ORIGIN + "/api/status",
    ]
    assert clock.sleeps == [1]
    assert evidence[-1]["reason_code"] == "release_mismatch"


def test_readiness_evidence_filters_api_extras_and_check_messages(clock):
    body = readiness("durable_workers")
    body["token"] = SECRET_SENTINEL
    body["checks"]["database"]["message"] = "postgres://user:" + SECRET_SENTINEL
    body["checks"][SECRET_SENTINEL] = {"ok": False, "required": True}
    client = Client([status(Authorization=SECRET_SENTINEL), body])
    evidence = []
    with pytest.raises(verifier.GateFailure):
        verifier.verify_readiness(ORIGIN, REVISION, opener=client, diagnostics=evidence)
    serialized = json.dumps(evidence)
    assert SECRET_SENTINEL not in serialized
    assert "postgres://" not in serialized
    assert evidence[0]["unknown_failed_required_count"] == 1
    assert evidence[0]["requests"][0]["body"]["parsed"] == status()
    assert evidence[0]["requests"][1]["body"]["parsed"]["checks"]["database"] == {
        "ok": True, "required": True,
    }


def test_bounded_non_json_diagnostics_never_store_raw_secret_bytes():
    raw = (SECRET_SENTINEL.encode() + b"<html>") * (verifier.BODY_LIMIT // 10)
    result = verifier.safe_api_body(raw)
    assert result["bytes_read"] == len(raw)
    assert result["truncated"] is True
    assert result["sha256"] == hashlib.sha256(raw[:verifier.BODY_LIMIT]).hexdigest()
    assert result["format"] == "non_json"
    assert SECRET_SENTINEL not in json.dumps(result)


class InterruptedResponse(Response):
    def __init__(self, url):
        super().__init__(b"", url)
        self.read_count = 0

    def read(self, size=-1):
        self.read_count += 1
        if self.read_count == 1:
            return b"JS"
        raise TimeoutError("socket with " + SECRET_SENTINEL)


def static_entry(path, body):
    return {"path": path, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def test_interrupted_static_stream_records_partial_hash_not_complete_hash(clock):
    client = Mock()
    client.open.return_value = InterruptedResponse(ORIGIN + "/new/assets/main.js")
    evidence = {}
    with pytest.raises(TimeoutError):
        verifier.verify_http(
            [static_entry("assets/main.js", b"JScript")], ORIGIN,
            opener=client, diagnostics=evidence,
        )
    observed = evidence["files"][0]
    assert observed["actual_size"] == 2
    assert observed["partial_sha256"] == hashlib.sha256(b"JS").hexdigest()
    assert observed["actual_sha256"] is None
    assert observed["complete_body"] is False
    assert observed["verified"] is False
    assert evidence["files_checked"] == 0
    assert evidence["state"] == "partial"
    assert SECRET_SENTINEL not in json.dumps(evidence)
    assert client.open.call_count == 1


def test_oversized_static_body_records_all_received_bytes_without_retry(clock):
    client = Client([b"JSX"])
    evidence = {}
    with pytest.raises(verifier.GateFailure) as error:
        verifier.verify_http(
            [static_entry("assets/main.js", b"JS")], ORIGIN,
            opener=client, diagnostics=evidence,
        )
    assert error.value.code == "static_size_mismatch"
    observed = evidence["files"][0]
    assert observed["actual_size"] == 3
    assert observed["partial_sha256"] == hashlib.sha256(b"JSX").hexdigest()
    assert observed["actual_sha256"] is None
    assert observed["complete_body"] is False
    assert observed["verified"] is False
    assert len(client.calls) == 1


def public_arguments(receipt):
    return [
        "--image", IMAGE, "--expected-image-id", IMAGE, "--revision", REVISION,
        "--archive-sha256", "d" * 64, "--compose-project", "puw-primary-next",
        "--base-url", ORIGIN, "--receipt", str(receipt),
    ]


def component_details():
    return [
        {
            "Id": str(index), "Image": IMAGE, "State": {"Running": True},
            "Config": {
                "Labels": {"com.docker.compose.service": service},
                "Env": [
                    "PU_RELEASE_REVISION=" + REVISION, "GMAIL_AUTO_SYNC_ENABLED=true",
                    "APP_SECRET_KEY=" + SECRET_SENTINEL,
                ],
            },
        }
        for index, service in enumerate(("backend", "worker", "worker", "scheduler"))
    ]


def test_component_image_changed_during_wait_is_rejected_before_any_static(
    tmp_path, monkeypatch, clock, capsys,
):
    inspected = []

    def docker(*args):
        if args[0] == "ps":
            return "0\n1\n2\n3"
        assert args[0] == "inspect"
        details = component_details()
        if inspected:
            details[1]["Image"] = "sha256:" + "c" * 64
        inspected.append(details)
        return json.dumps(details)

    monkeypatch.setattr(verifier, "docker", docker)
    monkeypatch.setattr(
        verifier, "image_identity", lambda *args: {"image_id": IMAGE, "revision": REVISION},
    )
    monkeypatch.setattr(
        verifier, "verify_readiness",
        lambda *args, **kwargs: {"api/status": status(), "api/readiness": readiness()},
    )
    frontend, static = Mock(), Mock()
    monkeypatch.setattr(verifier, "inspect_frontend", frontend)
    monkeypatch.setattr(verifier, "verify_http", static)
    receipt = tmp_path / "failure.json"
    assert verifier.main(public_arguments(receipt)) == 1
    persisted = json.loads(receipt.read_text())
    assert len(inspected) == 2
    assert persisted["verified"] is False
    assert persisted["stage"] == "components_after_readiness"
    assert persisted["component_checks"][-1]["containers"][1]["image_matches"] is False
    assert persisted["static"]["state"] == "not_started"
    frontend.assert_not_called()
    static.assert_not_called()
    assert SECRET_SENTINEL not in receipt.read_text()
    output = capsys.readouterr()
    assert SECRET_SENTINEL not in output.out + output.err


def test_success_receipt_and_stdout_filter_extra_api_secrets(
    tmp_path, monkeypatch, clock, capsys,
):
    body = readiness()
    body["password"] = SECRET_SENTINEL
    body["checks"]["database"]["message"] = SECRET_SENTINEL
    monkeypatch.setattr(
        verifier, "image_identity", lambda *args: {"image_id": IMAGE, "revision": REVISION},
    )
    monkeypatch.setattr(
        verifier, "verify_components", lambda *args, **kwargs: {"backend": ["0"], "worker": ["1", "2"], "scheduler": ["3"]},
    )
    monkeypatch.setattr(
        verifier, "verify_readiness",
        lambda *args, **kwargs: {"api/status": status(token=SECRET_SENTINEL), "api/readiness": body},
    )
    file = static_entry("assets/main.js", b"JS")
    monkeypatch.setattr(
        verifier, "inspect_frontend", lambda *args, **kwargs: ({"files": [file]}, "e" * 64),
    )

    def verify_http(files, *args, diagnostics, **kwargs):
        diagnostics.update({"state": "complete", "files_expected": 1, "files_checked": 1, "files": []})
        return [{**file, "status": 200}]

    monkeypatch.setattr(verifier, "verify_http", verify_http)
    receipt = tmp_path / "success.json"
    assert verifier.main(public_arguments(receipt)) == 0
    persisted = json.loads(receipt.read_text())
    assert persisted["verified"] is True
    assert persisted["readiness"]["api/status"] == status()
    assert persisted["readiness"]["api/readiness"]["checks"]["database"] == {"ok": True, "required": True}
    assert SECRET_SENTINEL not in receipt.read_text()
    output = capsys.readouterr()
    assert SECRET_SENTINEL not in output.out + output.err
