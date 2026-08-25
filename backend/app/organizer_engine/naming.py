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
    doc_type = _FOLDER_TO_DOC_TYPE.get(folder)
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
