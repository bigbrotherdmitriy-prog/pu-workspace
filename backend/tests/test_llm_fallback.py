import json
import logging

import httpx
import pytest

import app.gemini_analysis as gemini_analysis
from app.gemini_analysis import (
    ANALYSIS_SCHEMA,
    analyze_document_with_gemini,
)


def _make_response(url: str, status_code: int, payload: dict) -> httpx.Response:
    request = httpx.Request("POST", url)
    body = json.dumps(payload)
    return httpx.Response(status_code, request=request, content=body)


def _gemini_success_response() -> httpx.Response:
    return _make_response(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
        200,
        {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps({
                    "document_type": "Акт",
                    "executive_summary": "s",
                    "parties": [], "contract_references": [], "amounts": [], "dates": [],
                    "obligations": [], "risks": [], "inconsistencies": [], "missing_data": [],
                    "recommended_actions": [], "draft_reply": "", "confidence": "high",
                })}]}},
            ],
        },
    )


def _gemini_503_response() -> httpx.Response:
    return _make_response(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent",
        503,
        {"error": {"code": 503, "message": "Service Unavailable"}},
    )


def _deepseek_success_response() -> httpx.Response:
    return _make_response(
        "https://api.deepseek.com/chat/completions",
        200,
        {
            "choices": [
                {"message": {"content": json.dumps({
                    "document_type": "Акт",
                    "executive_summary": "deepseek",
                    "parties": [], "contract_references": [], "amounts": [], "dates": [],
                    "obligations": [], "risks": [], "inconsistencies": [], "missing_data": [],
                    "recommended_actions": [], "draft_reply": "", "confidence": "high",
                })}},
            ],
        },
    )


def test_gemini_503_falls_back_to_deepseek(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")

    calls: list[str] = []

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        calls.append(url)
        if "generativelanguage" in url:
            response = _gemini_503_response()
            raise httpx.HTTPStatusError("503", request=response.request, response=response)
        return _deepseek_success_response()

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = analyze_document_with_gemini("text", "f.txt")
    assert result["_provider"] == "deepseek"
    assert result["executive_summary"] == "deepseek"
    assert any("generativelanguage" in u for u in calls)
    assert any("api.deepseek.com" in u for u in calls)


def test_gemini_success_does_not_call_deepseek(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")

    calls: list[str] = []

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        calls.append(url)
        return _gemini_success_response()

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = analyze_document_with_gemini("text", "f.txt")
    assert result["_provider"] == "gemini"
    assert all("api.deepseek.com" not in u for u in calls)


def test_both_providers_fail_raises(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        response = _gemini_503_response() if "generativelanguage" in url else _make_response(
            "https://api.deepseek.com/chat/completions", 503,
            {"error": {"code": 503, "message": "Service Unavailable"}},
        )
        raise httpx.HTTPStatusError("503", request=response.request, response=response)

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    with pytest.raises(httpx.HTTPStatusError):
        analyze_document_with_gemini("text", "f.txt")


def test_no_gemini_key_but_deepseek_key_uses_deepseek_immediately(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")

    calls: list[str] = []

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        calls.append(url)
        return _deepseek_success_response()

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = analyze_document_with_gemini("text", "f.txt")
    assert result["_provider"] == "deepseek"
    assert all("generativelanguage" not in u for u in calls)
    assert all("api.deepseek.com" in u for u in calls)


def test_primary_deepseek_calls_deepseek_first_and_skips_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    monkeypatch.setenv("AI_PRIMARY_PROVIDER", "deepseek")

    calls: list[str] = []

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        calls.append(url)
        return _deepseek_success_response()

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = analyze_document_with_gemini("text", "f.txt")
    assert result["_provider"] == "deepseek"
    assert calls, "expected at least one HTTP call"
    assert all("api.deepseek.com" in u for u in calls)
    assert all("generativelanguage" not in u for u in calls)


def test_primary_deepseek_failure_falls_back_to_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    monkeypatch.setenv("AI_PRIMARY_PROVIDER", "deepseek")

    calls: list[str] = []

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        calls.append(url)
        if "api.deepseek.com" in url:
            response = _make_response(url, 503, {"error": {"code": 503, "message": "Service Unavailable"}})
            raise httpx.HTTPStatusError("503", request=response.request, response=response)
        return _gemini_success_response()

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = analyze_document_with_gemini("text", "f.txt")
    assert result["_provider"] == "gemini"
    assert any("api.deepseek.com" in u for u in calls)
    assert any("generativelanguage" in u for u in calls)


def test_default_provider_order_is_gemini_first(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    monkeypatch.delenv("AI_PRIMARY_PROVIDER", raising=False)

    calls: list[str] = []

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        calls.append(url)
        return _gemini_success_response()

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = analyze_document_with_gemini("text", "f.txt")
    assert result["_provider"] == "gemini"
    assert calls and all("generativelanguage" in u for u in calls)
    assert all("api.deepseek.com" not in u for u in calls)


def test_primary_provider_helper_defaults_to_gemini(monkeypatch):
    monkeypatch.delenv("AI_PRIMARY_PROVIDER", raising=False)
    assert gemini_analysis.primary_provider() == "gemini"
    monkeypatch.setenv("AI_PRIMARY_PROVIDER", "deepseek")
    assert gemini_analysis.primary_provider() == "deepseek"
    monkeypatch.setenv("AI_PRIMARY_PROVIDER", "GEMINI")
    assert gemini_analysis.primary_provider() == "gemini"


def test_timing_logs_ai_call_with_provider(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    monkeypatch.delenv("AI_PRIMARY_PROVIDER", raising=False)

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        return _gemini_success_response()

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    with caplog.at_level(logging.INFO, logger="app.gemini_analysis"):
        analyze_document_with_gemini("text", "f.txt")

    matching = [r for r in caplog.records if "ai_call kind=" in r.getMessage()]
    assert matching, "expected ai_call timing log"
    assert any("provider=gemini" in r.getMessage() for r in matching)


def test_timing_logs_failed_provider(monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        response = _gemini_503_response() if "generativelanguage" in url else _make_response(
            "https://api.deepseek.com/chat/completions", 503,
            {"error": {"code": 503, "message": "Service Unavailable"}},
        )
        raise httpx.HTTPStatusError("503", request=response.request, response=response)

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    with caplog.at_level(logging.WARNING, logger="app.gemini_analysis"):
        with pytest.raises(httpx.HTTPStatusError):
            analyze_document_with_gemini("text", "f.txt")

    matching = [r for r in caplog.records if "ai_call kind=" in r.getMessage()]
    assert matching, "expected ai_call failure log"
    assert any("provider=failed" in r.getMessage() for r in matching)
