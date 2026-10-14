from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_contract_document_picker_is_extracted_and_preserves_search_sources():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    panel = (ROOT / "frontend" / "src" / "modules" / "contracts" / "ContractDetailsPanel.tsx").read_text(encoding="utf-8")
    picker = (ROOT / "frontend" / "src" / "modules" / "contracts" / "ContractDocumentPicker.tsx").read_text(encoding="utf-8")
    # App mounts the contract card; the card hosts the document picker.
    assert "<ContractDetailsPanel" in app
    assert "<ContractDocumentPicker" in panel
    assert "<ContractDocumentPicker" not in app
    assert "Поиск документа по названию" in picker
    assert "recommended" in picker and "server" in picker and "upload" in picker
    assert "Сам файл и его название не изменяются" in picker
