"""Diagnostic logging for LocalUploadLifecycleAdapter failures.

Every fail-closed raise in the adapter must emit a ``warning`` that names the
method and a stable reason code.  Neither the exception text nor any
file-derived data may appear in the log record.
"""

from __future__ import annotations

import logging

import pytest

from app.local_upload_staging import (
    LocalUploadLifecycleAdapter,
    LocalUploadUnavailable,
)


class _ReserveRaisesValueError:
    def reserve(self, session, **kwargs):
        raise ValueError("secret-file.doc")


def test_reserve_value_error_logs_reason_without_exception_text(caplog):
    adapter = LocalUploadLifecycleAdapter(_ReserveRaisesValueError())
    with caplog.at_level(logging.WARNING):
        with pytest.raises(LocalUploadUnavailable):
            adapter.reserve(object())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "reserve failed: reason=exception:ValueError" in message
        for message in messages
    )
    assert all("secret-file.doc" not in message for message in messages)


class _MissingMethods:
    pass


def test_missing_method_logs_reason(caplog):
    adapter = LocalUploadLifecycleAdapter(_MissingMethods())
    with caplog.at_level(logging.WARNING):
        with pytest.raises(LocalUploadUnavailable):
            adapter.reserve(object())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "reserve failed: reason=missing_method" in message
        for message in messages
    )


class _UnexpectedResult:
    def reserve(self, session, **kwargs):
        return "not-a-reservation"


def test_unexpected_result_logs_reason(caplog):
    adapter = LocalUploadLifecycleAdapter(_UnexpectedResult())
    with caplog.at_level(logging.WARNING):
        with pytest.raises(LocalUploadUnavailable):
            adapter.reserve(object())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "reserve failed: reason=unexpected_result:str" in message
        for message in messages
    )


class _CauseRaises:
    def reserve(self, session, **kwargs):
        cause = KeyError("x")
        raise RuntimeError("outer") from cause


def test_exception_chain_includes_cause_type(caplog):
    adapter = LocalUploadLifecycleAdapter(_CauseRaises())
    with caplog.at_level(logging.WARNING):
        with pytest.raises(LocalUploadUnavailable):
            adapter.reserve(object())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "reserve failed: reason=exception:RuntimeError<-KeyError" in message
        for message in messages
    )
