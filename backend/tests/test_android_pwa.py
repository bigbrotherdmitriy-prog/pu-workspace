import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parents[2]


def test_android_manifest_has_installable_icons_and_shortcuts():
    manifest = json.loads((ROOT / "frontend/public/manifest.webmanifest").read_text(encoding="utf-8"))
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
    assert manifest["display"] == "standalone"
    assert len(manifest["shortcuts"]) >= 3


def test_android_navigation_and_safe_areas_are_present():
    navigation = (ROOT / "frontend/src/modules/android/AndroidBottomNav.tsx").read_text(encoding="utf-8")
    styles = (ROOT / "frontend/src/modules/android/android.css").read_text(encoding="utf-8")
    assert "Сегодня" in navigation and "Письма" in navigation and "Задачи" in navigation
    assert "safe-area-inset-bottom" in styles
    assert "100dvh" in styles
    assert "font-size: 16px" in styles


def test_android_shortcuts_open_the_requested_workspace_section():
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert 'get("section")' in app
    assert 'today: "Сегодня"' in app
    assert 'mail: "Письма"' in app
    assert 'tasks: "Задачи"' in app


def test_android_app_link_requires_release_certificate(monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("PU_ANDROID_CERT_SHA256", raising=False)
    response = client.get("/.well-known/assetlinks.json")
    assert response.status_code == 503


def test_android_app_link_is_bound_to_configured_release_certificate(monkeypatch):
    fingerprint = "94:1A:D5:3C:AB:FE:EE:BA:8A:08:FE:2A:DB:69:4F:38:B1:C3:1B:CE:ED:6D:D5:B4:94:03:BB:DC:20:51:D3:CE"
    monkeypatch.setenv("PU_ANDROID_CERT_SHA256", fingerprint)
    client = TestClient(app)
    response = client.get("/.well-known/assetlinks.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    target = response.json()[0]["target"]
    assert target["package_name"] == "ru.puworkspace.app"
    assert target["sha256_cert_fingerprints"] == [fingerprint]
