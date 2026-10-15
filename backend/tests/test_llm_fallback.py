import json

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
