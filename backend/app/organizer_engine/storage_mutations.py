"""Fail-closed contract for version-pinned storage rename/move and compensation.

This module deliberately owns no queue and performs no provider discovery.  A
durable-job handler may call it only after resolving an exact project binding.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Literal, Protocol

from app.integrations.contracts import MutableStorageAdapter


Provider = Literal["google_drive", "yandex_disk"]
Outcome = Literal["applied", "compensated", "partial_failure", "rolled_back", "unknown"]


class MutationConflict(ValueError):
    pass


@dataclass(frozen=True)
class StorageBindingPin:
    project_id: int
    provider: Provider
    connection_id: str
    folder_id: str
    binding_version: int


@dataclass(frozen=True)
class StorageMutation:
    kind: Literal["rename", "move"]
    object_id: str
    source_revision: str
    old_parent_id: str
    old_name: str
    new_parent_id: str
    new_name: str


@dataclass(frozen=True)
class MutationCommand:
    command_key: str
    pin: StorageBindingPin
    expected_record_version: int
    operations: tuple[StorageMutation, ...]


@dataclass(frozen=True)
class MutationReceipt:
    command_key: str
    payload_hash: str
    pin: StorageBindingPin
    resulting_record_version: int
    outcome: Outcome
    applied_operations: tuple[StorageMutation, ...]


class VersionedMutableStorageAdapter(MutableStorageAdapter, Protocol):
    def object_revision(self, object_id: str) -> str: ...


class MutationReceiptStore(Protocol):
    def current_binding(self, project_id: int) -> StorageBindingPin: ...
    def current_record_version(self, project_id: int) -> int: ...
    def get(self, command_key: str) -> MutationReceipt | None: ...
    def append(self, receipt: MutationReceipt) -> None: ...


def _hash(command: MutationCommand) -> str:
    payload = {
        "command_key": command.command_key,
        "pin": command.pin.__dict__,
        "expected_record_version": command.expected_record_version,
        "operations": [item.__dict__ for item in command.operations],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def command_hash(command: MutationCommand) -> str:
    """Stable, content-free digest used by durable attempt/receipt ledgers."""
    return _hash(command)


def _validate_scope(command: MutationCommand, store: MutationReceiptStore) -> str:
    if len(command.command_key) < 8 or not command.operations:
        raise MutationConflict("invalid_command")
    current = store.current_binding(command.pin.project_id)
    if current != command.pin:
        raise MutationConflict("stale_or_cross_project_binding")
    for item in command.operations:
        if not all((item.object_id, item.source_revision, item.old_parent_id, item.old_name,
                    item.new_parent_id, item.new_name)):
            raise MutationConflict("incomplete_exact_source_pin")
    return _hash(command)


def execute_mutation(
    command: MutationCommand,
    *,
    adapter: VersionedMutableStorageAdapter,
    store: MutationReceiptStore,
    success_outcome: Literal["applied", "rolled_back"] = "applied",
) -> MutationReceipt:
    """Apply exact operations, compensating in reverse on any provider failure."""
    payload_hash = _validate_scope(command, store)
    prior = store.get(command.command_key)
    if prior is not None:
        if prior.payload_hash != payload_hash:
            raise MutationConflict("idempotency_key_conflict")
        return prior
    if store.current_record_version(command.pin.project_id) != command.expected_record_version:
        raise MutationConflict("record_version_conflict")

    # Full dry-run before the first external mutation.
    for item in command.operations:
        adapter.assert_inside_copy(item.object_id, command.pin.folder_id)
        if adapter.object_revision(item.object_id) != item.source_revision:
            raise MutationConflict("source_revision_conflict")
        current = adapter.get_file_meta(item.object_id)
        if current.parent_id != item.old_parent_id or current.name != item.old_name:
            raise MutationConflict("source_state_conflict")

    applied: list[StorageMutation] = []
    try:
        for item in command.operations:
            if item.kind == "rename":
                adapter.rename_file(item.object_id, item.new_name, command.pin.folder_id)
            else:
                adapter.move_file(item.object_id, item.new_parent_id, item.old_parent_id, command.pin.folder_id)
            applied.append(item)
    except Exception:
        compensation_failed = False
        for item in reversed(applied):
            try:
                if item.kind == "rename":
                    adapter.rename_file(item.object_id, item.old_name, command.pin.folder_id)
                else:
                    adapter.move_file(item.object_id, item.old_parent_id, item.new_parent_id, command.pin.folder_id)
            except Exception:
                compensation_failed = True
        receipt = MutationReceipt(
            command_key=command.command_key,
            payload_hash=payload_hash,
            pin=command.pin,
            resulting_record_version=command.expected_record_version + 1,
            outcome="partial_failure" if compensation_failed else "compensated",
            applied_operations=tuple(applied),
        )
        store.append(receipt)
        return receipt

    receipt = MutationReceipt(
        command_key=command.command_key,
        payload_hash=payload_hash,
        pin=command.pin,
        resulting_record_version=command.expected_record_version + 1,
        outcome=success_outcome,
        applied_operations=tuple(applied),
    )
    store.append(receipt)
    return receipt


def rollback_mutation(
    receipt: MutationReceipt,
    *,
    rollback_key: str,
    expected_record_version: int,
    adapter: VersionedMutableStorageAdapter,
    store: MutationReceiptStore,
) -> MutationReceipt:
    command = MutationCommand(
        command_key=rollback_key,
        pin=receipt.pin,
        expected_record_version=expected_record_version,
        operations=tuple(
            StorageMutation(
                kind=item.kind,
                object_id=item.object_id,
                source_revision=adapter.object_revision(item.object_id),
                old_parent_id=item.new_parent_id,
                old_name=item.new_name,
                new_parent_id=item.old_parent_id,
                new_name=item.old_name,
            )
            for item in reversed(receipt.applied_operations)
        ),
    )
    return execute_mutation(
        command, adapter=adapter, store=store, success_outcome="rolled_back"
    )
