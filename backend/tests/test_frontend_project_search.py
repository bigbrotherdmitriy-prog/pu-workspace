from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_project_search_covers_core_project_entities():
    source = (ROOT / "frontend/src/modules/search/ProjectSearchResults.tsx").read_text(encoding="utf-8")
    for label in ("Документ", "Договор", "Задача", "Письмо"):
        assert label in source
    assert "Совпадений" in source


def test_project_search_is_wired_to_header():
    source = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert 'import { ProjectSearchResults' in source
    assert "projectSearchHits" in source
    assert "openProjectSearchHit" in source
