from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class StorageObject:
    """Provider-neutral object consumed by the document and project core."""

    id: str
    name: str
    mime_type: str
    parent_id: str
    md5_checksum: str | None = None
    size: int | None = None
    modified_time: str | None = None
    content_text: str | None = None
    object_type: str | None = None
    provider: str | None = None
    parent_ids: tuple[str, ...] = ()
    provider_revision: str | None = None
    web_url: str | None = None
    source_path: str | None = None
    availability: str = "unknown"
    acl_state: str = "unknown"
    provider_metadata: dict[str, Any] | None = None
    shortcut_target_id: str | None = None
    shortcut_target_mime_type: str | None = None
    shortcut_target_resource_key: str | None = None

    @property
    def is_folder(self) -> bool:
        if self.object_type is not None:
            return self.object_type == "folder"
        # Compatibility for existing adapters while they start supplying object_type.
        return self.mime_type.endswith(".folder") or self.mime_type == "inode/directory"
