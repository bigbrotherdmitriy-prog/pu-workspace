from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "frontend" / "src" / "App.tsx"
MODULE = ROOT / "frontend" / "src" / "modules" / "meetings" / "MeetingsModule.tsx"


def test_meetings_ui_is_extracted_from_app_shell() -> None:
    app = APP.read_text(encoding="utf-8")
    module = MODULE.read_text(encoding="utf-8")

    assert 'from "./modules/meetings/MeetingsModule"' in app
    assert "<MeetingsModule" in app
    assert "meeting-grid" not in app
    assert "meeting-grid" in module
    assert "Новое совещание" in module


def test_meeting_minutes_remain_an_explicit_user_action() -> None:
    module = MODULE.read_text(encoding="utf-8")

    assert "Внести протокол и проанализировать" in module
    assert "onRecordMinutes(item)" in module
    # Completed minutes can be corrected with an exact version; cancelled
    # meetings still have no edit action. Binding/confirmation is separate.
    assert 'item.status !== "cancelled"' in module
    assert '<MeetingSourcePanel' in module
    assert 'item.status === "completed" && item.minutes' in module
    assert 'Number.isSafeInteger(item.record_version)' in module
    app = APP.read_text(encoding="utf-8")
    assert 'expected_version: item.record_version' in app
    assert 'window.prompt("Вставьте протокол:' in app
