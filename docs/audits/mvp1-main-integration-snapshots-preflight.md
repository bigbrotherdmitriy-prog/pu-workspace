# MVP-1 Main Integration — Snapshots Preflight

Дата: 2026-09-10

Ветка: `codex/mvp1-main-integration`

Базовый HEAD перед Snapshots: `e9e03ad1a4f1b1f66f30107b8c81aba1da3d3c45`

Этот документ фиксирует результат read-only анализа области Snapshots перед selective port из `codex/mvp1-phase2-review` в актуальный `main`. Он является планом следующей сессии. Реализация Snapshots в момент составления документа не начиналась.

## 1. Статус

**Implementation: NOT STARTED.**

Текущая ветка содержит завершённую OAuth-область и не содержит нового Snapshot implementation поверх неё. OCR также не начинался.

## 2. Обязательная матрица проверок

| Инвариант | Подтверждающий тест | Состояние до реализации |
|---|---|---|
| Snapshot остаётся read-only | `backend/tests/test_virtual_workspace_api.py::test_snapshot_analysis_is_explicitly_read_only_in_contract` | Уже есть в main |
| Snapshot не запускает safe-copy автоматически | `backend/tests/test_virtual_workspace_api.py::test_connected_folder_snapshot_does_not_automatically_create_safe_copy` | Уже есть в main |
| Recovery snapshot не запускает mutation | `backend/tests/test_virtual_workspace_api.py::test_safe_copy_recovery_is_not_started_by_legacy_virtual_analyzer` | Уже есть в main |
| Breadcrumb строится от root к текущему узлу | `backend/tests/test_virtual_workspace_api.py::test_nested_drive_breadcrumb_is_root_to_current_folder` | Уже есть; требуется объединить с полным envelope |
| Mutation запускается только отдельным запросом | `backend/tests/test_storage_binding_validation.py::test_ready_snapshot_waits_for_explicit_safe_copy_request` | Уже есть в main |
| Поддерживается навигация с одинаковыми именами | `backend/tests/test_storage_binding_validation.py::test_navigation_three_levels_back_and_duplicate_names` | Уже есть в main |
| Повторная постановка не создаёт duplicate job | `backend/tests/test_storage_binding_validation.py::test_http_repeat_does_not_duplicate_queued_job` | Уже есть в main |
| Сохраняются exact metadata и вычисленный virtual path | `backend/tests/test_mvp1_snapshot_metadata_envelope.py::test_snapshot_envelope_preserves_exact_metadata_and_virtual_path` | Есть только в review; адаптировать |
| Отсутствующие provider-поля остаются явно `unknown` | `backend/tests/test_mvp1_snapshot_metadata_envelope.py::test_missing_provider_values_are_explicit_unknown_not_invented` | Есть только в review; адаптировать |
| API возвращает полный безопасный metadata envelope | `backend/tests/test_mvp1_snapshot_metadata_envelope.py::test_virtual_node_api_exposes_complete_safe_metadata_envelope` | Есть только в review; адаптировать |
| Metadata migration последовательна и additive | `backend/tests/test_mvp1_snapshot_metadata_envelope.py::test_snapshot_metadata_migration_is_sequential_and_additive` | Переписать на базу `e16a1c2d3f40` |
| Shortcut metadata сохраняется без перехода к target | `backend/tests/test_mvp1_google_metadata_contract.py::test_shared_drive_and_shortcut_metadata_are_preserved_without_following_target` | Есть только в review; адаптировать |
| Все страницы читаются без пропусков и усечения | `backend/tests/test_mvp1_google_metadata_contract.py::test_pagination_uses_next_page_token_without_duplicate_or_truncation` | Есть только в review; адаптировать |
| Pagination и retry не создают дубликаты | `backend/tests/test_mvp1_google_metadata_contract.py::test_all_pages_are_read_and_read_rate_limit_is_retried_bounded` | Есть только в review; адаптировать |
| Rate-limit 403 отличается от обычного forbidden | `backend/tests/test_mvp1_google_metadata_contract.py::test_google_403_rate_limit_reason_is_retried_but_plain_forbidden_is_not` | Есть только в review; адаптировать |
| Page size строго ограничен | `backend/tests/test_mvp1_google_metadata_contract.py::test_google_list_page_size_is_strictly_bounded` | Есть только в review; адаптировать |
| Shortcut migration линейна | `backend/tests/test_mvp1_google_metadata_contract.py::test_shortcut_metadata_migration_is_sequential_current_head` | Переписать на новую head |
| Завершённые `VirtualNode` не изменяются задним числом | Отдельный тест отсутствует | Обязательный пробел: добавить |
| Конкурентная публикация одного snapshot имеет одного победителя | PostgreSQL-тест отсутствует | Обязательный пробел: добавить |
| Existing `VirtualNode` корректно backfill после миграции | PostgreSQL-тест отсутствует | Обязательный пробел: добавить |

Решение области: main уже разделяет snapshot и mutation; review добавляет immutable metadata envelope, shortcut metadata, полную pagination и virtual tree. Нужно объединить инварианты, не заменяя `workspace.py` целиком.

## 3. Уровни доказательства

### Offline / synthetic

- Существующая граница snapshot/mutation уже покрыта тестами main.
- Metadata envelope, shortcut metadata и расширенная pagination пока существуют только в review-ветке.
- После selective port нужны адаптированные review-тесты и новый явный тест неизменяемости завершённых nodes.

### Tested runtime / PostgreSQL

**Статус Snapshots: NOT RUN.**

Обязательный PostgreSQL runtime должен доказать:

1. применение новых Snapshot-миграций поверх `e16a1c2d3f40`;
2. корректный backfill существующих `VirtualNode`;
3. сохранение JSON/metadata envelope реальным PostgreSQL;
4. настоящую конкурентную публикацию одного snapshot;
5. одного победителя без дубликатов;
6. отсутствие изменения уже опубликованных nodes.

Offline/SQLite результаты нельзя засчитывать как PostgreSQL runtime PASS.

## 4. Полный backend regression

Последний полный baseline-прогон выполнен на OAuth HEAD `e9e03ad1a4f1b1f66f30107b8c81aba1da3d3c45`:

- `1480 passed`;
- `27 skipped`;
- `0 failed`;
- `0 errors`.

Это baseline перед Snapshots, а не доказательство завершения Snapshot implementation. После реализации требуется новый полный backend regression с фактическими итоговыми числами.

## 5. Alembic

Текущая единственная релевантная хвостовая цепочка:

```text
d04e8a6c31f2
└── e16a1c2d3f40  (current head)
```

Review-миграции `a54f001c0a23` и `a54f001c0a24` напрямую переносить нельзя. Требуется новая линейная цепочка:

```text
d04e8a6c31f2
└── e16a1c2d3f40
    └── <new snapshot metadata revision>
        └── <new shortcut metadata revision>  (future head)
```

Идентификаторы новых ревизий ещё не создавались. Merge migration не планируется, если цепочка останется линейной.

## 6. Secrets scan

Snapshot-коммита пока нет, поэтому финальный Snapshot secrets scan ещё не выполнялся.

Для текущего OAuth-коммита ранее подтверждено:

- реальных секретов: 0;
- live tokens/credentials: 0;
- допускаются только placeholders в `.env.example`.

Перед будущим Snapshot-коммитом нужно повторить scan по полному diff и подтвердить те же условия.

## 7. Предполагаемый состав будущего коммита

Фактически Snapshot-реализацией сейчас изменено `0` файлов. Ожидаемый минимальный набор:

- `backend/app/models/workspace.py`;
- `backend/app/api/workspace.py` — только точечные изменения;
- `backend/app/integrations/storage.py`;
- `backend/app/integrations/google_workspace.py`;
- `backend/app/integrations/yandex_disk.py`;
- две новые линейные Alembic-миграции;
- `backend/app/schema.py`;
- `backend/tests/test_schema_revision.py`;
- `backend/tests/test_mvp1_snapshot_metadata_envelope.py`;
- `backend/tests/test_mvp1_google_metadata_contract.py`;
- новый PostgreSQL Snapshot runtime test;
- итоговый audit Snapshot-области.

Это планируемый, а не финальный список. Перед коммитом необходимо показать фактический список изменённых и новых файлов.

## 8. Ограничение на `workspace.py`

`backend/app/api/workspace.py` не заменялся версией из review-ветки.

При реализации необходимо:

- сохранить существующее разделение snapshot/mutation из main;
- точечно добавить metadata envelope, virtual path, shortcut и pagination;
- не переносить `workspace.py` целиком;
- не откатывать более свежую main-логику очереди, recovery и safe-copy;
- отдельно описать любое вынужденное отклонение от этого решения до коммита.

## 9. Git-состояние на preflight

Перед созданием этого документа:

```text
git diff --check: PASS
git status --porcelain: clean
```

Snapshot implementation и OCR не начинались. Следующая сессия должна начать с этого документа, выполнить selective port и пройти все перечисленные gates до перехода к OCR.
