# MVP1 storage live adapter readiness

Дата: 2026-09-05  
База: `f969bd1`  
Ветка: `codex/mvp1-storage-live-adapter-ready`

## Решение

Статус: **SYNTHETIC CONTRACT PASS / LIVE PROVIDERS BLOCKED**.

Добавлены provider-specific wrappers для Google Drive и Яндекс Диска. Они
выключены по умолчанию и допускают мутацию только через клиент, который явно
поддерживает атомарную проверку exact provider revision. Текущие production
клиенты намеренно отклоняются. Feature flag, OAuth и provider wiring не
изменялись.

## Аудит существующих клиентов

| Клиент | Текущее поведение | Пробел для live enablement |
|---|---|---|
| `DriveClient` | `files().update(...)` для rename/move | `get_file_meta` не возвращает provider revision; update не получает доказуемый atomic precondition; нет reconciliation receipt |
| `YandexDiskStorageAdapter` | `/resources/move`, локальный `_current_paths` | нет exact revision в read model и условной мутации; локальная карта пути не является provider precondition |

Обычная проверка metadata до вызова не закрывает TOCTOU. Поэтому wrapper требует
`get_exact_state`, `rename_if_revision` и `move_if_revision`; отсутствие любого
capability даёт `ExactPreconditionUnavailable` до provider effect.

## Реализованный контракт

- `enabled=False` по умолчанию;
- точные object revision, parent и ancestry ID;
- containment вложенных папок по ID, не по строковому prefix пути;
- повторная проверка revision между preflight и mutation;
- детерминированный provider operation key без document content;
- conditional rename/move с expected revision и expected parent;
- timeout after effect: exact-state reconciliation, без повторного вызова;
- timeout before effect: отказ только при доказательстве отсутствия эффекта;
- неоднозначный timeout: immutable `unknown` receipt, без ложного success/compensated;
- compensation использует новую revision результата предыдущего эффекта;
- live adapters не подключены к API, worker или storage factory.

## Проверки

Команды из `backend/`:

```powershell
python -m pytest tests/test_mvp1_storage_live_adapters.py -q
python -m pytest tests/test_mvp1_storage_mutation_runtime.py tests/test_mvp1_storage_mutation_repository.py tests/test_mvp1_storage_mutation_api.py tests/test_mvp1_storage_live_adapters.py -q
```

Результаты:

- новый synthetic contract: `18 passed`;
- coordinator regressions вместе с новым contract: `22 passed`;
- полный storage-mutation acceptance/API/repository/runtime/wiring набор:
  `42 passed, 3 skipped`;
- три skip относятся к PostgreSQL-only тестам без `TEST_POSTGRES_DSN`;
- реальных Google/Яндекс вызовов не выполнялось;
- очередь, миграции, schema pins, OAuth, secrets и production не изменялись.

Проверенные сценарии: default deny, отсутствие capability, current-client deny,
exact revision, nested destination, idempotent replay, timeout до/после эффекта,
ambiguous UNKNOWN, partial rename+move, compensation и revision race.

## Точный live-test gate

Live activation запрещена, пока для каждого провайдера отдельно не выполнены все
условия:

1. Provider client возвращает immutable object revision/etag и ancestry IDs.
2. Rename/move выполняются сервером атомарно только при совпадении exact revision
   (и parent для move); read-before-write без atomic condition не принимается.
3. Provider документирует семантику idempotency key или доступен надёжный
   provider receipt для reconciliation.
4. Изолированный tenant запускает тесты: concurrent stale revision, timeout
   before/after, delayed completion, process crash, replay, no-double-effect,
   partial move+rename, compensation и nested folders.
5. Durable worker сохраняет `unknown` до операторского reconciliation и не
   повторяет внешний эффект автоматически.
6. Отдельным reviewed commit подключается factory/feature flag; default остаётся
   false, а rollout ограничивается тестовым cohort.

До выполнения gate текущие `DriveClient` и `YandexDiskStorageAdapter` должны
оставаться отклонёнными wrapper-ом. Live OAuth/latency/rate-limit поведение и
PostgreSQL concurrency этим offline этапом не доказаны.

## Изменённые файлы

- `backend/app/integrations/storage_mutation_live.py`;
- `backend/app/organizer_engine/storage_mutations.py`;
- `backend/tests/test_mvp1_storage_live_adapters.py`;
- `docs/audits/mvp1-storage-live-adapter-ready.md`.
