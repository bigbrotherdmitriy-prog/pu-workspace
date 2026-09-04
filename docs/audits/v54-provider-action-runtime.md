# V5.4 Wave 3: provider action runtime

Дата проверки: 2026-09-04

Ветка: `codex/v54-provider-a06`

Базовый SHA интеграции: `1ce1c6c` (`a54f001c0a05` уже в истории)

## Итог

Реализован отключённый по умолчанию, только синтетический и только `CONFIRM`
provider action runtime. Тестовый acceptance harness не стал production dependency:
его контракт перенесён в отдельные product-модели, Protocol adapter seam, durable
outbox/attempt orchestration и append-only outcome observations.

Ни Gmail, ни Google, ни Yandex production path не подключены и не изменены.
Runtime нельзя создать с live adapter; для PostgreSQL разрешена только тестовая
БД с префиксом `puw_v54_test_`. Startup не устанавливает runtime автоматически,
реальные credentials, network calls и `AUTO` отсутствуют.

## Реализованный контур

- `ActionEnvelope` — frozen content-free envelope. Он принимает только opaque ID,
  точный mailbox/project/context/evidence binding, command/idempotency keys и
  SHA-256 payload; raw payload в API модели не представим.
- `ProviderActionAdapter` и `LiveAuthorityResolver` — узкие Protocol. Единственная
  реализация adapter в этой ветке — `StrictSyntheticProvider`.
- `ProviderAction`, `ProviderActionApproval` — immutable binding. Approval
  дублирует и проверяет action/revision, envelope/payload hash, mailbox, project,
  command/idempotency key, authority epoch, capability version и credential
  generation.
- `ProviderDispatchOutbox` использует существующий `BackgroundJob` kind
  `v54.synthetic_provider_action`; queue payload содержит только
  `organization_id`, `action_id`, `revision`. Вторая очередь не создана.
- `ProviderExecutionAttempt` резервируется и коммитится до provider I/O. Если
  процесс умер после effect, следующий worker видит `DISPATCHING` и выполняет
  scoped lookup, но не новый dispatch.
- `ProviderOutcomeObservation` хранит append-only `APPLIED`, `NOT_APPLIED` или
  `UNKNOWN`. Новое authoritative наблюдение добавляется следующей строкой и не
  переписывает прежнее; late receipt имеет отдельный source/late marker.
- Перед первым dispatch и перед reconciliation заново проверяются live project,
  mailbox, authority epoch, capability version, credential generation, evidence
  pins, expiry и разрешение на соответствующую операцию.
- `TimeoutBeforeEffect` становится `NOT_APPLIED/retry_safe=true`.
  `TimeoutAfterEffect`, произвольный adapter failure и malformed receipt становятся
  `UNKNOWN/retry_safe=false`; handler возвращает business outcome и не отдаёт его
  generic queue retry.
- Rollback, compensation и corrective follow-up допускаются только как новый
  immutable action с другим action/command/idempotency key, новой approval и
  отдельными audit events. Исходный outcome не переписывается.
- Audit использует существующий `AuditLog` и хранит только opaque IDs, hashes,
  safe enums/codes и counters. Payload, DSN, recipient, body, token и PII не
  записываются.

## Инварианты сбоя и конкуренции

1. Outbox commit предшествует enqueue; `recover_outbox` закрывает окно
   commit-before-enqueue.
2. Queue idempotency key обязан совпадать с sealed action; чужой kind/payload/key
   приводит к fail-closed `dispatch_binding_mismatch` до adapter call.
3. Attempt reservation предшествует adapter call. Единственная строка attempt на
   `(action_id, revision)` является durable decision point.
4. Повторный worker для `DISPATCHING`/`UNKNOWN` имеет только reconciliation path.
   Для `APPLIED`/`NOT_APPLIED` он возвращает существующее наблюдение.
5. Отсутствие lookup receipt не доказывает отсутствие effect и сохраняет
   `UNKNOWN`; новый provider dispatch не выполняется.

## Migration result

Добавлена ровно одна последовательная Alembic migration:

```text
revision = "a54f001c0a06"
down_revision = "a54f001c0a05"
```

Migration материализует модели из `backend/app/models/v54_provider_action.py`
в следующем порядке:

1. `v54_provider_actions`;
2. `v54_provider_action_approvals`;
3. `v54_provider_dispatch_outbox`;
4. `v54_provider_execution_attempts`;
5. `v54_provider_outcome_observations`.

Migration и ORM metadata содержат все model checks/uniques/indexes, FK
`job_id/first_job_id` к `background_jobs.id`, scoped composite FK
`(organization_id, action_id, revision)` от четырёх дочерних таблиц к action и
composite approval binding от outbox. Это не позволяет подменить tenant/action
или связать outbox с approval от другой ревизии. Downgrade удаляет пять таблиц
строго в обратном порядке.

`CURRENT_SCHEMA_REVISION`, Docker readiness, durable queue и v5.4 CI pins
переведены на `a54f001c0a06`; исторические ссылки на parent `a05` сохранены.
CI PostgreSQL test выполняет full upgrade на чистой тестовой БД, затем
`a06 -> a05 -> a06`, проверяет состав таблиц/FK/index и единственный head.

## Проверки

Targeted runtime и migration/offline PostgreSQL suite:

```text
python -m pytest tests/test_v54_provider_action_runtime.py \
  tests/test_v54_provider_action_migration.py -q -p no:cacheprovider
21 passed, 1 conditional PostgreSQL skip
```

Совместимость materialization/source/queue:

```text
python -m pytest tests/test_v54_materialization_lifecycle.py \
  tests/test_v54_materialization_postgres.py \
  tests/test_v54_source_evidence_pilot.py \
  tests/test_v54_source_evidence_postgres.py \
  tests/test_durable_jobs.py tests/test_worker_topology.py -q -p no:cacheprovider
71 passed, 3 conditional PostgreSQL skips
```

Полный backend suite с отдельным ASCII `--basetemp`:

```text
python -m pytest tests -q -p no:cacheprovider --basetemp=<ascii-temp>
1024 passed, 14 skipped, 6 warnings
```

Scripts CI suite:

```text
python -m pytest scripts/ci -q -p no:cacheprovider --basetemp=<ascii-temp>
110 passed
```

Шесть warning полного backend — существующий Alembic `path_separator`
deprecation; failures нет. Live PostgreSQL тест не запускался локально: Docker
CLI отсутствует. Он остаётся fail-closed conditional и включён в PostgreSQL CI с
явной disposable БД `puw_v54_test_migrations`; offline PostgreSQL render прошёл.

Process-fault тест принудительно завершает synthetic provider после записи
effect, но до receipt persistence. Второй worker делает один lookup; итоговые
counters: `dispatch=1`, `lookup=1`, `effects=1`.

## Не выполнено намеренно

- нет `AUTO`, ASSIST-to-effect или policy default;
- нет live provider/network/credential loader;
- нет endpoint/UI cutover;
- нет изменения Gmail/Google/Yandex execution code;
- нет push, merge или deploy.
