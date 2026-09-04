from __future__ import annotations

import re
from dataclasses import dataclass

from .types import DriveFile


_STRIP_PATTERN = re.compile(
    r"(?:[-_ ]?(?:v|версия|вер)\s?\d+(?:\.\d+)?)"
    r"|(?:[-_ ]?20\d{2}[-_.]\d{2}[-_.]\d{2})"
    r"|(?:[-_ ]?\d{2}[-_.]\d{2}[-_.]20\d{2})"
    r"|(?:\s?\(\d+\))",
    re.IGNORECASE,
)


def _base_name(filename: str) -> str:
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    return re.sub(r"\s+", " ", _STRIP_PATTERN.sub("", name)).strip().lower()


def _has_version_marker(filename: str) -> bool:
    """
    True only when the original basename contains an explicit marker
    that _STRIP_PATTERN understands as a version/copy/date suffix.

    Similarity by itself is NOT evidence that two files are versions.
    """
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    return _STRIP_PATTERN.search(name) is not None


@dataclass(slots=True)
class DuplicateGroup:
    files: list[DriveFile]
    kind: str


def find_duplicate_and_version_groups(
    files: list[DriveFile],
    similarity_threshold: float = 0.82,
) -> list[DuplicateGroup]:
    """
    Conservative duplicate/version detection.

    Exact duplicate:
      - ONLY same non-empty MD5 checksum.

    Probable version:
      - never based on an empty/very short normalized basename;
      - same extension required;
      - normalized basenames must be sufficiently descriptive;
      - similarity alone is advisory and never means "safe to delete".
    """
    non_folders = [f for f in files if not f.is_folder]

    # Exact duplicates are ONLY content-hash matches.
    by_hash: dict[str, list[DriveFile]] = {}
    for f in non_folders:
        if f.md5_checksum and (f.size is None or f.size > 0):
            by_hash.setdefault(f.md5_checksum, []).append(f)

    groups = [
        DuplicateGroup(group, "duplicate")
        for group in by_hash.values()
        if len(group) > 1
    ]

    exact_ids = {f.id for group in groups for f in group.files}
    remaining = [f for f in non_folders if f.id not in exact_ids]

    # Linear-time conservative version detection. Earlier pairwise fuzzy
    # comparison was quadratic for 10k-file folders. A version group now
    # requires an explicit marker and the same normalized basename/type.
    by_version_key: dict[tuple[str, str], list[DriveFile]] = {}
    for file in remaining:
        if file.size == 0 or not _has_version_marker(file.name):
            continue
        base = _base_name(file.name)
        if len(base) < 5:
            continue
        extension = file.name.rsplit(".", 1)[1].lower() if "." in file.name else ""
        by_version_key.setdefault((extension, base), []).append(file)

    groups.extend(
        DuplicateGroup(group, "version")
        for group in by_version_key.values()
        if len(group) > 1
    )

    return groups
