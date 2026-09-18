# MVP-1 Main Integration — Snapshots Completion

Дата: 2026-09-11

Ветка: `codex/mvp1-main-integration`

Базовый HEAD перед Snapshots: `e9e03ad1a4f1b1f66f30107b8c81aba1da3d3c45` (OAuth-область, закрыта).

Этот документ фиксирует фактический результат реализации Snapshots по плану из
[`mvp1-main-integration-snapshots-preflight.md`](mvp1-main-integration-snapshots-preflight.md).
Реализация выполнена, все обязательные гейты пройдены. **Коммит ещё не сделан** —
документ описывает состояние рабочего дерева перед коммитом.

## 1. Статус

**Implementation: COMPLETE, not yet committed.**

Selective port из `codex/mvp1-phase2-review` выполнен точечно: перенесены metadata
envelope, shortcut metadata, полная Google Drive pagination/retry и immutable
VirtualNode envelope. `backend/app/api/workspace.py` не заменён целиком — только
точечные изменения поверх существующего разделения snapshot/mutation. OCR не
начинался.

## 2. Сознательные отклонения от review-ветки (зафиксированы по ходу работы)

Ветка `codex/mvp1-phase2-review` устарела относительно только что закрытой OAuth-
области и содержит фичи вне scope Snapshots. Перенесены не были:

- `backend/app/integrations/storage.py`, `google_workspace.py`, `catalog.py` —
  review-diff в этих файлах откатывает более новую multi-scope OAuth-логику main
  (например, ослабляет проверку `token.access_token`, схлопывает multi-scope
  `GOOGLE_CAPABILITIES` до одного scope). Чистый шум устаревшей ветки, не Snapshots.
- OCR-слой `organizer_engine/drive.py` (`read_native_export_exact`, `NativeExportBytes`,
  `extraction_policy`/`ai_adapter`, `route_extraction` в `populate_content`) —
  следующая фаза (OCR), не начата намеренно.
- Подсистема `supports_exact_mutation_preconditions` / `exact_mutation_blocker` для
  `DriveClient` и `google_storage_mutation.py`/`storage_mutation_live.py` целиком —
  не в scope Snapshots, ни один тест матрицы её не требует. Для Yandex-адаптера сам
  флаг перенесён точечно (по прямому решению пользователя), обвязка вокруг него —
  нет.
- Metadata-паритет с Google для Yandex (`parent_ids`, `provider_revision`, `web_url`,
  `provider_metadata` и т.д.) — отложен на отдельную итерацию по решению пользователя.
- Структурная переделка `_build_snapshot` из review (`_snapshot_write_fence`,
  rollback-then-refence, `job.progress` bookkeeping) — вместо неё выбран минимальный
  точечный фикс: переиспользование существующего в main `_locked_snapshot`
  (`SELECT ... FOR UPDATE`), с локом только на короткое окно re-check+insert+commit
  после Drive I/O, без лока на время самого сетевого чтения.

## 3. Обязательная матрица проверок — фактический результат

| Инвариант | Тест | Результат |
|---|---|---|
| Snapshot остаётся read-only | `test_virtual_workspace_api.py::test_snapshot_analysis_is_explicitly_read_only_in_contract` | PASS |
| Snapshot не запускает safe-copy автоматически | `test_virtual_workspace_api.py::test_connected_folder_snapshot_does_not_automatically_create_safe_copy` | PASS |
| Recovery snapshot не запускает mutation | `test_virtual_workspace_api.py::test_safe_copy_recovery_is_not_started_by_legacy_virtual_analyzer` | PASS |
| Breadcrumb строится от root к текущему узлу | `test_virtual_workspace_api.py::test_nested_drive_breadcrumb_is_root_to_current_folder` | PASS (не пересекается с envelope, отдельный live-browse путь) |
| Mutation запускается только отдельным запросом | `test_storage_binding_validation.py::test_ready_snapshot_waits_for_explicit_safe_copy_request` | PASS |
| Навигация с одинаковыми именами | `test_storage_binding_validation.py::test_navigation_three_levels_back_and_duplicate_names` | PASS |
| Повторная постановка не создаёт duplicate job | `test_storage_binding_validation.py::test_http_repeat_does_not_duplicate_queued_job` | PASS |
| Exact metadata и вычисленный virtual path | `test_mvp1_snapshot_metadata_envelope.py::test_snapshot_envelope_preserves_exact_metadata_and_virtual_path` | PASS (перенесён и адаптирован) |
| Отсутствующие provider-поля — явный `unknown` | `test_mvp1_snapshot_metadata_envelope.py::test_missing_provider_values_are_explicit_unknown_not_invented` | PASS |
| Полный safe metadata envelope в API | `test_mvp1_snapshot_metadata_envelope.py::test_virtual_node_api_exposes_complete_safe_metadata_envelope` | PASS |
| Metadata-миграция последовательна и additive | `test_mvp1_snapshot_metadata_envelope.py::test_snapshot_metadata_migration_is_sequential_and_additive` | PASS (переписан на базу `e16a1c2d3f40` → новый head) |
| Shortcut metadata без перехода к target | `test_mvp1_google_metadata_contract.py::test_shared_drive_and_shortcut_metadata_are_preserved_without_following_target` | PASS |
| Все страницы читаются без пропусков/усечения | `test_mvp1_google_metadata_contract.py::test_pagination_uses_next_page_token_without_duplicate_or_truncation` | PASS |
| Pagination и retry не создают дубликаты | `test_mvp1_google_metadata_contract.py::test_all_pages_are_read_and_read_rate_limit_is_retried_bounded` | PASS |
| Rate-limit 403 vs обычный forbidden | `test_mvp1_google_metadata_contract.py::test_google_403_rate_limit_reason_is_retried_but_plain_forbidden_is_not` | PASS |
| Page size строго ограничен | `test_mvp1_google_metadata_contract.py::test_google_list_page_size_is_strictly_bounded` | PASS (написан заново — в review этот инвариант проверялся только inline внутри shortcut-теста, отдельного теста не было, несмотря на то что preflight ссылался на него как на существующий) |
| Shortcut-миграция линейна | `test_mvp1_google_metadata_contract.py::test_shortcut_metadata_migration_is_sequential_current_head` | PASS (новый head `201286e2acd0`) |
| Завершённые `VirtualNode` не изменяются задним числом | `test_storage_binding_validation.py::test_completed_virtual_node_is_never_rewritten_by_a_later_build` (SQLite) + `test_mvp1_snapshot_postgres.py::test_postgres_completed_virtual_node_is_never_rewritten_under_a_race` (PostgreSQL) | PASS, оба уровня |
| Конкурентная публикация — один победитель | `test_mvp1_snapshot_postgres.py::test_postgres_concurrent_snapshot_publish_has_exactly_one_winner` | PASS (реальные потоки против реального PostgreSQL) |
| Existing `VirtualNode` backfill после миграции | `test_mvp1_snapshot_postgres.py::test_postgres_existing_virtual_node_is_backfilled_by_snapshot_migrations` | PASS |

Все пробелы из preflight закрыты.

### Найденный и исправленный реальный баг

Тест `test_postgres_concurrent_snapshot_publish_has_exactly_one_winner` на первом
прогоне **поймал настоящую гонку** в собственной реализации `_build_snapshot`:
`_locked_snapshot()` брал `SELECT ... FOR UPDATE` (реальный лок на уровне БД
отрабатывал корректно), но возвращал **устаревший Python-объект** из identity map
сессии, если эта же строка уже была прочитана этой сессией ранее без лока — из-за
чего проигравший поток не видел `status == "ready"` победителя и падал в
`UniqueViolation` на `uq_virtual_node_snapshot_external`. Исправлено добавлением
`execution_options(populate_existing=True)` к запросу в `_locked_snapshot`
(`app/api/workspace.py`) — точечный, безопасный для всех остальных вызывающих
мест фикс. Без реального PostgreSQL-теста с настоящими потоками эта гонка не
была бы обнаружена: offline/SQLite однопоточные тесты физически не могут её
проявить.

## 4. Offline / SQLite regression — фактические числа

Полный `python -m pytest -q` из `backend/`:

```
1496 passed, 25 skipped, 1 failed
```

(baseline до Snapshots на HEAD `e9e03ad1`: `1480 passed, 27 skipped, 0 failed, 0 errors`.)

Единственный failed-тест:

`test_mvp1_google_storage_oauth.py::test_legacy_and_mvp1_storage_oauth_routes_coexist_without_collision`
— `AttributeError: '_IncludedRouter' object has no attribute 'path'`.

Подтверждено изолированной проверкой: этот тест падает **идентично на чистом
HEAD `e9e03ad1`**, до единой правки Snapshots (проверено через `git stash` +
прогон именно этого теста в том же Docker-окружении). Это предсуществующий
дефект окружения (похоже на несовместимость между `fastapi==0.141.1`/
`starlette==1.3.1`, закреплёнными в `requirements.txt`, и тем, как тест
итерирует `app.routes`), никак не связанный со Snapshots и с уже закрытой
OAuth-областью. Пользователь подтвердил: оставить как известный
предсуществующий дефект, не блокирующий Snapshots-гейты.

3 новых PostgreSQL-only теста корректно пропускаются offline (входят в 25 skipped).

## 5. Alembic

Новая линейная цепочка (сгенерирована автоматически через `alembic revision`,
без ручного подбора ID):

```text
d04e8a6c31f2
└── e16a1c2d3f40
    └── 21d8f9354c18  (add snapshot metadata envelope)
        └── 201286e2acd0  (add snapshot shortcut metadata, current head)
```

Merge migration не понадобился — цепочка осталась линейной.

Проверено на реальном одноразовом PostgreSQL 16 (не только `sql=True` offline-рендер):

- `alembic upgrade head` с нуля — вся историческая цепочка применяется чисто,
  финальный `alembic_version = 201286e2acd0`, структура `virtual_nodes`
  соответствует ожиданиям (9 новых колонок, 1 новый индекс).
- `alembic downgrade e16a1c2d3f40` — обе новые ревизии корректно откатываются,
  схема возвращается к исходному состоянию.
- Повторный `alembic upgrade head` — чисто накатывается заново.

`CURRENT_SCHEMA_REVISION` в `app/schema.py` обновлён на `201286e2acd0`.

### Побочный эффект: репо-wide trip-wire на текущий head

Добавление нового head потребовало синхронно обновить строковые константы
"текущий head" в файлах вне Snapshots-области (механическое следствие уже
одобренного шага, не архитектурное решение):

- `backend/tests/test_v54_autonomy_authorization.py`, `test_v54_deadline_precision.py`,
  `test_v54_pilot_foundation.py`, `test_v54_provider_action_migration.py`,
  `test_v54_materialization_postgres.py`
- `backend/tests/test_mvp1_storage_oauth_migration.py` — здесь константа `REVISION`
  обслуживала две разные роли (имя файла конкретной старой миграции vs текущий
  head); роли разведены, а не просто переименованы.
- `scripts/ci/v54_pilot_workflow.py`, `scripts/ci/durable_queue/run.py`,
  `scripts/ci/test_v54_wave3_ci_gate.py`, `scripts/ci/test_v54_pilot_workflow.py`,
  `.github/workflows/docker-smoke.yml` (2 места)

## 6. PostgreSQL tested runtime

**Статус: PASS**, реальный одноразовый PostgreSQL 16 в Docker (не CI —
локальный прогон в рамках этой сессии), три новых теста в
`backend/tests/test_mvp1_snapshot_postgres.py`:

1. `test_postgres_concurrent_snapshot_publish_has_exactly_one_winner` — два реальных
   потока, `Barrier`, вызывают настоящий `app.api.workspace._build_snapshot` (не
   реимплементацию) на одном snapshot одновременно; побеждает ровно один, второй
   — чистый no-op без исключений и без false-failed.
2. `test_postgres_completed_virtual_node_is_never_rewritten_under_a_race` — после
   публикации snapshot повторный вызов с адаптером, отдающим уже другие
   метаданные (как будто источник изменился), не меняет ни одной уже
   опубликованной строки `VirtualNode`.
3. `test_postgres_existing_virtual_node_is_backfilled_by_snapshot_migrations` —
   строка `VirtualNode`, вставленная на head `e16a1c2d3f40` (до Snapshot-миграций),
   после `alembic upgrade` до `201286e2acd0` корректно backfilled: `parent_external_ids
   = []`, `provider_metadata_hash = 'unknown'`, `availability/acl_state = 'unknown'`,
   `analysis_state = 'pending'`, исходные данные (`external_id`, `name`) не тронуты,
   без дублирования строк.

Тесты гейтятся через `PUW_MVP1_SNAPSHOT_DATABASE_URL` (по аналогии с
существующим `PUW_V54_MATERIALIZATION_DATABASE_URL`), офлайн пропускаются
(`pytest.skip`), в CI не подключены — по аналогии с остальными
`PUW_*_DATABASE_URL`-гейтами это отдельное решение о постоянном CI wiring,
не входившее в текущий scope.

## 7. Secrets scan

Выполнен по полному `git diff` (18 изменённых файлов) плюс содержимому всех
5 новых файлов. Метод: grep по паттернам ключей/токенов/приватных ключей
(`api_key`, `secret_key`, `access_token`, `AKIA...`, `ghp_...`, `xox...`,
`BEGIN ... PRIVATE KEY` и т.п.) и по произвольным высокоэнтропийным
base64-подобным строкам ≥32 символов с ручной проверкой каждого совпадения.

Результат:

- реальных секретов: 0;
- live tokens/credentials: 0;
- единственное совпадение по паттерну `access_token` — существующий параметр
  конструктора `YandexDiskStorageAdapter.__init__(self, access_token: str, ...)`,
  не новый код, не значение;
- одноразовые учётные данные локального Docker-тестового PostgreSQL
  (использовались только в shell-командах этой сессии для гейтов) в
  закоммиченные файлы не попали — проверено отдельным grep.

## 8. Фактический список изменённых/новых файлов

Изменено (18):

- `.github/workflows/docker-smoke.yml`
- `backend/app/api/workspace.py`
- `backend/app/core/integration_types.py`
- `backend/app/integrations/yandex_disk.py`
- `backend/app/models/workspace.py`
- `backend/app/organizer_engine/drive.py`
- `backend/app/schema.py`
- `backend/tests/test_drive_safety.py`
- `backend/tests/test_mvp1_storage_oauth_migration.py`
- `backend/tests/test_storage_binding_validation.py`
- `backend/tests/test_v54_autonomy_authorization.py`
- `backend/tests/test_v54_deadline_precision.py`
- `backend/tests/test_v54_materialization_postgres.py`
- `backend/tests/test_v54_pilot_foundation.py`
- `backend/tests/test_v54_provider_action_migration.py`
- `scripts/ci/durable_queue/run.py`
- `scripts/ci/test_v54_pilot_workflow.py`
- `scripts/ci/test_v54_wave3_ci_gate.py`
- `scripts/ci/v54_pilot_workflow.py`

Новые (5):

- `backend/migrations/versions/21d8f9354c18_add_snapshot_metadata_envelope.py`
- `backend/migrations/versions/201286e2acd0_add_snapshot_shortcut_metadata.py`
- `backend/tests/test_mvp1_snapshot_metadata_envelope.py`
- `backend/tests/test_mvp1_google_metadata_contract.py`
- `backend/tests/test_mvp1_snapshot_postgres.py`

## 9. Ограничение на `workspace.py` — соблюдено

`backend/app/api/workspace.py` не заменён review-версией. Точечно добавлены:
`_snapshot_path`, `_virtual_node_values`, envelope в `list_virtual_nodes`
(`cursor`/`limit`/`analysis_state`, offset-based cursor), re-check под
`_locked_snapshot` в `_build_snapshot`. Существующая логика очереди, recovery
и safe-copy не откатывалась.

## 10. Git-состояние на момент завершения

```text
git status --porcelain: 23 файла (18 modified + 5 new), рабочее дерево грязное
Коммит: НЕ СДЕЛАН
```

Следующий шаг — коммит после явного подтверждения пользователя. Переход к OCR
требует отдельного подтверждения и в текущей сессии не запланирован.
