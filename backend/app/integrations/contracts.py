from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from app.core.integration_types import StorageObject


class StorageAdapterError(RuntimeError):
    """Provider-neutral storage failure exposed to Core and API layers."""


class StorageAccessDenied(StorageAdapterError):
    pass


class StorageCredentialsExpired(StorageAdapterError):
    pass


class StorageRateLimited(StorageAdapterError):
    pass


class StorageQuotaExceeded(StorageAdapterError):
    pass


class StorageUnavailable(StorageAdapterError):
    pass


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    ready: bool
    detail: str = ""


@dataclass(slots=True)
class StorageCopyResult:
    copy_root_id: str
    copy_root_name: str
    id_map: dict[str, str]
    item_count: int


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    external_id: str
    sender: str
    text: str
    attachments: Sequence[StorageObject] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class IntegrationAdapter(Protocol):
    provider: str

    def health(self) -> AdapterHealth: ...


@runtime_checkable
class StorageAdapter(IntegrationAdapter, Protocol):
    def get_object(self, object_id: str) -> StorageObject: ...
    def list_children(self, folder_id: str) -> list[StorageObject]: ...
    def walk_tree(self, root_folder_id: str, limit: int) -> list[StorageObject]: ...
    def read_bytes(self, object_id: str, max_bytes: int) -> tuple[bytes, str]: ...
    def copy_folder_tree(
        self, source_folder_id: str, new_parent_id: str, source_name: str,
        source_items: list[StorageObject] | None = None, *, idempotency_key: str | None = None,
    ) -> StorageCopyResult: ...


@runtime_checkable
class MutableStorageAdapter(StorageAdapter, Protocol):
    """Mutation boundary restricted to an explicitly created safe-copy tree."""

    def get_file_meta(self, object_id: str) -> StorageObject: ...
    def create_folder(self, name: str, parent_id: str) -> str: ...
    def assert_inside_copy(self, object_id: str, copy_root_id: str) -> None: ...
    def rename_file(self, object_id: str, new_name: str, copy_root_id: str) -> None: ...
    def move_file(self, object_id: str, new_parent_id: str, old_parent_id: str, copy_root_id: str) -> None: ...
    def trash_safe_copy(self, copy_root_id: str) -> None: ...


@runtime_checkable
class ChannelAdapter(IntegrationAdapter, Protocol):
    def receive(self, limit: int = 100) -> list[ChannelMessage]: ...
    def send(self, destination: str, text: str) -> str: ...


@runtime_checkable
class AIProviderAdapter(IntegrationAdapter, Protocol):
    def analyze_document(self, text: str, filename: str) -> dict[str, Any]: ...
    def analyze_message(self, text: str, context_name: str) -> dict[str, Any]: ...


@runtime_checkable
class ActionAdapter(IntegrationAdapter, Protocol):
    """Publishes approved Core actions to an external work-management provider."""

    def sync_tasks(self, tasks: Sequence[Any], force_update: bool = False) -> tuple[int, int]: ...
    def sync_calendar(self, tasks: Sequence[Any], force_update: bool = False) -> tuple[int, int]: ...
