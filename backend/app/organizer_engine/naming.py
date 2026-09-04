from __future__ import annotations

import re

from .config import DEFAULT_FOLDER, NAME_SEPARATOR

_FOLDER_TO_DOC_TYPE = {
    "01_УПРАВЛЕНИЕ ПРОЕКТОМ": "Управление проектом",
    "02_ДОГОВОРЫ И ЮРИДИЧЕСКИЕ": "Договор",
    "03_ФИНАНСЫ И СМЕТЫ": "Финансы",
    "04_ПРОЕКТИРОВАНИЕ": "Проектирование",
    "05_ЗАКУПКИ И ПОСТАВКИ": "Закупка",
    "06_ПОДРЯДЧИКИ И КОНТРАГЕНТЫ": "Контрагент",
    "07_ПЕРЕПИСКА И СОГЛАСОВАНИЯ": "Переписка",
    "08_ИСПОЛНЕНИЕ И ОТЧЁТНОСТЬ": "Исполнение",
    "09_ЗАКРЫТИЕ ПРОЕКТА": "Закрытие проекта",
    "99_АРХИВ": "Архив",
    "00_НЕРАЗОБРАННОЕ": None,
}


def _specific_doc_type(original_filename: str, folder: str, fallback: str | None) -> str | None:
    stem = original_filename.rsplit(".", 1)[0]
    if folder == "01_УПРАВЛЕНИЕ ПРОЕКТОМ" and re.search(
        r"(?<![0-9a-zа-яё])(?:гпр|график\s+производства\s+работ)(?![0-9a-zа-яё])",
        stem,
        re.IGNORECASE,
    ):
        return "ГПР"
    if folder == "03_ФИНАНСЫ И СМЕТЫ" and re.search(
        r"(?<![0-9a-zа-яё])(?:ддс|движение\s+денежных\s+средств|плат[её]жный\s+календарь)(?![0-9a-zа-яё])",
        stem,
        re.IGNORECASE,
    ):
        return "ДДС"
    return fallback


def build_name(
    original_filename: str,
    folder: str,
    project: str | None = None,
    counterparty: str | None = None,
    date_iso: str | None = None,
    version: str | None = None,
) -> str:
    if folder == DEFAULT_FOLDER:
        return original_filename
    doc_type = _specific_doc_type(original_filename, folder, _FOLDER_TO_DOC_TYPE.get(folder))
    ext = ""
    stem = original_filename
    if "." in original_filename:
        stem, ext0 = original_filename.rsplit(".", 1)
        ext = "." + ext0
    # Preserve the human subject/document number from the original basename.
    # Metadata is added around it; the organizer never replaces it with a
    # generic category-only filename.
    stem = re.sub(r"\s+", " ", stem).strip(" .-_—")
    parts = [p for p in (project, doc_type, counterparty, stem, date_iso, version) if p]
    parts = list(dict.fromkeys(parts))
    if not parts:
        return original_filename
    candidate = NAME_SEPARATOR.join(parts) + ext
    # Google Drive permits long names, but a conservative cap keeps names
    # portable across sync clients while retaining the extension.
    if len(candidate) > 240:
        candidate = candidate[: 240 - len(ext)].rstrip(" .-_—") + ext
    return candidate


def build_standard_name(
    original_filename: str,
    folder: str,
    project: str | None = None,
) -> str:
    """Build a portable, readable name without discarding the original subject."""
    ext = ""
    stem = original_filename
    if "." in original_filename:
        stem, ext0 = original_filename.rsplit(".", 1)
        ext = "." + ext0.lower()
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .-_—") or "Документ"
    doc_type = _specific_doc_type(
        original_filename,
        folder,
        _FOLDER_TO_DOC_TYPE.get(folder) or "Неразобранное",
    )
    prefix_parts = [p for p in (project, doc_type) if p]
    prefix = NAME_SEPARATOR.join(prefix_parts)
    # A retry may encounter files that Drive changed before the database
    # transaction was rolled back. Collapse any already-present (even doubled)
    # standard prefix so retries converge to exactly one readable name.
    if prefix:
        marker = prefix + NAME_SEPARATOR
        while stem.casefold().startswith(marker.casefold()):
            stem = stem[len(marker):].strip(" .-_—") or "Документ"
    parts = list(dict.fromkeys(p for p in (*prefix_parts, stem) if p))
    candidate = NAME_SEPARATOR.join(parts) + ext
    if len(candidate) > 240:
        candidate = candidate[: 240 - len(ext)].rstrip(" .-_—") + ext
    return candidate
