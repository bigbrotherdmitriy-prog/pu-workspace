import httpx
import pytest

from app.core.external_retry import (
    DEFAULT_EXTERNAL_RETRY,
    RATE_LIMIT_ONLY_RETRY,
    RETRYABLE_STATUS_CODES,
    RetryPolicy,
    request_with_retry,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.captured = []

    def request(self, method, url, **kwargs):
        self.calls += 1
        self.captured.append({"method": method, "url": url, "kwargs": kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(status: int, *, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://provider.test"))


def test_retries_rate_limit_and_honors_retry_after_with_jitter():
    client = FakeClient([response(429, retry_after="2"), response(200)])
    delays = []

    result = request_with_retry(
        client, "POST", "https://provider.test",
        policy=RetryPolicy(attempts=3, base_delay=0.5, max_delay=5, jitter=0.25),
        sleep=delays.append, random_value=lambda: 0.4,
    )

    assert result.status_code == 200
    assert client.calls == 2
    assert delays == [2.1]


def test_does_not_retry_authentication_or_validation_failures():
    client = FakeClient([response(401), response(200)])

    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(client, "POST", "https://provider.test", sleep=lambda _: None)

    assert client.calls == 1


def test_retries_network_failure_with_exponential_backoff():
    request = httpx.Request("GET", "https://provider.test")
    client = FakeClient([httpx.ReadTimeout("slow", request=request), response(503), response(200)])
    delays = []

    result = request_with_retry(
        client, "GET", "https://provider.test",
        policy=RetryPolicy(attempts=3, base_delay=1, max_delay=10, jitter=0),
        sleep=delays.append,
    )

    assert result.status_code == 200
    assert delays == [1, 2]


def test_non_idempotent_send_does_not_retry_ambiguous_network_failure():
    request = httpx.Request("POST", "https://provider.test/send")
    client = FakeClient([httpx.ReadTimeout("response lost", request=request), response(200)])

    with pytest.raises(httpx.ReadTimeout):
        request_with_retry(
            client, "POST", "https://provider.test/send",
            policy=RATE_LIMIT_ONLY_RETRY, sleep=lambda _: None,
        )

    assert client.calls == 1


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS_CODES))
def test_default_policy_retries_each_retryable_status_on_safe_get(status):
    client = FakeClient([response(status), response(200)])
    delays = []

    result = request_with_retry(
        client, "GET", "https://provider.test/resource",
        policy=DEFAULT_EXTERNAL_RETRY,
        sleep=delays.append, random_value=lambda: 0.0,
    )

    assert result.status_code == 200
    assert client.calls == 2
    assert len(delays) == 1


@pytest.mark.parametrize("status", [401, 403, 400, 422])
def test_terminal_statuses_are_not_retried_and_sleep_is_empty(status):
    client = FakeClient([response(status), response(200)])
    delays = []

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        request_with_retry(
            client, "GET", "https://provider.test/resource",
            policy=DEFAULT_EXTERNAL_RETRY,
            sleep=delays.append, random_value=lambda: 0.0,
        )

    assert excinfo.value.response.status_code == status
    assert client.calls == 1
    assert delays == []


def test_exhaustion_makes_three_requests_two_delays_and_raises_last_error():
    first_response = response(503)
    middle_response = response(502)
    last_response = response(504)
    client = FakeClient([first_response, middle_response, last_response])
    delays = []

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        request_with_retry(
            client, "GET", "https://provider.test/resource",
            policy=RetryPolicy(attempts=3, base_delay=1, max_delay=10, jitter=0),
            sleep=delays.append, random_value=lambda: 0.0,
        )

    assert excinfo.value.response is last_response
    assert excinfo.value.response.status_code == 504
    assert client.calls == 3
    assert delays == [1, 2]


@pytest.mark.parametrize("status", [503, 500])
def test_rate_limit_only_policy_does_not_retry_other_server_errors(status):
    client = FakeClient([response(status), response(200)])
    delays = []

    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(
            client, "GET", "https://provider.test/resource",
            policy=RATE_LIMIT_ONLY_RETRY,
            sleep=delays.append, random_value=lambda: 0.0,
        )

    assert client.calls == 1
    assert delays == []


def test_rate_limit_only_policy_does_not_retry_ambiguous_network_timeout():
    request = httpx.Request("GET", "https://provider.test/resource")
    client = FakeClient([httpx.ReadTimeout("slow", request=request), response(200)])
    delays = []

    with pytest.raises(httpx.ReadTimeout):
        request_with_retry(
            client, "GET", "https://provider.test/resource",
            policy=RATE_LIMIT_ONLY_RETRY,
            sleep=delays.append, random_value=lambda: 0.0,
        )

    assert client.calls == 1
    assert delays == []


def test_rate_limit_only_policy_retries_429_then_succeeds():
    client = FakeClient([response(429), response(200)])
    delays = []

    result = request_with_retry(
        client, "GET", "https://provider.test/resource",
        policy=RATE_LIMIT_ONLY_RETRY,
        sleep=delays.append, random_value=lambda: 0.0,
    )

    assert result.status_code == 200
    assert client.calls == 2
    assert len(delays) == 1


def test_headers_body_and_idempotency_key_are_passed_unchanged_on_retry():
    client = FakeClient([response(503), response(200)])
    headers = {"X-Fake-Header": "fake-value", "Idempotency-Key": "fake-idempotency-key"}
    body = b"fake-body"

    result = request_with_retry(
        client, "GET", "https://provider.test/resource",
        policy=RetryPolicy(attempts=3, base_delay=1, max_delay=10, jitter=0),
        sleep=lambda _: None, random_value=lambda: 0.0,
        headers=headers, content=body,
    )

    assert result.status_code == 200
    assert client.calls == 2
    for call in client.captured:
        assert call["method"] == "GET"
        assert call["url"] == "https://provider.test/resource"
        assert call["kwargs"]["headers"] == headers
        assert call["kwargs"]["content"] == body
        assert "idempotency_key" not in call["kwargs"]


def test_real_httpx_client_signature_accepts_headers_and_content_via_mock_transport():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if len(captured) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, request=request)

    transport = httpx.MockTransport(handler)
    headers = {"X-Fake-Header": "fake-value", "Idempotency-Key": "fake-idempotency-key"}
    body = b"fake-body"
    delays = []

    with httpx.Client(transport=transport) as client:
        result = request_with_retry(
            client, "POST", "https://provider.test/resource",
            policy=RetryPolicy(attempts=3, base_delay=1, max_delay=10, jitter=0),
            sleep=delays.append, random_value=lambda: 0.0,
            headers=headers, content=body,
        )

    assert result.status_code == 200
    assert len(captured) == 2
    assert delays == [1]
    first, second = captured
    assert first.method == second.method == "POST"
    assert first.url == second.url == httpx.URL("https://provider.test/resource")
    assert first.headers.get("Idempotency-Key") == "fake-idempotency-key"
    assert second.headers.get("Idempotency-Key") == "fake-idempotency-key"
    assert first.headers.get("X-Fake-Header") == "fake-value"
    assert second.headers.get("X-Fake-Header") == "fake-value"
    assert first.content == second.content == body


@pytest.mark.parametrize("retry_after", [None, "not-a-number"])
def test_missing_or_invalid_retry_after_uses_exponential_fallback(retry_after):
    client = FakeClient([response(429, retry_after=retry_after), response(200)])
    delays = []

    result = request_with_retry(
        client, "GET", "https://provider.test/resource",
        policy=RetryPolicy(attempts=3, base_delay=1, max_delay=10, jitter=0),
        sleep=delays.append, random_value=lambda: 0.0,
    )

    assert result.status_code == 200
    assert client.calls == 2
    assert delays == [1]


def test_large_retry_after_is_capped_by_max_delay():
    client = FakeClient([response(429, retry_after="9999"), response(200)])
    delays = []

    result = request_with_retry(
        client, "GET", "https://provider.test/resource",
        policy=RetryPolicy(attempts=3, base_delay=1, max_delay=5, jitter=0),
        sleep=delays.append, random_value=lambda: 0.0,
    )

    assert result.status_code == 200
    assert client.calls == 2
    assert delays == [5]


@pytest.mark.parametrize("attempts", [0, -1])
def test_attempts_below_one_raises_without_calling_client_or_sleep(attempts):
    client = FakeClient([response(200)])
    delays = []

    with pytest.raises(ValueError):
        request_with_retry(
            client, "GET", "https://provider.test/resource",
            policy=RetryPolicy(attempts=attempts),
            sleep=delays.append, random_value=lambda: 0.0,
        )

    assert client.calls == 0
    assert delays == []


# Codex fallback: DS25 was rejected; no real provider or sleeps in these regressions.
@pytest.mark.parametrize("changes", [
    {"attempts": True}, {"attempts": 1.5}, {"attempts": "3"},
    {"base_delay": -1}, {"base_delay": float("nan")}, {"base_delay": float("inf")},
    {"max_delay": -1}, {"max_delay": float("nan")}, {"max_delay": float("inf")},
    {"jitter": -1}, {"jitter": float("nan")}, {"jitter": float("inf")},
    {"base_delay": True}, {"max_delay": "8"},
])
def test_invalid_policy_never_calls_provider(changes):
    client = FakeClient([response(200)])
    delays = []
    with pytest.raises(ValueError):
        request_with_retry(client, "GET", "https://provider.test", policy=RetryPolicy(**changes), sleep=delays.append)
    assert client.calls == 0 and delays == []


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "-2"])
def test_nonfinite_or_negative_retry_after_uses_safe_fallback(value):
    client = FakeClient([response(429, retry_after=value), response(200)])
    delays = []
    request_with_retry(client, "GET", "https://provider.test", policy=RetryPolicy(base_delay=1, jitter=0), sleep=delays.append)
    assert delays == [1]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True, "0.5"])
def test_invalid_random_source_never_reaches_sleep(value):
    client = FakeClient([response(429), response(200)])
    delays = []
    with pytest.raises(ValueError):
        request_with_retry(client, "GET", "https://provider.test", sleep=delays.append, random_value=lambda: value)
    assert client.calls == 1 and delays == []
