from __future__ import annotations

import difflib
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

    used: set[str] = set()

    for i, f1 in enumerate(remaining):
        if f1.id in used:
            continue

        # Empty files carry no content evidence and must never be
        # classified as probable versions.
        if f1.size == 0:
            continue

        base1 = _base_name(f1.name)

        # Critical guard:
        # "" / "v2" / "(2)"-like names must not cluster together.
        if len(base1) < 5:
            continue

        ext1 = f1.name.rsplit(".", 1)[1].lower() if "." in f1.name else ""

        cluster = [f1]

        for f2 in remaining[i + 1:]:
            if f2.id in used:
                continue

            # Empty files carry no content evidence and must never be
            # classified as probable versions.
            if f2.size == 0:
                continue

            base2 = _base_name(f2.name)
            if len(base2) < 5:
                continue

            ext2 = f2.name.rsplit(".", 1)[1].lower() if "." in f2.name else ""

            # Different file types are not versions of each other.
            if ext1 != ext2:
                continue

            # Similarity alone is not version evidence. At least one
            # original filename must contain an explicit version/copy/date
            # marker recognized by _STRIP_PATTERN.
            if not (_has_version_marker(f1.name) or _has_version_marker(f2.name)):
                continue

            # After removing the explicit marker, the normalized names must
            # still describe the same document closely enough.
            ratio = difflib.SequenceMatcher(None, base1, base2).ratio()

            # Similar names alone are not enough to call files versions.
            # At least one filename must contain an explicit version/copy/date
            # marker understood by _STRIP_PATTERN.
            has_explicit_marker = (
                _has_version_marker(f1.name)
                or _has_version_marker(f2.name)
            )

            if ratio >= similarity_threshold and has_explicit_marker:
                cluster.append(f2)
                used.add(f2.id)

        if len(cluster) > 1:
            used.add(f1.id)
            groups.append(DuplicateGroup(cluster, "version"))

    return groups
