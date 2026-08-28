from pathlib import Path


def _source() -> str:
    return (Path(__file__).parents[2] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")


def test_task_completion_supports_optional_evidence_and_history():
    source = _source()
    assert "Без вложения — это допустимо" in source
    assert "completion_document_id: completionDocumentId || null" in source
    assert "История задачи и решений" in source
    assert "Подтвердить завершение" in source


def test_contract_document_picker_has_source_tabs_and_search():
    source = _source()
    assert "Сервер / реестр" in source
    assert "Облако / загрузки" in source
    assert "Google Drive" in source
    assert "Поиск документа по названию" in source
    assert "Найти договор по номеру, контрагенту и тексту" in source
    assert "/source-candidates" in source
    assert "Рекомендованные" in source
    assert "candidate.reasons.join" in source
