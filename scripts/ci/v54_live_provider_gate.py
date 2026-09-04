"""Default-off acceptance gate for an isolated live-provider sandbox.

The gate never accepts message bodies, recipients, provider object IDs or raw
provider diagnostics.  A sandbox bridge owns its sink internally and exposes a
small, content-free API used only to prove timeout-after-effect reconciliation.

Without the explicit acknowledgement and every test-only secret the command
writes a NOT_RUN protocol and exits successfully.  A partial or unsafe
configuration can therefore never be mistaken for a provider PASS.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


SCHEMA = "puw.v54.live-provider.protocol.v1"
ENABLE_ACK = "I_ACKNOWLEDGE_EPHEMERAL_SINK_ONLY_EFFECT"
ARTIFACT = Path("v54-live-provider-artifacts/protocol.json")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_HOST_LABEL = re.compile(r"(^|[.-])(sandbox|test|testing|staging|qa)([.-]|$)", re.I)
FORBIDDEN_ADDRESS_KEYS = {
    "PUW_V54_LIVE_PROVIDER_ADDRESS",
    "PUW_V54_LIVE_PROVIDER_FROM",
    "PUW_V54_LIVE_PROVIDER_RECIPIENT",
    "PUW_V54_LIVE_PROVIDER_SENDER",
    "PUW_V54_LIVE_PROVIDER_TO",
}
REQUIRED_SECRET_KEYS = (
    "PUW_V54_LIVE_PROVIDER_BASE_URL",
    "PUW_V54_LIVE_PROVIDER_TOKEN",
    "PUW_V54_LIVE_PROVIDER_ACCOUNT_FINGERPRINT",
    "PUW_V54_LIVE_PROVIDER_EXPECTED_HOST_SHA256",
)
SAFE_FAILURE_CODES = {
    "address_input_forbidden",
    "attestation_invalid",
    "cleanup_failed",
    "dispatch_did_not_timeout",
    "effect_count_invalid",
    "lookup_exhausted",
    "protocol_invalid",
    "sandbox_request_failed",
    "unsafe_endpoint",
}


class GateFailure(RuntimeError):
    """Allowlisted failure without provider diagnostics."""

    def __init__(self, code: str):
        self.code = code if code in SAFE_FAILURE_CODES else "protocol_invalid"
        super().__init__(self.code)


class ExpectedTimeoutAfterEffect(TimeoutError):
    """The bridge intentionally withheld the dispatch response after effect."""


@dataclass(frozen=True)
class GateConfig:
    base_url: str
    token: str
    account_fingerprint: str
    expected_host_sha256: str
    run_nonce: str
    dispatch_timeout_seconds: float = 2.0
    lookup_attempts: int = 8
    lookup_delay_seconds: float = 1.0


class SandboxAdapter(Protocol):
    def attest(self) -> Mapping[str, object]: ...

    def dispatch_timeout_after_effect(self, command: Mapping[str, object]) -> None: ...

    def lookup(self, command: Mapping[str, object]) -> Mapping[str, object]: ...

    def cleanup(self, command: Mapping[str, object]) -> None: ...


class HttpSandboxAdapter:
    """Content-free bridge client; endpoint and token are never exposed."""

    def __init__(self, config: GateConfig):
        self.config = config
        self.context = ssl.create_default_context()

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        timeout: float = 10.0,
    ) -> Mapping[str, object]:
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.token}",
            "X-PUW-Run-Nonce": self.config.run_nonce,
        }
        if payload is not None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
            headers["Content-Type"] = "application/json"
        request = Request(urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/")),
                          data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout, context=self.context) as response:
                raw = response.read(16_385)
                if len(raw) > 16_384:
                    raise GateFailure("protocol_invalid")
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise GateFailure("protocol_invalid")
                return value
        except (TimeoutError, socket.timeout) as exc:
            raise ExpectedTimeoutAfterEffect from exc
        except HTTPError as exc:
            if exc.code == 504 and method == "POST":
                raise ExpectedTimeoutAfterEffect from None
            raise GateFailure("sandbox_request_failed") from None
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ExpectedTimeoutAfterEffect from None
            raise GateFailure("sandbox_request_failed") from None
        except (UnicodeError, ValueError, OSError):
            raise GateFailure("sandbox_request_failed") from None

    def attest(self) -> Mapping[str, object]:
        return self._request("GET", "/v1/acceptance/capabilities")

    def dispatch_timeout_after_effect(self, command: Mapping[str, object]) -> None:
        self._request(
            "POST",
            "/v1/acceptance/effects",
            payload={**command, "fault": "timeout-after-effect"},
            timeout=self.config.dispatch_timeout_seconds,
        )

    def lookup(self, command: Mapping[str, object]) -> Mapping[str, object]:
        return self._request("POST", "/v1/acceptance/lookup", payload=command)

    def cleanup(self, command: Mapping[str, object]) -> None:
        result = self._request("POST", "/v1/acceptance/cleanup", payload=command)
        if result != {"status": "CLEANED"}:
            raise GateFailure("cleanup_failed")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_protocol(status: str, reason_code: str | None) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "status": status,
        "reason_code": reason_code,
        "provider_scope": "ephemeral-test-sink-only",
        "dispatch_attempts": 0,
        "lookup_attempts": 0,
        "observed_effects": 0,
        "timeout_after_effect_observed": False,
        "reconciliation": "NOT_RUN",
        "cleanup": "NOT_RUN",
        "raw_output_published": False,
    }


def _write_protocol(protocol: Mapping[str, object], target: Path = ARTIFACT) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(protocol, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def config_from_env(env: Mapping[str, str]) -> tuple[GateConfig | None, str | None]:
    if env.get("PUW_V54_LIVE_PROVIDER_ENABLE") != ENABLE_ACK:
        return None, "disabled"
    if any(env.get(key) for key in FORBIDDEN_ADDRESS_KEYS):
        raise GateFailure("address_input_forbidden")
    if any(not env.get(key) for key in REQUIRED_SECRET_KEYS):
        return None, "test_secrets_missing"

    base_url = env["PUW_V54_LIVE_PROVIDER_BASE_URL"].strip()
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or is_ip
        or host in {"localhost", "localhost.localdomain"}
        or "prod" in host
        or not SAFE_HOST_LABEL.search(host)
        or _digest(host) != env["PUW_V54_LIVE_PROVIDER_EXPECTED_HOST_SHA256"]
    ):
        raise GateFailure("unsafe_endpoint")

    token = env["PUW_V54_LIVE_PROVIDER_TOKEN"]
    account_fingerprint = env["PUW_V54_LIVE_PROVIDER_ACCOUNT_FINGERPRINT"]
    if len(token) < 24 or not SHA256.fullmatch(account_fingerprint):
        raise GateFailure("protocol_invalid")
    run_id = env.get("GITHUB_RUN_ID", "local") + ":" + env.get("GITHUB_RUN_ATTEMPT", "1")
    dispatch_timeout = float(env.get("PUW_V54_LIVE_PROVIDER_DISPATCH_TIMEOUT", "2"))
    lookup_attempts = int(env.get("PUW_V54_LIVE_PROVIDER_LOOKUP_ATTEMPTS", "8"))
    lookup_delay = float(env.get("PUW_V54_LIVE_PROVIDER_LOOKUP_DELAY", "1"))
    if (
        not 0.5 <= dispatch_timeout <= 10
        or not 1 <= lookup_attempts <= 20
        or not 0 <= lookup_delay <= 10
    ):
        raise GateFailure("protocol_invalid")
    return GateConfig(
        base_url=base_url,
        token=token,
        account_fingerprint=account_fingerprint,
        expected_host_sha256=env["PUW_V54_LIVE_PROVIDER_EXPECTED_HOST_SHA256"],
        run_nonce=_digest("puw-v54-live-provider:" + run_id),
        dispatch_timeout_seconds=dispatch_timeout,
        lookup_attempts=lookup_attempts,
        lookup_delay_seconds=lookup_delay,
    ), None


def _require_attestation(attestation: Mapping[str, object], config: GateConfig) -> None:
    expected = {
        "schema": "puw.v54.live-provider.capabilities.v1",
        "environment": "ephemeral-test",
        "effect_class": "sink-only",
        "address_policy": "no-external-delivery",
        "cleanup": "supported",
        "fault": "timeout-after-effect",
        "account_fingerprint": config.account_fingerprint,
        "run_nonce": config.run_nonce,
    }
    if dict(attestation) != expected:
        raise GateFailure("attestation_invalid")


def run_gate(config: GateConfig, adapter: SandboxAdapter, *, sleep=time.sleep) -> dict[str, object]:
    protocol = _base_protocol("FAIL", "protocol_invalid")
    command = {
        "action_id": _digest("action:" + config.run_nonce),
        "command_key": _digest("command:" + config.run_nonce),
        "idempotency_key": _digest("idempotency:" + config.run_nonce),
        "payload_hash": _digest("content-free-sink-effect:" + config.run_nonce),
        "account_fingerprint": config.account_fingerprint,
        "run_nonce": config.run_nonce,
    }
    attested = False
    try:
        _require_attestation(adapter.attest(), config)
        attested = True
        protocol["dispatch_attempts"] = 1
        try:
            adapter.dispatch_timeout_after_effect(command)
        except ExpectedTimeoutAfterEffect:
            protocol["timeout_after_effect_observed"] = True
        else:
            raise GateFailure("dispatch_did_not_timeout")

        observation = None
        for attempt in range(1, config.lookup_attempts + 1):
            protocol["lookup_attempts"] = attempt
            candidate = adapter.lookup(command)
            if candidate.get("outcome") == "UNKNOWN":
                if set(candidate) != {"outcome", "observed_effects"} or candidate["observed_effects"] != 0:
                    raise GateFailure("protocol_invalid")
                if attempt < config.lookup_attempts:
                    sleep(config.lookup_delay_seconds)
                continue
            observation = candidate
            break
        if observation is None:
            raise GateFailure("lookup_exhausted")
        expected = {
            "outcome": "APPLIED",
            "observed_effects": 1,
            "action_id": command["action_id"],
            "command_key": command["command_key"],
            "idempotency_key": command["idempotency_key"],
            "payload_hash": command["payload_hash"],
            "account_fingerprint": command["account_fingerprint"],
            "run_nonce": command["run_nonce"],
        }
        if dict(observation) != expected:
            if observation.get("observed_effects") != 1:
                raise GateFailure("effect_count_invalid")
            raise GateFailure("protocol_invalid")
        protocol.update({
            "status": "PASS",
            "reason_code": None,
            "observed_effects": 1,
            "reconciliation": "PASS",
        })
    except GateFailure as exc:
        protocol["status"] = "FAIL"
        protocol["reason_code"] = exc.code
    except Exception:
        protocol["status"] = "FAIL"
        protocol["reason_code"] = "sandbox_request_failed"
    finally:
        if attested:
            try:
                adapter.cleanup(command)
                protocol["cleanup"] = "PASS"
            except Exception:
                protocol["cleanup"] = "FAIL"
                protocol["status"] = "FAIL"
                protocol["reason_code"] = "cleanup_failed"
    return protocol


def main(env: Mapping[str, str] | None = None, target: Path = ARTIFACT) -> int:
    source = os.environ if env is None else env
    try:
        config, reason = config_from_env(source)
        if config is None:
            protocol = _base_protocol("NOT_RUN", reason)
        else:
            protocol = run_gate(config, HttpSandboxAdapter(config))
    except GateFailure as exc:
        protocol = _base_protocol("FAIL", exc.code)
    except (TypeError, ValueError):
        protocol = _base_protocol("FAIL", "protocol_invalid")
    _write_protocol(protocol, target)
    print(json.dumps({"schema": SCHEMA, "status": protocol["status"],
                      "reason_code": protocol["reason_code"]}, sort_keys=True))
    return 1 if protocol["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
