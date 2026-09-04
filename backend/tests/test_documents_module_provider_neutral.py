from pathlib import Path

from app.integrations.source_urls import source_object_url


ROOT = Path(__file__).resolve().parents[2]


def test_source_url_is_owned_by_integration_layer():
    assert source_object_url("google_drive", "abc") == "https://drive.google.com/open?id=abc"
    assert source_object_url("local", "abc") is None
    assert source_object_url("google_drive", None) is None


def test_documents_module_does_not_construct_vendor_url():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module = (ROOT / "frontend" / "src" / "modules" / "documents" / "DocumentsModule.tsx").read_text(encoding="utf-8")
    assert "<DocumentsModule" in app
    assert "source_url" in module
    assert "drive.google.com/open?id" not in app
    assert "drive.google.com" not in module
