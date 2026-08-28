from __future__ import annotations

from dataclasses import dataclass

from app.core.integration_types import StorageObject

FOLDER_MIME = "application/vnd.google-apps.folder"


# Backwards-compatible import for adapters and third-party callers. Domain code
# uses StorageObject; removing this alias would needlessly break the live Google adapter.
DriveFile = StorageObject


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
    source_modified_at: str | None = None
    source_checksum: str | None = None
