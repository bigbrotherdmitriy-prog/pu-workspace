from __future__ import annotations

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
    if "." in original_filename:
        _, ext0 = original_filename.rsplit(".", 1)
        ext = "." + ext0
    parts = [p for p in (project, doc_type, counterparty, date_iso, version) if p]
    if not parts:
        return original_filename
    return NAME_SEPARATOR.join(parts) + ext
