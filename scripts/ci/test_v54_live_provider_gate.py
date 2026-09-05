from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/ci/v54_live_provider_gate.py"
SPEC = importlib.util.spec_from_file_location("v54_live_provider_gate", MODULE_PATH)
gate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid_env() -> dict[str, str]:
    host = "provider-sandbox.example.test"
    return {
        "PUW_V54_LIVE_PROVIDER_ENABLE": gate.ENABLE_ACK,
        "PUW_V54_LIVE_PROVIDER_BASE_URL": f"https://{host}/bridge",
        "PUW_V54_LIVE_PROVIDER_TOKEN": "t" * 32,
        "PUW_V54_LIVE_PROVIDER_ACCOUNT_FINGERPRINT": "a" * 64,
        "PUW_V54_LIVE_PROVIDER_EXPECTED_HOST_SHA256": digest(host),
        "GITHUB_RUN_ID": "100",
        "GITHUB_RUN_ATTEMPT": "2",
    }


class Adapter:
    def __init__(self, config, *, lookup=None, dispatch_timeout=True, cleanup=True, raw_error=None):
        self.config = config
        self.lookup_values = list(lookup or [])
        self.dispatch_timeout = dispatch_timeout
        self.cleanup_ok = cleanup
        self.raw_error = raw_error
        self.dispatches = 0
        self.lookups = 0
        self.cleanups = 0
        self.command = None

    def attest(self):
        return {
            "schema": "puw.v54.live-provider.capabilities.v1",
            "environment": "ephemeral-test",
            "effect_class": "sink-only",
            "address_policy": "no-external-delivery",
            "cleanup": "supported",
            "fault": "timeout-after-effect",
            "account_fingerprint": self.config.account_fingerprint,
            "run_nonce": self.config.run_nonce,
        }

    def dispatch_timeout_after_effect(self, command):
        self.dispatches += 1
        self.command = dict(command)
        if self.raw_error:
            raise RuntimeError(self.raw_error)
        if self.dispatch_timeout:
            raise gate.ExpectedTimeoutAfterEffect()

    def lookup(self, command):
        self.lookups += 1
        assert command == self.command
        if self.lookup_values:
            value = self.lookup_values.pop(0)
            if value == "applied":
                return {
                    "outcome": "APPLIED",
                    "observed_effects": 1,
                    **dict(command),
                }
            return value
        return {"outcome": "UNKNOWN", "observed_effects": 0}

    def cleanup(self, command):
        self.cleanups += 1
        assert command == self.command
        if not self.cleanup_ok:
            raise RuntimeError("raw cleanup secret")


def configuration(env=None):
    config, reason = gate.config_from_env(env or valid_env())
    assert reason is None and config is not None
    return config


def test_default_is_not_run_and_never_constructs_network_adapter(tmp_path, monkeypatch):
    class Forbidden:
        def __init__(self, _config):
            raise AssertionError("network adapter must not be constructed")

    monkeypatch.setattr(gate, "HttpSandboxAdapter", Forbidden)
    target = tmp_path / "protocol.json"
    assert gate.main({}, target) == 0
    assert json.loads(target.read_text())["status"] == "NOT_RUN"
    assert json.loads(target.read_text())["reason_code"] == "disabled"


def test_explicit_enable_without_all_secrets_is_not_run(tmp_path):
    target = tmp_path / "protocol.json"
    assert gate.main({"PUW_V54_LIVE_PROVIDER_ENABLE": gate.ENABLE_ACK}, target) == 0
    protocol = json.loads(target.read_text())
    assert protocol["status"] == "NOT_RUN"
    assert protocol["reason_code"] == "test_secrets_missing"
    assert protocol["observed_effects"] == 0


@pytest.mark.parametrize("url", [
    "http://provider-sandbox.example.test",
    "https://production.example.test",
    "https://127.0.0.1",
    "https://localhost",
    "https://provider.example.com",
    "https://user:password@provider-sandbox.example.test",
    "https://provider-sandbox.example.test?token=secret",
])
def test_unsafe_or_production_like_endpoints_fail_closed(url, tmp_path):
    env = valid_env()
    env["PUW_V54_LIVE_PROVIDER_BASE_URL"] = url
    target = tmp_path / "protocol.json"
    assert gate.main(env, target) == 1
    protocol = json.loads(target.read_text())
    assert protocol["status"] == "FAIL"
    assert protocol["reason_code"] == "unsafe_endpoint"


@pytest.mark.parametrize("key", sorted(gate.FORBIDDEN_ADDRESS_KEYS))
def test_addresses_are_not_accepted_from_ci(key, tmp_path):
    env = valid_env()
    env[key] = "customer@example.com"
    target = tmp_path / "protocol.json"
    assert gate.main(env, target) == 1
    assert json.loads(target.read_text())["reason_code"] == "address_input_forbidden"


@pytest.mark.parametrize(("key", "value"), [
    ("PUW_V54_LIVE_PROVIDER_DISPATCH_TIMEOUT", "0"),
    ("PUW_V54_LIVE_PROVIDER_DISPATCH_TIMEOUT", "nan"),
    ("PUW_V54_LIVE_PROVIDER_LOOKUP_ATTEMPTS", "0"),
    ("PUW_V54_LIVE_PROVIDER_LOOKUP_ATTEMPTS", "100"),
    ("PUW_V54_LIVE_PROVIDER_LOOKUP_DELAY", "-1"),
])
def test_retry_and_timeout_limits_fail_closed(key, value, tmp_path):
    env = valid_env()
    env[key] = value
    target = tmp_path / "protocol.json"
    assert gate.main(env, target) == 1
    assert json.loads(target.read_text())["reason_code"] == "protocol_invalid"


def test_timeout_after_effect_reconciles_once_without_redispatch():
    config = configuration()
    adapter = Adapter(config, lookup=[
        {"outcome": "UNKNOWN", "observed_effects": 0},
        "applied",
    ])
    sleeps = []
    protocol = gate.run_gate(config, adapter, sleep=sleeps.append)
    assert protocol == {
        "schema": gate.SCHEMA,
        "status": "PASS",
        "reason_code": None,
        "provider_scope": "ephemeral-test-sink-only",
        "dispatch_attempts": 1,
        "lookup_attempts": 2,
        "observed_effects": 1,
        "timeout_after_effect_observed": True,
        "reconciliation": "PASS",
        "cleanup": "PASS",
        "raw_output_published": False,
    }
    assert adapter.dispatches == 1
    assert adapter.lookups == 2
    assert adapter.cleanups == 1
    assert sleeps == [config.lookup_delay_seconds]


def test_two_observed_effects_fail_and_cleanup_still_runs():
    config = configuration()
    adapter = Adapter(config)

    def duplicated(command):
        adapter.lookups += 1
        return {"outcome": "APPLIED", "observed_effects": 2, **dict(command)}

    adapter.lookup = duplicated
    protocol = gate.run_gate(config, adapter, sleep=lambda _seconds: None)
    assert protocol["status"] == "FAIL"
    assert protocol["reason_code"] == "effect_count_invalid"
    assert adapter.dispatches == 1
    assert adapter.cleanups == 1


def test_unexpected_dispatch_response_is_not_treated_as_timeout():
    config = configuration()
    adapter = Adapter(config, dispatch_timeout=False)
    protocol = gate.run_gate(config, adapter)
    assert protocol["status"] == "FAIL"
    assert protocol["reason_code"] == "dispatch_did_not_timeout"
    assert adapter.dispatches == 1
    assert adapter.lookups == 0
    assert adapter.cleanups == 1


def test_lookup_exhaustion_never_causes_second_dispatch():
    config = configuration()
    adapter = Adapter(config)
    protocol = gate.run_gate(config, adapter, sleep=lambda _seconds: None)
    assert protocol["status"] == "FAIL"
    assert protocol["reason_code"] == "lookup_exhausted"
    assert adapter.dispatches == 1
    assert adapter.lookups == config.lookup_attempts
    assert adapter.cleanups == 1


def test_cleanup_failure_overrides_pass():
    config = configuration()
    adapter = Adapter(config, lookup=["applied"], cleanup=False)
    protocol = gate.run_gate(config, adapter)
    assert protocol["status"] == "FAIL"
    assert protocol["reason_code"] == "cleanup_failed"
    assert protocol["cleanup"] == "FAIL"


def test_attestation_mismatch_prevents_dispatch_and_cleanup():
    config = configuration()
    adapter = Adapter(config)
    adapter.attest = lambda: {"environment": "production"}
    protocol = gate.run_gate(config, adapter)
    assert protocol["reason_code"] == "attestation_invalid"
    assert adapter.dispatches == adapter.lookups == adapter.cleanups == 0


def test_raw_exception_and_secrets_never_enter_protocol(tmp_path, monkeypatch):
    env = valid_env()
    raw = "postgresql://user:password@db customer@example.com " + env["PUW_V54_LIVE_PROVIDER_TOKEN"]
    config = configuration(env)
    monkeypatch.setattr(gate, "HttpSandboxAdapter", lambda _config: Adapter(config, raw_error=raw))
    target = tmp_path / "protocol.json"
    assert gate.main(env, target) == 1
    encoded = target.read_text()
    assert raw not in encoded
    assert env["PUW_V54_LIVE_PROVIDER_TOKEN"] not in encoded
    assert "customer@example.com" not in encoded
    assert "postgresql://" not in encoded
    assert json.loads(encoded)["reason_code"] == "sandbox_request_failed"


def test_command_contains_only_content_free_allowlisted_fields():
    config = configuration()
    adapter = Adapter(config, lookup=["applied"])
    assert gate.run_gate(config, adapter)["status"] == "PASS"
    assert set(adapter.command) == {
        "action_id", "command_key", "idempotency_key", "payload_hash",
        "account_fingerprint", "run_nonce",
    }
    assert all(SHA == 64 for SHA in map(len, adapter.command.values()))


def test_workflow_is_manual_default_off_and_content_free():
    workflow_path = ROOT / ".github/workflows/v54-live-provider-acceptance.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    on = workflow.get("on") or workflow.get(True)
    assert set(on) == {"workflow_dispatch"}
    execute = on["workflow_dispatch"]["inputs"]["execute_live_sandbox"]
    assert execute["type"] == "boolean" and execute["default"] is False
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["acceptance"]
    assert job["environment"] == "v54-live-provider-sandbox"
    assert job["timeout-minutes"] <= 10
    text = workflow_path.read_text(encoding="utf-8")
    assert "push:" not in text and "pull_request:" not in text and "schedule:" not in text
    assert "recipient" not in text.lower() and "sender" not in text.lower()
    assert "production" not in text.lower()
    assert "protocol.json" in text


def test_protocol_shape_has_no_unbounded_diagnostics():
    config = configuration()
    protocol = gate.run_gate(config, Adapter(config, lookup=["applied"]))
    assert set(protocol) == {
        "schema", "status", "reason_code", "provider_scope", "dispatch_attempts",
        "lookup_attempts", "observed_effects", "timeout_after_effect_observed",
        "reconciliation", "cleanup", "raw_output_published",
    }
    assert "error" not in protocol and "message" not in protocol and "endpoint" not in protocol
