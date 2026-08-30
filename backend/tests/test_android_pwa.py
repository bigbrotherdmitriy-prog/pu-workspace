import json
from pathlib import Path


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
