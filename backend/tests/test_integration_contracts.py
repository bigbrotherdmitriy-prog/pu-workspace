import pytest

from app.core.integration_types import StorageObject
from app.integrations.ai import GeminiAIAdapter
from app.integrations.actions import publish_actions
from app.integrations.contracts import ActionAdapter, AIProviderAdapter, AdapterHealth, ChannelAdapter, IntegrationAdapter
from app.integrations.registry import AdapterRegistry
from app.integrations.telegram import TelegramChannelAdapter
from app.organizer_engine.types import DriveFile, FOLDER_MIME


class DemoAdapter:
    provider = "demo"

    def health(self) -> AdapterHealth:
        return AdapterHealth(ready=True, detail="ok")


class DemoActionAdapter(DemoAdapter):
    def sync_tasks(self, tasks, force_update=False):
        return len(tasks), 0

    def sync_calendar(self, tasks, force_update=False):
        return len(tasks), 0


def test_storage_object_keeps_legacy_drive_file_compatible():
    assert DriveFile is StorageObject
    assert StorageObject("1", "folder", FOLDER_MIME, "root").is_folder
    assert StorageObject("2", "folder", "custom/type", "root", object_type="folder").is_folder


def test_registry_resolves_by_capability_and_provider():
    registry = AdapterRegistry()
    adapter = registry.register("storage", DemoAdapter())
    assert isinstance(adapter, IntegrationAdapter)
    assert registry.get("storage", "demo") is adapter
    assert registry.providers("storage") == ("demo",)
    with pytest.raises(LookupError):
        registry.get("channel", "demo")


def test_action_publication_is_provider_neutral():
    adapter = DemoActionAdapter()
    assert isinstance(adapter, ActionAdapter)
    result = publish_actions(adapter, [object(), object()])
    assert result.task_synced == 2
    assert result.calendar_synced == 2


def test_gemini_adapter_conforms_to_ai_contract(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "configured-for-test")
    adapter = GeminiAIAdapter()
    assert isinstance(adapter, AIProviderAdapter)
    assert adapter.health().ready


def test_telegram_adapter_conforms_to_channel_contract(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "configured-for-test")
    adapter = TelegramChannelAdapter()
    assert isinstance(adapter, ChannelAdapter)
    assert adapter.health().ready
    assert adapter.receive() == []
