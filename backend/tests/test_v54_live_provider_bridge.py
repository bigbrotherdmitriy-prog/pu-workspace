from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.v54_live_provider_bridge import Settings, create_app


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FakeSink:
    def __init__(self):
        self.fingerprint = digest("test-only-google-sub")
        self.effects: dict[str, dict] = {}
        self.calls: list[str] = []

    def configured(self):
        return True

    def account_fingerprint(self):
        return self.fingerprint

    def cleanup_expired(self):
        self.calls.append("ttl-cleanup")
        return 0

    def create_once(self, command):
        self.calls.append("create")
        self.effects.setdefault(command.command_key, command.model_dump(exclude={"fault"}))

    def lookup(self, command):
        self.calls.append("lookup")
        effect = self.effects.get(command.command_key)
        return ({"outcome": "APPLIED", "observed_effects": 1, **effect}
                if effect else {"outcome": "UNKNOWN", "observed_effects": 0})

    def cleanup(self, command):
        self.calls.append("cleanup")
        self.effects.pop(command.command_key, None)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        bridge_token="b" * 32,
        setup_token="s" * 32,
        fernet_key=Fernet.generate_key().decode(),
        client_id="test-client",
        client_secret="test-secret",
        redirect_uri="https://provider-sandbox.example.test/oauth/callback",
        expected_email="test@example.invalid",
        data_dir=tmp_path,
    )


def command(sink: FakeSink, nonce: str) -> dict[str, str]:
    return {
        "action_id": digest("action"),
        "command_key": digest("command"),
        "idempotency_key": digest("idempotency"),
        "payload_hash": digest("payload"),
        "account_fingerprint": sink.fingerprint,
        "run_nonce": nonce,
    }


def headers(nonce: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + "b" * 32, "X-PUW-Run-Nonce": nonce}


def test_exact_attestation_and_one_effect_lookup_cleanup(tmp_path):
    sink = FakeSink()
    nonce = digest("run")
    expected = command(sink, nonce)
    with TestClient(create_app(settings(tmp_path), sink)) as client:
        attestation = client.get("/v1/acceptance/capabilities", headers=headers(nonce))
        assert attestation.status_code == 200
        assert attestation.json() == {
            "schema": "puw.v54.live-provider.capabilities.v1",
            "environment": "ephemeral-test",
            "effect_class": "sink-only",
            "address_policy": "no-external-delivery",
            "cleanup": "supported",
            "fault": "timeout-after-effect",
            "account_fingerprint": sink.fingerprint,
            "run_nonce": nonce,
        }
        response = client.post("/v1/acceptance/effects", headers=headers(nonce),
                               json={**expected, "fault": "timeout-after-effect"})
        assert response.status_code == 504
        assert client.post("/v1/acceptance/lookup", headers=headers(nonce), json=expected).json() == {
            "outcome": "APPLIED", "observed_effects": 1, **expected,
        }
        assert client.post("/v1/acceptance/cleanup", headers=headers(nonce), json=expected).json() == {
            "status": "CLEANED"
        }
        assert client.post("/v1/acceptance/lookup", headers=headers(nonce), json=expected).json() == {
            "outcome": "UNKNOWN", "observed_effects": 0,
        }
    assert sink.calls == ["ttl-cleanup", "create", "lookup", "cleanup", "lookup"]


def test_effect_is_idempotent_for_same_command(tmp_path):
    sink = FakeSink()
    nonce = digest("run")
    body = {**command(sink, nonce), "fault": "timeout-after-effect"}
    with TestClient(create_app(settings(tmp_path), sink)) as client:
        assert client.post("/v1/acceptance/effects", headers=headers(nonce), json=body).status_code == 504
        assert client.post("/v1/acceptance/effects", headers=headers(nonce), json=body).status_code == 504
    assert len(sink.effects) == 1


def test_rejects_wrong_token_nonce_identity_and_extra_content(tmp_path):
    sink = FakeSink()
    nonce = digest("run")
    body = command(sink, nonce)
    with TestClient(create_app(settings(tmp_path), sink)) as client:
        assert client.post("/v1/acceptance/lookup", headers={**headers(nonce), "Authorization": "Bearer wrong"},
                           json=body).status_code == 401
        assert client.post("/v1/acceptance/lookup", headers=headers(digest("other")), json=body).status_code == 403
        assert client.post("/v1/acceptance/lookup", headers=headers(nonce),
                           json={**body, "account_fingerprint": digest("wrong")}).status_code == 403
        assert client.post("/v1/acceptance/lookup", headers=headers(nonce),
                           json={**body, "recipient": "forbidden@example.com"}).status_code == 422


def test_setup_is_hidden_and_cannot_reconnect_configured_sink(tmp_path):
    sink = FakeSink()
    with TestClient(create_app(settings(tmp_path), sink)) as client:
        assert client.post("/oauth/start", headers={"X-PUW-Setup-Token": "wrong"}).status_code == 404
        assert client.post(
            "/oauth/start", headers={"X-PUW-Setup-Token": "s" * 32}, follow_redirects=False
        ).status_code == 409
