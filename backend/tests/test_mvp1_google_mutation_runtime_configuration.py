import pytest

from app.organizer_engine.storage_mutation_runtime import (
    GoogleStorageMutationRuntime, configured_storage_mutation_runtime,
)


def test_live_runtime_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PU_MVP1_GOOGLE_LIVE_MUTATIONS", raising=False)
    assert configured_storage_mutation_runtime() is None


def test_explicit_flag_builds_google_runtime_without_resolving_credentials(monkeypatch):
    monkeypatch.setenv("PU_MVP1_GOOGLE_LIVE_MUTATIONS", "true")
    runtime = configured_storage_mutation_runtime()
    assert isinstance(runtime, GoogleStorageMutationRuntime) and runtime.enabled is True


def test_google_runtime_rejects_non_phase1c_connection_before_provider_io():
    runtime = GoogleStorageMutationRuntime(lambda: None, enabled=True)
    from app.organizer_engine.storage_mutations import StorageBindingPin
    with pytest.raises(Exception, match="live_storage_binding_unsupported"):
        runtime._adapter(StorageBindingPin(1, "google_drive", "legacy", "root", 1), None)
