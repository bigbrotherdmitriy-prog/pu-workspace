# Акт закрытия MVP-1 (запуск проекта, safe-copy, provider locator)

Дата независимой сверки: 09.10.2026. Метод: read-only запросы к production-БД (`puw-primary-next-primary-db`) и поиск по тестам, независимо от `docs/CURRENT_MVP_AUDIT_RU.md` (30.08.2026) и `docs/audits/mvp1-completion.md` (04.09.2026).

**Явно не входит в этот акт** (раздел «В» плана закрытия): живая OAuth-приёмка на реальных тестовых аккаунтах Google Drive/Яндекс Диск — это действие владельца с реальными внешними учётными данными, не проверяется read-only сверкой с текущего прод-окружения.

## Подтверждено на реальных данных прода

**Provider locator и safe-copy реально используются**: 18 строк `source_folders`, 19 снапшотов (`workspace_snapshots`), из них 17 — `ready`, 2 — `failed`. Оба отказа — ожидаемая защита, не дефект: «Folder contains more than 12000 items. Safe copy was not started» (проект №3) — система сознательно отказывается копировать слишком большие папки, а не падает молча.

**Важное уточнение по фактическому охвату provider-locator**: из 18 реальных подключений **все 18 — Google Drive** (`google_drive`/`google_drive_managed`); ни одного реального подключения Яндекс Диска на проде нет. Это прямо подтверждает давно известный пробел (см. `ADR` и аудит от 04.09.2026): точный provider locator для Яндекс Диска существует в коде (`provider` — свободное поле, не ограничено одним провайдером), но никогда не проверялся на реальном использовании, только Google Drive.

## Подтверждено тестами (поведенческое покрытие, не живые данные)

- **Идемпотентность переименования**: `test_standard_name_is_idempotent_and_collapses_duplicate_prefixes` (`test_bulk_standardization.py`).
- **Откат (rollback) при массовом переименовании**: `test_bulk_standardization_changes_safe_copy_and_logs_rollback_data`.
- **Изоляция частичного сбоя** (один сломанный файл не откатывает остальные успешные): `test_one_broken_file_does_not_rollback_successful_files`.
- **Повторный запуск не применяет уже применённые операции повторно**: `test_repeated_bulk_standardization_does_not_apply_completed_operations_again`.
- **Защита удаления договоров**: `test_contract_update_accepts_commercial_fields_and_delete_requires_confirmation` (удаление требует подтверждения) и `test_physical_contract_delete_is_blocked_by_document_and_tree_links` (физическое удаление блокируется при наличии связанных документов/дерева).

## Не проверялось в рамках этого акта

- Живой OAuth-acceptance на изолированных тестовых аккаунтах Google Drive/Яндекс Диск.
- Provider-native revision после изменения реального файла, latency/delta-scan на объёмах 1 000–10 000 объектов.
- Браузерный E2E с живым provider picker (только synthetic fixtures по состоянию на 04.09.2026, не перепроверялось здесь).

## Заключение

Критерии, проверяемые без живых внешних учётных данных, подтверждены: как реальными данными прода (provider locator, safe-copy, с явной оговоркой про отсутствие реального покрытия Яндекс Диска), так и поведенческими тестами (идемпотентность, rollback, защита удаления договоров). **MVP-1 закрыт в этой части.** Живой provider-gate (реальные тестовые аккаунты обоих провайдеров) остаётся открытым пунктом, требующим действия владельца — не входит в этот акт и не блокирует его.
