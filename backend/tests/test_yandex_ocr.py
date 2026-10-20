"""Yandex Vision OCR policy tests with no network access."""
from pathlib import Path
import logging
from types import SimpleNamespace

import httpx
import pytest

from app.organizer_engine import content


class _Directory:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return str(self.path)

    def __exit__(self, *_args):
        return False


def _response(status: int, payload=None):
    return SimpleNamespace(status_code=status, json=lambda: payload or {})


VALID_PAYLOAD = {
    "result": {
        "textAnnotation": {
            "width": "1000",
            "height": "1200",
            "fullText": "Договор № 1\nСумма 100",
            "blocks": [
                {
                    "lines": [
                        {
                            "text": "Договор № 1",
                            "words": [
                                {
                                    "text": "Договор",
                                    "boundingBox": {"vertices": [
                                        {"x": "10", "y": "20"}, {"x": "60", "y": "20"},
                                        {"x": "60", "y": "40"}, {"x": "10", "y": "40"},
                                    ]},
                                },
                                {
                                    "text": "№",
                                    "boundingBox": {"vertices": [
                                        {"x": "65", "y": "20"}, {"x": "80", "y": "20"},
                                        {"x": "80", "y": "40"}, {"x": "65", "y": "40"},
                                    ]},
                                },
                            ],
                        }
                    ]
                }
            ],
        }
    }
}


def test_configured_requires_key_and_folder(monkeypatch):
    monkeypatch.delenv("YANDEX_VISION_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    monkeypatch.delenv("OCR_PROVIDER", raising=False)
    assert content._yandex_ocr_configured() is False
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    assert content._yandex_ocr_configured() is True
    monkeypatch.setenv("OCR_PROVIDER", "local")
    assert content._yandex_ocr_configured() is False


def test_yandex_page_parses_full_text(tmp_path, monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    monkeypatch.setattr(content.httpx, "post", lambda *a, **k: _response(200, VALID_PAYLOAD))
    page = content._yandex_ocr_page(image, 1)
    assert page is not None
    assert "Договор № 1" in page.text
    assert page.method == "ocr"


def test_yandex_page_retries_on_429(tmp_path, monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    responses = iter([_response(429), _response(200, VALID_PAYLOAD)])
    monkeypatch.setattr(content.httpx, "post", lambda *a, **k: next(responses))
    monkeypatch.setattr(content.time, "sleep", lambda _s: None)
    page = content._yandex_ocr_page(image, 1)
    assert page is not None
    assert "Договор № 1" in page.text


def test_yandex_page_none_after_two_500(tmp_path, monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    monkeypatch.setattr(content.httpx, "post", lambda *a, **k: _response(500))
    monkeypatch.setattr(content.time, "sleep", lambda _s: None)
    assert content._yandex_ocr_page(image, 1) is None


def test_yandex_page_none_on_connect_error(tmp_path, monkeypatch):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")

    def boom(*_a, **_k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(content.httpx, "post", boom)
    assert content._yandex_ocr_page(image, 1) is None


def test_pdf_pages_fall_back_to_tesseract_when_cloud_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    monkeypatch.delenv("OCR_PROVIDER", raising=False)
    monkeypatch.setattr(content, "OCR_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(content.shutil, "which", lambda name: f"/fake/{name}")

    def render(command, **_kwargs):
        Path(command[-1]).with_suffix(".jpg").write_bytes(b"synthetic")
        return SimpleNamespace(returncode=0)

    def fake_cloud(image, page_number):
        if page_number == 2:
            return None
        return content.PageExtraction(
            page=page_number, text=f"cloud page {page_number}",
            confidence=0.95, method="ocr",
        )

    tesseract_calls: list[int] = []

    def fake_tesseract(path, _timeout, *, allow_fallback=True):
        tesseract_calls.append(int(path.stem.rsplit("-", 1)[-1]))
        return f"local page {path.stem}"

    monkeypatch.setattr(content.subprocess, "run", render)
    monkeypatch.setattr(content, "_yandex_ocr_page", fake_cloud)
    monkeypatch.setattr(content, "_tesseract", fake_tesseract)
    monkeypatch.setattr(content.tempfile, "TemporaryDirectory", lambda **_k: _Directory(tmp_path))

    result = content._ocr_pdf_pages(b"synthetic pdf bytes", {1, 2, 3})
    assert set(result) == {1, 2, 3}
    assert 2 in tesseract_calls
    assert 1 not in tesseract_calls
    assert 3 not in tesseract_calls


def test_authorization_header_and_no_key_in_logs(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("YANDEX_VISION_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    image = tmp_path / "page.jpg"
    image.write_bytes(b"synthetic")
    captured: dict[str, object] = {}

    def capture(_url, *, json, headers, timeout):
        captured["headers"] = headers
        return _response(500)

    monkeypatch.setattr(content.httpx, "post", capture)
    monkeypatch.setattr(content.time, "sleep", lambda _s: None)
    caplog.set_level(logging.WARNING)
    assert content._yandex_ocr_page(image, 1) is None
    assert captured["headers"]["Authorization"] == "Api-Key test-key"
    assert "test-key" not in caplog.text
