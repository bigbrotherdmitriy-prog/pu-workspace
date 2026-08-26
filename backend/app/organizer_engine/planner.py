from __future__ import annotations

from collections import Counter

from .classifier import classify, extract_date, extract_version
from .config import DEFAULT_FOLDER, MIN_AUTO_CONFIDENCE
from .dedup import find_duplicate_and_version_groups
from .naming import build_name
from .types import DriveFile, ProposalItem


def _modified_sort_key(file: DriveFile) -> tuple[str, str]:
    return (file.modified_time or "", file.id)


def _build_folder_context(
    file: DriveFile,
    folders_by_id: dict[str, DriveFile],
    max_depth: int = 30,
) -> str:
    """
    Reconstruct the already-fetched ancestor path entirely in memory.

    walk_tree() supplies folder objects as well as files, so this performs
    no additional Google Drive requests.
    """
    parts: list[str] = []
    current_id = file.parent_id
    visited: set[str] = set()

    for _ in range(max_depth):
        if not current_id or current_id in visited:
            break

        visited.add(current_id)

        folder = folders_by_id.get(current_id)
        if not folder:
            break

        parts.append(folder.name)
        current_id = folder.parent_id

    parts.reverse()
    return " / ".join(parts)


def build_proposal(
    files: list[DriveFile],
    project_name: str | None = None,
    confirmed_rules: list[dict] | None = None,
) -> list[ProposalItem]:
    non_folders = [f for f in files if not f.is_folder]
    folders_by_id = {f.id: f for f in files if f.is_folder}

    # Detection is advisory only. Special cases are surfaced in the proposal,
    # never deleted, archived, renamed, or applied without explicit review.
    special_by_id: dict[str, str] = {}
    for group in find_duplicate_and_version_groups(non_folders):
        for grouped_file in group.files:
            # Exact content evidence wins over a weaker version relationship.
            if group.kind == "duplicate" or grouped_file.id not in special_by_id:
                special_by_id[grouped_file.id] = group.kind
    draft: list[ProposalItem] = []

    for file in non_folders:
        context = _build_folder_context(file, folders_by_id)

        result = classify(
            file.name,
            confirmed_rules,
            context=context,
            content=file.content_text,
        )

        special = special_by_id.get(file.id)

        unsafe_to_rename = (
            result.is_ambiguous
            or result.confidence < MIN_AUTO_CONFIDENCE
            or special in {"duplicate", "version"}
        )

        target_folder = result.folder

        if result.is_ambiguous or result.confidence < MIN_AUTO_CONFIDENCE:
            target_folder = DEFAULT_FOLDER

        proposed = file.name
        if not unsafe_to_rename and target_folder != DEFAULT_FOLDER:
            proposed = build_name(
                file.name,
                target_folder,
                project=project_name,
                date_iso=extract_date(file.name),
                version=extract_version(file.name),
            )

        reasoning = result.reasoning

        if context:
            reasoning += f" Исходный контекст: {context}."

        if special == "duplicate":
            reasoning += (
                " Обнаружен точный MD5-дубликат. "
                "Это дополнительная копия; имя сохраняется. "
                "Автоматическое удаление запрещено."
            )

        elif special == "version":
            reasoning += (
                " Обнаружена вероятная версия. "
                "Имя сохраняется; актуальность должен подтвердить пользователь."
            )

        if result.is_ambiguous or result.confidence < MIN_AUTO_CONFIDENCE:
            reasoning += (
                " Низкая уверенность: файл оставлен с исходным именем "
                "и направлен в неразобранное."
            )

        kind = "rename_move" if proposed != file.name else "move"

        draft.append(
            ProposalItem(
                file_id=file.id,
                current_name=file.name,
                current_parent_id=file.parent_id,
                proposed_name=proposed,
                proposed_folder=target_folder,
                kind=kind,
                special_case=special or (
                    "ambiguous"
                    if result.is_ambiguous
                    or result.confidence < MIN_AUTO_CONFIDENCE
                    else None
                ),
                confidence=result.confidence,
                reasoning=reasoning,
                source_modified_at=file.modified_time,
                source_checksum=file.md5_checksum,
            )
        )

    # Target-name collision safety.
    counts = Counter(
        (item.proposed_folder, item.proposed_name)
        for item in draft
    )

    result_items: list[ProposalItem] = []

    for item in draft:
        key = (item.proposed_folder, item.proposed_name)

        if counts[key] > 1 and item.proposed_name != item.current_name:
            item.proposed_name = item.current_name
            item.kind = "move"
            item.special_case = item.special_case or "collision"
            item.reasoning += (
                " Обнаружена коллизия целевого имени; "
                "автоматическое переименование отменено."
            )

        result_items.append(item)

    return result_items
