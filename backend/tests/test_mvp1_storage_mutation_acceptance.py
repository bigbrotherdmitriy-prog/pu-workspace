from dataclasses import replace

import pytest

from app.core.integration_types import StorageObject
from app.organizer_engine.storage_mutations import (
    MutationCommand,
    MutationConflict,
    MutationReceipt,
    StorageBindingPin,
    StorageMutation,
    execute_mutation,
    rollback_mutation,
)


class Receipts:
    def __init__(self, pin):
        self.pin = pin
        self.version = 4
        self.rows: dict[str, MutationReceipt] = {}

    def current_binding(self, project_id):
        if project_id != self.pin.project_id:
            raise MutationConflict("resource_unavailable")
        return self.pin

    def current_record_version(self, project_id):
        return self.version

    def get(self, key):
        return self.rows.get(key)

    def append(self, receipt):
        if receipt.command_key in self.rows:
            raise AssertionError("immutable receipt overwrite")
        self.rows[receipt.command_key] = receipt
        self.version = receipt.resulting_record_version


class Provider:
    def __init__(self, provider):
        self.provider = provider
        self.items = {
            "nested/file-a": StorageObject("nested/file-a", "old.pdf", "application/pdf", "root/a/b"),
            "nested/file-b": StorageObject("nested/file-b", "move.xlsx", "application/xlsx", "root/a/b"),
        }
        self.revisions = {key: "rev-1" for key in self.items}
        self.calls = []
        self.fail_object = None
        self.fail_compensation = False

    def object_revision(self, object_id): return self.revisions[object_id]
    def get_file_meta(self, object_id): return self.items[object_id]
    def assert_inside_copy(self, object_id, root):
        if not object_id.startswith("nested/") or root != "root/a": raise ValueError("outside")
    def rename_file(self, object_id, new_name, _root):
        self.calls.append(("rename", object_id, new_name))
        if object_id == self.fail_object or (self.fail_compensation and new_name == "old.pdf"):
            raise RuntimeError("synthetic provider failure")
        self.items[object_id].name = new_name
        self.revisions[object_id] = f"rev-{len(self.calls) + 1}"
    def move_file(self, object_id, new_parent, _old_parent, _root):
        self.calls.append(("move", object_id, new_parent))
        if object_id == self.fail_object: raise RuntimeError("synthetic provider failure")
        self.items[object_id].parent_id = new_parent
        self.revisions[object_id] = f"rev-{len(self.calls) + 1}"
    def list_children(self, _id): return []
    def create_folder(self, *_args): raise NotImplementedError
    def trash_safe_copy(self, _id): raise NotImplementedError
    def get_object(self, object_id): return self.items[object_id]
    def walk_tree(self, *_args): return list(self.items.values())
    def read_bytes(self, *_args): raise NotImplementedError
    def copy_folder_tree(self, *_args, **_kwargs): raise NotImplementedError
    def health(self): raise NotImplementedError


def command(provider="google_drive"):
    pin = StorageBindingPin(7, provider, "connection-7", "root/a", 3)
    return MutationCommand(
        "mutation:project7:001", pin, 4,
        (
            StorageMutation("rename", "nested/file-a", "rev-1", "root/a/b", "old.pdf", "root/a/b", "standard.pdf"),
            StorageMutation("move", "nested/file-b", "rev-1", "root/a/b", "move.xlsx", "root/a/b/c", "move.xlsx"),
        ),
    )


@pytest.mark.parametrize("provider", ["google_drive", "yandex_disk"])
def test_nested_mutations_are_exact_idempotent_and_rollbackable(provider):
    cmd = command(provider)
    store = Receipts(cmd.pin)
    adapter = Provider(provider)
    receipt = execute_mutation(cmd, adapter=adapter, store=store)
    replay = execute_mutation(cmd, adapter=adapter, store=store)
    assert replay is receipt
    assert receipt.outcome == "applied"
    assert adapter.items["nested/file-a"].name == "standard.pdf"
    assert adapter.items["nested/file-b"].parent_id == "root/a/b/c"

    rolled = rollback_mutation(receipt, rollback_key="rollback:project7:001",
                               expected_record_version=5, adapter=adapter, store=store)
    assert rolled.outcome == "rolled_back"
    assert adapter.items["nested/file-a"].name == "old.pdf"
    assert adapter.items["nested/file-b"].parent_id == "root/a/b"


def test_stale_project_connection_folder_revision_and_cas_fail_before_provider_effect():
    cmd = command()
    for changed in (
        replace(cmd.pin, project_id=8), replace(cmd.pin, connection_id="old-connection"),
        replace(cmd.pin, folder_id="old/root"), replace(cmd.pin, binding_version=2),
    ):
        store = Receipts(cmd.pin)
        adapter = Provider(cmd.pin.provider)
        with pytest.raises(MutationConflict):
            execute_mutation(replace(cmd, pin=changed), adapter=adapter, store=store)
        assert adapter.calls == []
    store = Receipts(cmd.pin)
    with pytest.raises(MutationConflict, match="record_version"):
        execute_mutation(replace(cmd, expected_record_version=3), adapter=Provider(cmd.pin.provider), store=store)
    adapter = Provider(cmd.pin.provider)
    adapter.revisions["nested/file-a"] = "rev-2"
    with pytest.raises(MutationConflict, match="source_revision"):
        execute_mutation(cmd, adapter=adapter, store=Receipts(cmd.pin))
    assert adapter.calls == []


def test_idempotency_conflict_and_partial_failure_have_immutable_receipts():
    cmd = command()
    store = Receipts(cmd.pin)
    adapter = Provider(cmd.pin.provider)
    receipt = execute_mutation(cmd, adapter=adapter, store=store)
    with pytest.raises(MutationConflict, match="idempotency"):
        execute_mutation(replace(cmd, operations=(replace(cmd.operations[0], new_name="other.pdf"),)),
                         adapter=adapter, store=store)
    with pytest.raises(AttributeError):
        receipt.outcome = "tampered"

    failed_store = Receipts(cmd.pin)
    failed_adapter = Provider(cmd.pin.provider)
    failed_adapter.fail_object = "nested/file-b"
    compensated = execute_mutation(cmd, adapter=failed_adapter, store=failed_store)
    assert compensated.outcome == "compensated"
    assert failed_adapter.items["nested/file-a"].name == "old.pdf"

    partial_store = Receipts(cmd.pin)
    partial_adapter = Provider(cmd.pin.provider)
    partial_adapter.fail_object = "nested/file-b"
    partial_adapter.fail_compensation = True
    partial = execute_mutation(cmd, adapter=partial_adapter, store=partial_store)
    assert partial.outcome == "partial_failure"
    assert partial_store.get(cmd.command_key) is partial
