from datetime import date
from types import SimpleNamespace
from app.google_calendar import event_payload


def test_calendar_event_is_one_day_and_grounded():
    task = SimpleNamespace(title="Предоставить акт", source_file_name="Письмо.pdf", source_excerpt="Предоставить акт до 30.08.2026", confidence=0.9, due_date=date(2026, 8, 30))
    event = event_payload(task)
    assert event["start"]["date"] == "2026-08-30"
    assert event["end"]["date"] == "2026-08-31"
    assert "Письмо.pdf" in event["description"]
