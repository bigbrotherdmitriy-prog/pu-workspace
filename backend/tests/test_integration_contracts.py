import pytest

from app.core.integration_types import StorageObject
from app.integrations.ai import GeminiAIAdapter
from app.integrations.contracts import AIProviderAdapter, AdapterHealth, IntegrationAdapter
from app.integrations.registry import AdapterRegistry
from app.organizer_engine.types import DriveFile, FOLDER_MIME


class DemoAdapter:
    provider = "demo"

    def health(self) -> AdapterHealth:
        return AdapterHealth(ready=True, detail="ok")


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


def test_gemini_adapter_conforms_to_ai_contract(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "configured-for-test")
    adapter = GeminiAIAdapter()
    assert isinstance(adapter, AIProviderAdapter)
    assert adapter.health().ready
