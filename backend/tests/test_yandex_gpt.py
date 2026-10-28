import json

import httpx
import pytest

from app import gemini_analysis
from app.gemini_analysis import (
    ANALYSIS_SCHEMA,
    _try_deepseek,
    _yandex_json,
    yandex_configured,
)


def _yandex_response(text: str) -> httpx.Response:
    request = httpx.Request("POST", "https://llm.api.cloud.yandex.net/foundationModels/v1/completion")
    body = json.dumps({
        "result": {
            "alternatives": [{"message": {"role": "assistant", "text": text}}],
        },
    })
    return httpx.Response(200, request=request, content=body)


def _deepseek_response(text: str) -> httpx.Response:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    body = json.dumps({"choices": [{"message": {"content": text}}]})
    return httpx.Response(200, request=request, content=body)


def _clear_ai_env(monkeypatch):
    for name in (
        "AI_PRIMARY_PROVIDER", "GEMINI_API_KEY", "DEEPSEEK_API_KEY",
        "YANDEX_GPT_API_KEY", "YANDEX_VISION_API_KEY", "YANDEX_FOLDER_ID",
        "YANDEX_GPT_MODEL", "YANDEX_GPT_URL", "YANDEX_GPT_MAX_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_yandex_primary_uses_yandex_url_headers_and_model_uri(monkeypatch):
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("AI_PRIMARY_PROVIDER", "yandex")
    monkeypatch.setenv("YANDEX_GPT_API_KEY", "test-yandex-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1g-test-folder")

    captured = {}

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["json"] = kwargs.get("json")
        return _yandex_response(json.dumps({"document_type": "Акт", "executive_summary": "ok"}))

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = _try_deepseek("sys", "user", ANALYSIS_SCHEMA)

    assert result["document_type"] == "Акт"
    assert result["_provider"] == "yandex"
    assert captured["url"] == "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    assert captured["headers"]["Authorization"] == "Api-Key test-yandex-key"
    assert captured["headers"]["x-folder-id"] == "b1g-test-folder"
    assert captured["json"]["modelUri"] == "gpt://b1g-test-folder/yandexgpt/latest"
    assert captured["json"]["completionOptions"]["stream"] is False
    assert captured["json"]["completionOptions"]["maxTokens"] == "4000"


def test_yandex_json_parses_markdown_fenced_response(monkeypatch):
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("YANDEX_GPT_API_KEY", "test-yandex-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1g-test-folder")

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        return _yandex_response("```json\n{\"document_type\": \"Счёт\"}\n```")

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = _yandex_json("sys", "user", ANALYSIS_SCHEMA)
    assert result == {"document_type": "Счёт"}


def test_yandex_json_raises_on_garbage_response(monkeypatch):
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("YANDEX_GPT_API_KEY", "test-yandex-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1g-test-folder")

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        return _yandex_response("совершенно не JSON")

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    with pytest.raises(ValueError):
        _yandex_json("sys", "user", ANALYSIS_SCHEMA)


def test_yandex_configured_requires_key_and_folder(monkeypatch):
    _clear_ai_env(monkeypatch)
    assert yandex_configured() is False

    monkeypatch.setenv("YANDEX_GPT_API_KEY", "test-yandex-key")
    assert yandex_configured() is False

    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1g-test-folder")
    assert yandex_configured() is True

    monkeypatch.delenv("YANDEX_GPT_API_KEY")
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-vision-key")
    assert yandex_configured() is True


def test_yandex_failure_falls_back_to_deepseek(monkeypatch):
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("AI_PRIMARY_PROVIDER", "yandex")
    monkeypatch.setenv("YANDEX_GPT_API_KEY", "test-yandex-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "b1g-test-folder")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")

    def fake_request_with_retry(client, method, url, *, policy, **kwargs):
        if "llm.api.cloud.yandex.net" in url:
            raise httpx.HTTPStatusError(
                "500 Server Error",
                request=httpx.Request("POST", url),
                response=httpx.Response(500, request=httpx.Request("POST", url)),
            )
        return _deepseek_response(json.dumps({"document_type": "Акт"}))

    monkeypatch.setattr(gemini_analysis, "request_with_retry", fake_request_with_retry)

    result = _try_deepseek("sys", "user", ANALYSIS_SCHEMA)
    assert result["document_type"] == "Акт"
    assert result["_provider"] == "deepseek"
