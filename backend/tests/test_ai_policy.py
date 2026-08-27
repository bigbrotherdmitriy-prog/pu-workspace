from types import SimpleNamespace

import pytest

from app.ai_policy import ExternalAIBlocked, prepare_external_ai_text
from app.api.ai_policy import router


class FakeSession:
    def __init__(self, mode: str | None):
        self.policy = SimpleNamespace(mode=mode, dlp_enabled=True, prompt_version="v1") if mode else None

    def get(self, _model, _project_id):
        return self.policy


def test_ai_policy_routes_are_exposed():
    paths = {route.path for route in router.routes}
    assert "/projects/{project_id}/ai-policy" in paths


def test_local_only_blocks_external_ai():
    with pytest.raises(ExternalAIBlocked):
        prepare_external_ai_text(FakeSession("local_only"), 1, "секрет")


def test_redacted_mode_replaces_sensitive_values():
    text, mode = prepare_external_ai_text(
        FakeSession("redacted"), 1, "Пишите user@example.com или +7 999 123-45-67, ИНН 7707083893"
    )
    assert mode == "redacted"
    assert "user@example.com" not in text
    assert "999 123" not in text
    assert "7707083893" not in text
    assert "[EMAIL_" in text and "[PHONE_" in text and "[INN_" in text


def test_metadata_only_never_returns_content():
    text, mode = prepare_external_ai_text(FakeSession("metadata_only"), 1, "очень секретный текст")
    assert mode == "metadata_only"
    assert "секретный" not in text
    assert "22" in text
