from __future__ import annotations

from dataclasses import dataclass
import math
import random
import time
from typing import Any, Callable

import httpx


RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25
    retry_network_errors: bool = True
    retryable_status_codes: frozenset[int] = RETRYABLE_STATUS_CODES


DEFAULT_EXTERNAL_RETRY = RetryPolicy()
HEAVY_AI_RETRY = RetryPolicy(attempts=3, base_delay=1.0, max_delay=12.0, jitter=0.5)
RATE_LIMIT_ONLY_RETRY = RetryPolicy(
    attempts=3,
    base_delay=1.0,
    max_delay=12.0,
    jitter=0.25,
    retry_network_errors=False,
    retryable_status_codes=frozenset({429}),
)


def _retry_after_seconds(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After", "").strip()
    try:
        seconds = float(value) if value else None
        return seconds if seconds is not None and math.isfinite(seconds) and seconds >= 0 else None
    except ValueError:
        return None


def _is_retryable(exc: httpx.HTTPError, policy: RetryPolicy) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return policy.retry_network_errors
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in policy.retryable_status_codes
    return False


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    policy: RetryPolicy = DEFAULT_EXTERNAL_RETRY,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
    **kwargs: Any,
) -> httpx.Response:
    """Execute one retry-safe HTTP request without logging URL, headers or body."""
    if type(policy.attempts) is not int or policy.attempts < 1:
        raise ValueError("retry attempts must be a positive integer")
    for name in ("base_delay", "max_delay", "jitter"):
        value = getattr(policy, name)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"retry {name} must be finite and nonnegative")
    last_error: httpx.HTTPError | None = None
    for attempt in range(policy.attempts):
        try:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            last_error = exc
            if not _is_retryable(exc, policy) or attempt + 1 >= policy.attempts:
                raise
            response = exc.response if isinstance(exc, httpx.HTTPStatusError) else None
            retry_after = _retry_after_seconds(response)
            exponential = min(policy.max_delay, policy.base_delay * (2**attempt))
            delay = retry_after if retry_after is not None else exponential
            sample = random_value()
            if type(sample) not in (int, float) or not math.isfinite(sample) or not 0 <= sample <= 1:
                raise ValueError("retry random sample must be finite and between zero and one")
            delay = min(policy.max_delay, delay + policy.jitter * sample)
            sleep(delay)
    assert last_error is not None
    raise last_error
