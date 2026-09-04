from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_inbox_shell_is_extracted_and_provider_neutral():
    app = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    module = (ROOT / "frontend" / "src" / "modules" / "inbox" / "InboxModule.tsx").read_text(encoding="utf-8")
    assert "<InboxModule" in app
    assert "Входящие письма" in module
    assert "Ничего внешнего не создаётся без подтверждения" in module
    assert "Gmail" not in module
