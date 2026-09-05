from __future__ import annotations

from dataclasses import replace

import pytest

from app.integrations.storage_mutation_live import (
    ConditionalMutationTimeout,
    ExactPreconditionUnavailable,
    GoogleDriveExactMutationAdapter,
    ProviderObjectState,
    YandexDiskExactMutationAdapter,
)
from app.integrations.yandex_disk import YandexDiskStorageAdapter
from app.organizer_engine.drive import DriveClient
from app.organizer_engine.storage_mutations import (
    MutationCommand,
    MutationConflict,
    MutationReceipt,
    StorageBindingPin,
    StorageMutation,
    execute_mutation,
)


class ReceiptStore:
    def __init__(self, pin: StorageBindingPin):
        self.pin = pin
        self.version = 1
        self.receipts: dict[str, MutationReceipt] = {}

    def current_binding(self, project_id: int) -> StorageBindingPin:
        if project_id != self.pin.project_id:
            raise MutationConflict("resource_unavailable")
        return self.pin

    def current_record_version(self, _project_id: int) -> int:
        return self.version

    def get(self, key: str) -> MutationReceipt | None:
        return self.receipts.get(key)

    def append(self, receipt: MutationReceipt) -> None:
        assert receipt.command_key not in self.receipts
        self.receipts[receipt.command_key] = receipt
        self.version = receipt.resulting_record_version


class FakeConditionalClient:
    supports_exact_mutation_preconditions = True

    def __init__(self, provider: str):
        self.provider = provider
        self.states = {
            "root": ProviderObjectState("root", "copy", "account-root", "r1", (), object_type="folder"),
            "folder-a": ProviderObjectState("folder-a", "A", "root", "r1", ("root",), object_type="folder"),
            "folder-b": ProviderObjectState("folder-b", "B", "folder-a", "r1", ("root", "folder-a"), object_type="folder"),
            "file-a": ProviderObjectState("file-a", "old.pdf", "folder-a", "r1", ("root", "folder-a")),
            "file-b": ProviderObjectState("file-b", "move.pdf", "folder-a", "r1", ("root", "folder-a")),
        }
        self.calls: list[tuple[str, str, str, str]] = []
        self.effects = 0
        self.timeout: dict[tuple[str, str], str] = {}
        self.results: dict[str, ProviderObjectState] = {}

    def get_exact_state(self, object_id: str) -> ProviderObjectState:
        return self.states[object_id]

    def _next(self, before: ProviderObjectState, **changes: str) -> ProviderObjectState:
        version = int(before.revision.removeprefix("r")) + 1
        return replace(before, revision=f"r{version}", **changes)

    def rename_if_revision(self, object_id, new_name, *, expected_revision, operation_key):
        self.calls.append(("rename", object_id, expected_revision, operation_key))
        if operation_key in self.results:
            return self.results[operation_key]
        before = self.states[object_id]
        assert before.revision == expected_revision
        mode = self.timeout.get(("rename", object_id))
        if mode == "before":
            raise ConditionalMutationTimeout(effect_definitely_absent=True)
        if mode == "ambiguous":
            raise ConditionalMutationTimeout()
        after = self._next(before, name=new_name)
        self.states[object_id] = after
        self.results[operation_key] = after
        self.effects += 1
        if mode == "after":
            raise ConditionalMutationTimeout()
        return after

    def move_if_revision(
        self, object_id, new_parent_id, *, expected_parent_id, expected_revision, operation_key
    ):
        self.calls.append(("move", object_id, expected_revision, operation_key))
        if operation_key in self.results:
            return self.results[operation_key]
        before = self.states[object_id]
        assert before.revision == expected_revision
        assert before.parent_id == expected_parent_id
        mode = self.timeout.get(("move", object_id))
        if mode == "before":
            raise ConditionalMutationTimeout(effect_definitely_absent=True)
        if mode == "ambiguous":
            raise ConditionalMutationTimeout()
        parent = self.states[new_parent_id]
        after = self._next(
            before,
            parent_id=new_parent_id,
            ancestor_ids=parent.ancestor_ids + (new_parent_id,),
        )
        self.states[object_id] = after
        self.results[operation_key] = after
        self.effects += 1
        if mode == "after":
            raise ConditionalMutationTimeout()
        return after


def adapter_for(provider: str, client: FakeConditionalClient, *, enabled: bool = True):
    cls = GoogleDriveExactMutationAdapter if provider == "google_drive" else YandexDiskExactMutationAdapter
    return cls(client, enabled=enabled)


def one_command(provider: str, *, key: str = "mutation:live:001") -> MutationCommand:
    return MutationCommand(
        key,
        StorageBindingPin(7, provider, "connection-7", "root", 4),
        1,
        (StorageMutation("rename", "file-a", "r1", "folder-a", "old.pdf", "folder-a", "standard.pdf"),),
    )


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_disabled_by_default_and_missing_conditional_capability_fail_closed(provider):
    client = FakeConditionalClient(provider)
    with pytest.raises(ExactPreconditionUnavailable, match="disabled"):
        adapter_for(provider, client, enabled=False).object_revision("file-a")
    client.supports_exact_mutation_preconditions = False
    with pytest.raises(ExactPreconditionUnavailable, match="precondition"):
        adapter_for(provider, client).object_revision("file-a")
    assert client.calls == []


@pytest.mark.parametrize(
    ("provider", "legacy_client", "wrapper"),
    [
        ("google_drive", DriveClient.__new__(DriveClient), GoogleDriveExactMutationAdapter),
        ("yandex_disk", YandexDiskStorageAdapter.__new__(YandexDiskStorageAdapter), YandexDiskExactMutationAdapter),
    ],
)
def test_current_live_clients_are_explicitly_rejected_without_atomic_precondition(
    provider, legacy_client, wrapper
):
    assert legacy_client.provider == provider
    adapter = wrapper(legacy_client, enabled=True)
    assert adapter.health().ready is False
    with pytest.raises(ExactPreconditionUnavailable, match="precondition"):
        adapter.object_revision("opaque-object")


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_exact_revision_nested_folder_and_replay_have_one_provider_effect(provider):
    client = FakeConditionalClient(provider)
    adapter = adapter_for(provider, client)
    command = one_command(provider)
    store = ReceiptStore(command.pin)
    first = execute_mutation(command, adapter=adapter, store=store)
    replay = execute_mutation(command, adapter=adapter, store=store)
    assert replay is first
    assert first.outcome == "applied"
    assert client.states["file-a"].name == "standard.pdf"
    assert client.effects == 1


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_timeout_after_effect_reconciles_without_duplicate_call(provider):
    client = FakeConditionalClient(provider)
    client.timeout[("rename", "file-a")] = "after"
    command = one_command(provider)
    receipt = execute_mutation(command, adapter=adapter_for(provider, client), store=ReceiptStore(command.pin))
    assert receipt.outcome == "applied"
    assert client.effects == 1
    assert len(client.calls) == 1


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_timeout_before_effect_is_proved_absent_and_not_retried(provider):
    client = FakeConditionalClient(provider)
    client.timeout[("rename", "file-a")] = "before"
    command = one_command(provider)
    receipt = execute_mutation(command, adapter=adapter_for(provider, client), store=ReceiptStore(command.pin))
    assert receipt.outcome == "compensated"
    assert client.states["file-a"].name == "old.pdf"
    assert client.effects == 0
    assert len(client.calls) == 1


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_ambiguous_timeout_is_unknown_not_false_compensation(provider):
    client = FakeConditionalClient(provider)
    client.timeout[("rename", "file-a")] = "ambiguous"
    command = one_command(provider)
    receipt = execute_mutation(command, adapter=adapter_for(provider, client), store=ReceiptStore(command.pin))
    assert receipt.outcome == "unknown"
    assert client.effects == 0
    assert len(client.calls) == 1


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_partial_rename_then_move_failure_compensates_with_new_exact_revision(provider):
    client = FakeConditionalClient(provider)
    client.timeout[("move", "file-b")] = "before"
    command = MutationCommand(
        "mutation:live:partial",
        StorageBindingPin(7, provider, "connection-7", "root", 4),
        1,
        (
            StorageMutation("rename", "file-a", "r1", "folder-a", "old.pdf", "folder-a", "standard.pdf"),
            StorageMutation("move", "file-b", "r1", "folder-a", "move.pdf", "folder-b", "move.pdf"),
        ),
    )
    receipt = execute_mutation(command, adapter=adapter_for(provider, client), store=ReceiptStore(command.pin))
    assert receipt.outcome == "compensated"
    assert client.states["file-a"].name == "old.pdf"
    assert client.states["file-b"].parent_id == "folder-a"
    assert [call[0] for call in client.calls] == ["rename", "move", "rename"]
    assert client.calls[-1][2] == "r2"


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_move_to_deep_nested_folder_uses_ancestry_ids_not_path_prefix(provider):
    client = FakeConditionalClient(provider)
    adapter = adapter_for(provider, client)
    assert adapter.object_revision("file-b") == "r1"
    adapter.move_file("file-b", "folder-b", "folder-a", "root")
    assert client.states["file-b"].parent_id == "folder-b"
    assert client.states["file-b"].ancestor_ids == ("root", "folder-a", "folder-b")


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_revision_race_and_destination_outside_root_fail_before_effect(provider):
    client = FakeConditionalClient(provider)
    adapter = adapter_for(provider, client)
    assert adapter.object_revision("file-a") == "r1"
    client.states["file-a"] = replace(client.states["file-a"], revision="r2")
    with pytest.raises(ExactPreconditionUnavailable, match="changed"):
        adapter.get_file_meta("file-a")

    client.states["outside"] = ProviderObjectState(
        "outside", "outside", "account-root", "r1", (), object_type="folder"
    )
    adapter = adapter_for(provider, client)
    assert adapter.object_revision("file-b") == "r1"
    with pytest.raises(Exception, match="outside"):
        adapter.move_file("file-b", "outside", "folder-a", "root")
    assert client.calls == []
