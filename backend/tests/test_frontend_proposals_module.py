from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_proposals_module_preserves_safe_copy_workflow():
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    module = (ROOT / "frontend/src/modules/proposals/ProposalsModule.tsx").read_text(encoding="utf-8")
    assert "<ProposalsModule" in app
    for label in ["Подтвердить только безопасные", "Dry-run и применить к копии", "Стандартизировать все файлы в копии", "Откатить", "Проверить и изменить этот оригинал"]:
        assert label in module

def test_proposals_module_keeps_folder_analysis_summary():
    module = (ROOT / "frontend/src/modules/proposals/ProposalsModule.tsx").read_text(encoding="utf-8")
    assert "FolderAnalysisSummary" in module
    assert "props.onOpenDocuments" in module
