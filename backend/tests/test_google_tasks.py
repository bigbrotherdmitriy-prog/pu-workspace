from datetime import date
from types import SimpleNamespace
from app.google_tasks import task_payload


def test_google_task_payload_keeps_source_and_due_date():
    task = SimpleNamespace(title="Подготовить акт", source_file_name="Письмо.docx", source_excerpt="Просим подготовить акт.", confidence=0.9, due_date=date(2026, 8, 30))
    payload = task_payload(task)
    assert payload["title"] == "Подготовить акт"
    assert payload["due"] == "2026-08-30T00:00:00Z"
    assert "Письмо.docx" in payload["notes"]
