from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_brand_exposes_calm_surface_tokens():
    css = (ROOT / "frontend/src/brand.css").read_text(encoding="utf-8")
    assert "--pu-radius-card: 12px" in css
    assert "--pu-radius-shell: 16px" in css
    assert ".card, .metrics article" in css
    assert "border: .5px solid var(--pu-border)" in css
    assert "box-shadow: none" in css


def test_sidebar_is_flat_and_active_state_has_no_inset_shadow():
    brand = (ROOT / "frontend/src/brand.css").read_text(encoding="utf-8")
    designer = (ROOT / "frontend/src/designer.css").read_text(encoding="utf-8")
    assert "background: var(--pu-graphite-950)" in brand
    assert "background: var(--pu-graphite-950)" in designer
    active_rule = brand.split("nav button.active", 1)[1].split("}", 1)[0]
    assert "linear-gradient" not in active_rule
    assert "box-shadow" not in active_rule


def test_analytics_and_key_modules_use_shared_tokens():
    brand = (ROOT / "frontend/src/brand.css").read_text(encoding="utf-8")
    assert "repeat(auto-fit, minmax(200px, 1fr))" in brand
    for relative in (
        "frontend/src/modules/today/today.css",
        "frontend/src/modules/project-launch/project-launch.css",
        "frontend/src/modules/folder-analysis/folder-analysis.css",
        "frontend/src/modules/search/project-search.css",
        "frontend/src/modules/android/android.css",
    ):
        css = (ROOT / relative).read_text(encoding="utf-8")
        assert "var(--pu-radius-card)" in css or "var(--pu-radius-shell)" in css
