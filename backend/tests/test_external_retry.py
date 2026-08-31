import httpx
import pytest

from app.core.external_retry import RATE_LIMIT_ONLY_RETRY, RetryPolicy, request_with_retry


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
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
