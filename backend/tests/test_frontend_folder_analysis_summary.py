from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_folder_analysis_summary_explains_scan_result_and_next_step():
    source = (ROOT / "frontend/src/modules/folder-analysis/FolderAnalysisSummary.tsx").read_text(encoding="utf-8")
    assert "Итог анализа рабочей папки" in source
    assert "Оригиналы не изменены" in source
    assert "Распознано" in source
    assert "Неразобранное" in source
    assert "Переименований" in source
    assert "Следующий шаг" in source


def test_folder_analysis_summary_is_connected_to_proposals_screen():
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    assert 'import { FolderAnalysisSummary }' in app
    assert "<FolderAnalysisSummary" in app
    assert 'onOpenDocuments={() => setActive("Документы")}' in app
