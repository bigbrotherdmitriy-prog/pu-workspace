# Parallel validation integration

Дата: 2026-09-03. Итог: **CONDITIONAL**. Backend regression и локальные
smoke/harness contract tests — PASS; PostgreSQL runtime не выполнялся.

## Изоляция и история

Ветка `codex/parallel-validation-integration`, отдельная worktree
`pu-workspace-parallel-validation-integration`. Точная база:
`814ff77b79bd3a6d1382c345783946a7b9b7898e`.
Применимых AGENTS.md при проверке не найдено. Все три входящих коммита
доступны локально, merge-base каждого с базой совпадает с этой базой.

Порядок переноса (исходный SHA → SHA cherry-pick):

1. `223eefa3b7de9be2ea3266ed2d597056674c64c8` → `07faf19670c4d8c6209668b575d08de3656985de`.
2. `387c75019df040d8fc8166457b3d1970a975b835` → `1b74f07213c93d653efa5e76920d6e0d4d78075a`.
3. `386af8ac00125a867edb7e0f32db0aee39c17820` → `d1672abec3897e0e40352371359471e91563e2a0`.
4. Отдельный integration commit содержит этот отчёт, workspace.py и два тестовых
   файла. Его полный SHA выдаётся в итоговом ответе (`git rev-parse HEAD`).

Основная worktree: `codex/commercial-p2-yandex360`,
HEAD `83774aac726acd4e27b349e9194f30783158bde8`. Исходные незакоммиченные файлы:
backend/app/api/auth.py, local_upload.py, workspace.py; backend/app/schema.py;
backend/app/static/app.js; docker-compose.yml; frontend/index.html.
Эти изменения не переносились и не изменялись. Push/merge/deploy, production
доступ и реальные внешние интеграции не выполнялись.

## Краткий аудит и пересечения до исправлений

Прочитаны queue-recovery-validation.md, storage-binding-validation.md и
gmail-project-validation.md. Текстовых пересечений файлов между тремя потоками
нет; все cherry-pick прошли без конфликтов. Семантические пересечения:

| Поток | Контракт | Интеграционный риск |
|---|---|---|
| Queue | lease/fencing, retry, durable jobs | manual retry создаёт второй job во время автоматического retry |
| Storage | project/provider/connection/folder pin | замена analysis_result теряет ссылку на safe-copy session |
| Workspace recovery | восстановление pending/failed snapshot | canonical key отличается от manual key |
| Gmail | confidence/evidence, ручное назначение, ACL | нельзя считать fallback project подтверждённым назначением |

Существующая очередь переиспользована. Новых mailbox/thread/contact-моделей,
адаптеров, OCR-алгоритмов, staging или миграций нет.

## Воспроизведения и минимальные исправления

Новый test_parallel_validation_integration.py переиспользует fixture хранилищ
Google Drive/Яндекс Диск; внешние adapters/scanner заменены синтетическими.
До соответствующих исправлений наблюдались:

- 14 FAILED: ручной retry при queued/running/retrying, анализ одновременно с
  automatic retry, неатомарное изменение snapshot при enqueue failure,
  попадание provider error с синтетическим содержимым документа в snapshot.
- 4 FAILED: повтор завершённого анализа и несоответствие source.provider.
- 2 FAILED: после замены analysis_result создавалась новая orphan session,
  хотя durable safe-copy job уже ссылался на старую.
- 2 FAILED: recovery создавал canonical analysis job рядом с manual job.

Исправления в workspace.py:

- snapshot row lock и поиск активной работы snapshot/analysis/safe_copy;
  ручной retry возвращает 409 при активной работе; recovery переиспользует job;
- изменение состояния и enqueue ручной операции в одной транзакции;
- safe-copy replay восстанавливает session из существующего durable job и
  проверяет project/source/session, не полагаясь только на mutable JSON;
- проверка source.provider дополнительно к сохранённому binding;
- завершённый virtual analysis не выполняется повторно;
- snapshot получает безопасную фиксированную ошибку safe-copy, не provider text.

Два существующих storage-тестовых сценария теперь явно завершают предыдущий job
перед ручным повтором: ready/failed snapshot сам по себе не означает terminal job.
Остальные проверки сохранены, skip не добавлялись.

## Проверки и фактические результаты

Использован локальный `.venv-pu-workspace-tests`, `PYTHONPATH=backend`,
`DATABASE_URL=sqlite+pysqlite:///:memory:`; provider fixtures используют временные
SQLite БД. Это НЕ PostgreSQL fault/concurrency validation.

| Команда | Результат |
|---|---|
| `python -m pytest backend/tests -q --tb=short -p no:cacheprovider` | 476 passed, 1 skipped, 2 warnings; 95.96 s |
| `python -m pytest scripts/ci/tests scripts/ci/durable_queue/test_contract.py -q -p no:cacheprovider` | 73 passed; 2.40 s |
| `python -m pytest backend/tests/integration -q -rs -p no:cacheprovider` | 1 skipped: PostgreSQL integration tests require PU_TEST_POSTGRES=1 |
| из backend: `python -m alembic -c alembic.ini heads` | одна head: f360a1b2c3d4 |
| CURRENT_SCHEMA_REVISION | f360a1b2c3d4, совпадает |
| `git diff --check` | PASS |

Warnings: существующий Alembic config не задаёт path_separator.
PostgreSQL-тест не скрыт и не заменён SQLite; отсутствие runtime явно блокирует
безусловный PASS. Docker build/up, два API/two workers, fault/backup/restore,
реальные Google/Yandex/Gmail/OCR/AI вызовы здесь не выполнялись.

## Покрытие запрошенных инвариантов и ограничения

1. Все существующие обновления analysis_result в workspace проверены:
   storage_binding сохраняется в анализе, safe-copy и ручном reset. Managed
   template создаёт новый snapshot без pin; legacy snapshots без pin остаются
   совместимыми, их защита слабее.
2. Incoming storage regression покрывает snapshot → enqueue failure → recovery;
   дополнительный тест покрывает восстановление session после замены JSON.
3. Retry-build/analyze/recovery больше не создают новую активную работу при
   обнаруженном job того же snapshot. Эффективность row lock на PostgreSQL
   требует runtime с двумя соединениями/процессами.
4. Binding проверяется до storage side effects; Google alias нормализуется.
   Одновременная переавторизация connection после проверки не полностью fenced.
5. Incoming queue tests проверяют отказ просроченному владельцу при heartbeat,
   progress, completion/failure. Реальная lease expiry PostgreSQL не проверена.
6. Доставка остаётся **at-least-once**. Completed analysis replay и reuse session
   защищены; частично выполненный анализ с промежуточными commits не имеет
   универсальной транзакции или fencing всех бизнес-записей. Уже начатые copy,
   rename/move, Gmail send не получают exactly-once от queue lease. Конкуренция
   legacy organizer.scan с safe-copy и отдельного standardize с virtual analysis
   требует дополнительного runtime/бизнес-аудита; полной взаимной блокировки
   всех organizer entrypoints этот patch не заявляет.
7. Incoming Gmail tests сохраняют confidence/evidence, ручные назначения,
   project access. Identity mailbox и переназначенные письма с OAuth другого
   проекта остаются ограничениями исходного потока; новые модели не вводились.
8. Рабочие job payload используют IDs/locators, не тела писем/документов.
   Generic enqueue не валидирует произвольный payload. Безопасная snapshot error
   не доказывает безопасность всех organizer/scheduler logs/error fields.
   Production строки/логи не читались; универсальная очистка не заявляется.
9. OCR evidence/manual review и cooperative cancellation не переписаны;
   существующий backend regression прошёл. OCR runtime с реальными файлами нет.
10. Google и Yandex synthetic storage regressions проходят; внешняя совместимость
    реальными credentials не проверялась намеренно.

## Контракт frontend-интегратора

- Picker должен сохранять project_id, provider, connection_id/connection_row_id
  из ответа discovery и выбранный encoded locator. Не подставлять позже текущий
  active project вместо проекта открытого picker.
- Передавать поддерживаемые provider/connection guards при выборе папки.
  При 409 changed connection открыть picker заново, не повторять молча старый locator.
- Retry-build/analyze могут вернуть 409 при queued/running/retrying работе:
  показать существующую очередь/прогресс, не зацикливать автоматические повторы.
- Ответ already_queued анализа не означает завершение. Использовать snapshot/job
  ID исходного проекта и игнорировать устаревшие ответы после переключения проекта.
- Не удалять storage_binding при client-side merge отображаемых analysis_result.
- Confidence/evidence Gmail показывать отдельно от ручной связи. Fallback intake
  project не равен подтверждённому назначению; ручное подтверждение сохраняется.
- Frontend и backend/app/static/app.js в этом потоке не менялись.

## Передача runtime-потоку

Существующие CI/harness не дорабатывались. Unit/contract PASS не доказывает
исправность их контейнерной топологии. На чистом PostgreSQL нужно проверить:
две конкурентные manual/recovery операции с row locks, JSON integer predicates,
snapshot → safe-copy recovery после сбоя, истечение lease и устаревший handler,
restart API/Compose, backup/restore и отсутствие секретов в runtime artifacts.
Особенно добавить сценарии manual key против canonical recovery key и partial
business effects, не только синтетический AuditLog. Требование миграции отсутствует.

Integration commit изменяет только backend/app/api/workspace.py,
backend/tests/test_storage_binding_validation.py,
backend/tests/test_parallel_validation_integration.py и этот отчёт.
Полный список входящих файлов: `git diff --name-only 814ff77b79bd3a6d1382c345783946a7b9b7898e HEAD`.
