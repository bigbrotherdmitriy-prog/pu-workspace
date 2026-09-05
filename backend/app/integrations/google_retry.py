"""Bounded retry boundary for idempotent Google provider reads."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any


class GoogleReadError(RuntimeError):
    """A content-free provider-read failure safe for logs and API boundaries."""

    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _status(error: BaseException) -> int | None:
    response = getattr(error, "resp", None)
    value = getattr(response, "status", None)
    if value is None:
        value = getattr(error, "status_code", None)
    return value if type(value) is int else None


def _retry_after(error: BaseException) -> float | None:
    response = getattr(error, "resp", None)
    headers = response if isinstance(response, Mapping) else getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= 5 else None


def _retryable(error: BaseException) -> bool:
    status = _status(error)
    if status is not None:
        return status == 429 or 500 <= status <= 599
    return isinstance(error, (ConnectionError, TimeoutError, OSError))


def execute_google_read(
    request_factory: Callable[[], Any],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.25,
    before_attempt: Callable[[], None] | None = None,
) -> Any:
    """Execute one idempotent read with bounded exponential backoff.

    Provider exception text is deliberately replaced by a stable safe code.
    Authentication and invalid-cursor failures are not retried. Callers retain
    responsibility for their existing mailbox generation/authority checks.
    """
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise ValueError("invalid_google_read_policy")
    if not isinstance(base_delay_seconds, (int, float)) or not 0 <= base_delay_seconds <= 2:
        raise ValueError("invalid_google_read_policy")
    for attempt in range(max_attempts):
        try:
            if before_attempt is not None:
                before_attempt()
            return request_factory().execute()
        except GoogleReadError:
            raise
        except Exception as error:
            retryable = _retryable(error)
            if not retryable:
                raise GoogleReadError("provider_read_rejected", retryable=False) from None
            if attempt + 1 >= max_attempts:
                raise GoogleReadError("provider_read_unavailable", retryable=True) from None
            delay = _retry_after(error)
            if delay is None:
                delay = min(5.0, float(base_delay_seconds) * (2 ** attempt))
            time.sleep(delay)
    raise AssertionError("unreachable")
