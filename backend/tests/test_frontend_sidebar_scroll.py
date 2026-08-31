from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_sidebar_navigation_scrolls_without_clipping_last_items():
    css = (ROOT / "frontend/src/brand.css").read_text(encoding="utf-8")
    assert ".shell > aside nav" in css
    assert "min-height: 0" in css
    assert "overflow-y: auto" in css
    assert "height: 100dvh" in css
    assert ".shell > aside .profile" in css
    assert "flex: none" in css
