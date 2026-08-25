from __future__ import annotations

from dataclasses import dataclass

FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass(slots=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    parent_id: str
    md5_checksum: str | None = None
    size: int | None = None
    modified_time: str | None = None
    content_text: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME


@dataclass(slots=True)
class Classification:
    folder: str
    confidence: float
    reasoning: str
    is_ambiguous: bool = False


@dataclass(slots=True)
class ProposalItem:
    file_id: str
    current_name: str
    current_parent_id: str
    proposed_name: str
    proposed_folder: str
    kind: str
    special_case: str | None = None
    confidence: float = 1.0
    reasoning: str = ""
