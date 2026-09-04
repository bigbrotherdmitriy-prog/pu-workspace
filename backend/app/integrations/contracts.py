from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from app.core.integration_types import StorageObject


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    ready: bool
    detail: str = ""


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
