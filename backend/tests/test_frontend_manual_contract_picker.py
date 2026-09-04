from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_contract_picker_supports_explicit_manual_selection():
    source = (ROOT / "frontend/src/modules/contracts/ContractDocumentPicker.tsx").read_text(encoding="utf-8")
    assert "Выбрать договор самому" in source
    assert "Ручной выбор документа договора" in source
    assert "Привязать выбранный файл" in source
    assert "setSelectedDocumentId(document.id)" in source
    assert "props.onLink(selectedDocumentId)" in source


def test_manual_picker_keeps_source_filters_and_original_link():
    source = (ROOT / "frontend/src/modules/contracts/ContractDocumentPicker.tsx").read_text(encoding="utf-8")
    assert "Сервер / реестр" in source
    assert "Google Drive" in source
    assert "Открыть оригинал" in source
    assert "source_url" in source
