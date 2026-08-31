from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_folder_analysis_shows_exact_progress_and_remaining_count():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    css_source = (ROOT / "frontend" / "src" / "source.css").read_text(encoding="utf-8")

    assert "processed_item_count" in app_source
    assert "обработано" in app_source
    assert "осталось" in app_source
    assert "source-progress-track" in app_source
    assert ".source-progress-track" in css_source


def test_processing_queue_exposes_counts_required_by_progress_ui():
    workspace_source = (ROOT / "backend" / "app" / "api" / "workspace.py").read_text(encoding="utf-8")

    assert "source_item_count" in workspace_source
    assert "copy_item_count" in workspace_source
    assert "processed_item_count" in workspace_source
