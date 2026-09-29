import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import httpx
import pytest

from app import telegram_relay as relay


@pytest.fixture(autouse=True)
def state(monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setattr(relay, "_send_state", {
        "successes_total": 0, "errors_total": 0, "last_success_at": None,
        "last_error_at": None, "last_error_type": None, "last_error_status": None,
    })
    monkeypatch.setattr(relay, "_poll_state", {
        "last_poll_at": None, "last_update_id": None, "delivered_updates": 0,
        "last_error": None, "last_error_at": None, "last_error_operation": None,
        "last_error_type": None, "last_error_status": None, "errors_total": 0,
        "consecutive_errors": 0, "recoveries_total": 0, "last_recovered_at": None,
    })


def test_health_requires_successful_fresh_poll():
    assert relay.health()["status"] == "degraded"
    relay._record_poll_success()
    assert relay.health()["status"] == "healthy"


def test_send_network_error_is_not_retried_or_exposed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_RELAY_SECRET", "fake")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    client = MagicMock()
    client.__enter__.return_value = client
    client.request.side_effect = httpx.ReadError("SECRET URL")
    monkeypatch.setattr(relay, "_client", lambda: client)
    with pytest.raises(relay.HTTPException) as exc:
        relay.send(relay.SendRequest(chat_id="test", message="synthetic"), "fake")
    assert exc.value.status_code == 502
    assert "SECRET" not in exc.value.detail
    assert client.request.call_count == 1
    assert relay.health()["outbound"]["errors_total"] == 1
    assert relay.health()["outbound"]["successes_total"] == 0


def test_send_telegram_rejection_is_not_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_RELAY_SECRET", "fake")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    client = MagicMock()
    client.__enter__.return_value = client
    client.request.return_value = httpx.Response(200, json={"ok": False}, request=httpx.Request("POST", "https://example.invalid"))
    monkeypatch.setattr(relay, "_client", lambda: client)
    with pytest.raises(relay.HTTPException):
        relay.send(relay.SendRequest(chat_id="test", message="synthetic"), "fake")
    assert relay.health()["outbound"]["errors_total"] == 1
    relay._poll_state["last_poll_at"] = (datetime.now(timezone.utc) - timedelta(seconds=91)).isoformat()
    assert relay.health()["status"] == "degraded"


def test_disabled_polling_is_not_proof_of_health(monkeypatch):
    relay._record_poll_success()
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "false")
    assert relay.health()["status"] == "degraded"


def test_recovery_retains_history_and_never_logs_exception_content(capsys):
    request = httpx.Request("GET", "https://example.invalid/botSECRET/getUpdates")
    error = httpx.HTTPStatusError("SECRET", request=request, response=httpx.Response(502, request=request))
    delays = [relay._record_poll_failure("getUpdates", error) for _ in range(8)]
    assert delays == [3, 6, 12, 24, 30, 30, 30, 30]
    assert relay.health()["status"] == "degraded"
    relay._record_poll_success()
    result = relay.health()
    assert result["status"] == "healthy"
    assert result["errors_total"] == 8
    assert result["recoveries_total"] == 1
    assert result["consecutive_errors"] == 0
    assert result["last_error_status"] == 502
    assert result["last_error_operation"] == "getUpdates"
    assert result["last_error_at"] and result["last_recovered_at"]
    assert "SECRET" not in capsys.readouterr().out
    assert "SECRET" not in str(result)


@pytest.mark.parametrize("payload", [{"ok": False}, [], {"result": []}])
def test_http_200_is_not_enough(payload):
    response = httpx.Response(200, json=payload, request=httpx.Request("GET", "https://example.invalid"))
    with pytest.raises(ValueError):
        relay._require_telegram_ok(response)


def test_startup_error_recovers_without_discarding_updates(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "fake")
    ok = httpx.Response(200, json={"ok": True, "result": []}, request=httpx.Request("GET", "https://example.invalid"))
    telegram = AsyncMock()
    telegram.__aenter__.return_value = telegram
    telegram.post.side_effect = [httpx.ReadError("private"), ok]
    telegram.get.side_effect = [ok, asyncio.CancelledError()]
    backend = AsyncMock()
    backend.__aenter__.return_value = backend
    clients = iter([telegram, backend])
    monkeypatch.setattr(relay.httpx, "AsyncClient", lambda **kwargs: next(clients))
    monkeypatch.setattr(relay.asyncio, "sleep", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(relay._poll_updates())
    assert telegram.post.call_count == 2
    assert all(c.kwargs["json"] == {"drop_pending_updates": False} for c in telegram.post.call_args_list)
    assert relay._poll_state["recoveries_total"] == 1
    assert relay._poll_state["last_error_operation"] == "deleteWebhook"
    backend.post.assert_not_called()


def test_webhook_failure_keeps_offset_and_is_not_cleared_by_poll(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "fake")
    request = httpx.Request("GET", "https://example.invalid")
    ok = httpx.Response(200, json={"ok": True, "result": []}, request=request)
    updates = httpx.Response(200, json={"ok": True, "result": [{"update_id": 42}]}, request=request)
    telegram = AsyncMock()
    telegram.__aenter__.return_value = telegram
    telegram.post.return_value = ok
    telegram.get.side_effect = [updates, updates, asyncio.CancelledError()]
    backend = AsyncMock()
    backend.__aenter__.return_value = backend
    backend.post.side_effect = [httpx.ReadError("private"), ok]
    clients = iter([telegram, backend])
    monkeypatch.setattr(relay.httpx, "AsyncClient", lambda **kwargs: next(clients))
    async def check_failure(delay):
        assert relay.health()["status"] == "degraded"
        assert relay._poll_state["last_update_id"] is None
        assert relay._poll_state["last_error_operation"] == "backend_webhook"
    monkeypatch.setattr(relay.asyncio, "sleep", check_failure)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(relay._poll_updates())
    assert "offset" not in telegram.get.call_args_list[1].kwargs["params"]
    assert telegram.get.call_args_list[2].kwargs["params"]["offset"] == 43
    assert relay._poll_state["delivered_updates"] == 1
    assert relay.health()["status"] == "healthy"
