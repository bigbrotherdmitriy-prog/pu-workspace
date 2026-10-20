"""Gemini vision OCR policy tests with no network access."""
from pathlib import Path
import logging
from types import SimpleNamespace

import pytest

from app.organizer_engine import content


def _response(status: int, payload=None):
    return SimpleNamespace(status_code=status, json=lambda: payload or {})


VALID_PAYLOAD = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {"text": "Договор № 1"},
                    {"text": "Сумма 100"},
                ]
            }
        }
    ]
}


def test_configured_requires_explicit_provider_and_key(monkeypatch):
    monkeypatch.delenv("OCR_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert content._gemini_ocr_configured() is False
    monkeypatch.setenv("OCR_PROVIDER", "auto")
    assert content._gemini_ocr_configured() is False
    monkeypatch.setenv("OCR_PROVIDER", "gemini")
    assert content._gemini_ocr_configured() is True
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert content._gemini_ocr_configured() is False


def test_gemini_page_parses_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OCR_PROVIDER", "gemini")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    captured: dict[str, object] = {}

    def capture(_url, *, json, headers, timeout):
        captured["headers"] = headers
        captured["json"] = json
        return _response(200, VALID_PAYLOAD)

    monkeypatch.setattr(content.httpx, "post", capture)
    page = content._gemini_ocr_page(image, 1)
    assert page is not None
    assert "Договор № 1" in page.text
    assert "Сумма 100" in page.text
    assert page.method == "ocr"
    assert captured["headers"]["x-goog-api-key"] == "test-key"
    parts = captured["json"]["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"


def test_gemini_page_retries_on_429(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OCR_PROVIDER", "gemini")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    responses = iter([_response(429), _response(200, VALID_PAYLOAD)])
    monkeypatch.setattr(content.httpx, "post", lambda *a, **k: next(responses))
    monkeypatch.setattr(content.time, "sleep", lambda _s: None)
    page = content._gemini_ocr_page(image, 1)
    assert page is not None
    assert "Договор № 1" in page.text


def test_gemini_page_none_after_two_500(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OCR_PROVIDER", "gemini")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    monkeypatch.setattr(content.httpx, "post", lambda *a, **k: _response(500))
    monkeypatch.setattr(content.time, "sleep", lambda _s: None)
    assert content._gemini_ocr_page(image, 1) is None


def test_gemini_no_key_in_logs(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OCR_PROVIDER", "gemini")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    monkeypatch.setattr(content.httpx, "post", lambda *a, **k: _response(500))
    monkeypatch.setattr(content.time, "sleep", lambda _s: None)
    caplog.set_level(logging.WARNING)
    assert content._gemini_ocr_page(image, 1) is None
    assert "test-key" not in caplog.text


def test_cloud_falls_back_to_yandex_when_gemini_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OCR_PROVIDER", "gemini")
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    called: list[str] = []

    def fake_gemini(_image, _page):
        called.append("gemini")
        return None

    def fake_yandex(_image, page_number):
        called.append("yandex")
        return content.PageExtraction(
            page=page_number, text="cloud page", confidence=0.95, method="ocr"
        )

    monkeypatch.setattr(content, "_gemini_ocr_page", fake_gemini)
    monkeypatch.setattr(content, "_yandex_ocr_page", fake_yandex)
    page = content._cloud_ocr_page(image, 1)
    assert page is not None
    assert page.text == "cloud page"
    assert called == ["gemini", "yandex"]
