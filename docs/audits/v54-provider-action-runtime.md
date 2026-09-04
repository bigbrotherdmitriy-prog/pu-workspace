# V5.4 Wave 3: provider action runtime

Дата проверки: 2026-09-04

Ветка: `codex/v54-provider-action-runtime`

Базовый SHA: `b0dbf98d82f034637512c199ba107d44a8133735`

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

## Migration handoff

Alembic migration намеренно не добавлена: текущий head этой базы остаётся
`a54f001c0a04`, а `a54f001c0a05` принадлежит параллельной ветке. После landing
`a54f001c0a05` интегратор должен создать ровно одну последовательную migration:

```text
revision = "a54f001c0a06"
down_revision = "a54f001c0a05"
```

Migration должна материализовать модели из
`backend/app/models/v54_provider_action.py` в следующем порядке:

1. `v54_provider_actions`;
2. `v54_provider_action_approvals`;
3. `v54_provider_dispatch_outbox`;
4. `v54_provider_execution_attempts`;
5. `v54_provider_outcome_observations`.

При переносе нужны все model constraints/indexes, FK `job_id/first_job_id` к
`background_jobs.id`, уникальные scoped command/idempotency bindings и уникальная
observation sequence. Дополнительно PostgreSQL migration review должен добавить
composite FK action/revision между дочерними таблицами и
`v54_provider_actions`, а approval ID — между outbox и approvals: эти FK не
включены в текущие SQLite-portable модели, чтобы не имитировать ещё не
существующий schema head. Downgrade удаляет таблицы строго в обратном порядке и
не должен переписывать provider/audit историю.

Перед merge интегратор обязан выполнить offline PostgreSQL migration render,
upgrade на чистой БД, upgrade с `a54f001c0a05`, downgrade/upgrade round-trip и
проверку единственного Alembic head.

## Проверки

Targeted runtime suite:

```text
python -m pytest tests/test_v54_provider_action_runtime.py -q -p no:cacheprovider
20 passed
```

Совместимость с существующими v54 acceptance/trust/pilot и queue тестами:

```text
python -m pytest tests/test_v54_provider_acceptance_contract.py \
  tests/test_v54_action_trust.py tests/test_v54_pilot_integration.py \
  tests/test_durable_jobs.py tests/test_worker_topology.py \
  tests/test_v54_provider_action_runtime.py -q -p no:cacheprovider
133 passed
```

Первый полный прогон старым project venv: `982 passed, 11 skipped, 3 failed`.
Все три failure находятся в `tests/test_content.py` и вызваны отсутствием пакета
`pypdf` в этом venv (`ModuleNotFoundError`), а не изменённым кодом. Финальный
повтор с доступным bundled `pypdf` фиксируется ниже после выполнения.

Финальный полный прогон с отдельным writable `--basetemp` и bundled `pypdf`:

```text
python -B -c "... pytest.main(['-q', '-p', 'no:cacheprovider', '--basetemp=...'])"
986 passed, 11 skipped, 4 warnings
```

Четыре warning — существующий Alembic `path_separator` deprecation; failures нет.

Process-fault тест принудительно завершает synthetic provider после записи
effect, но до receipt persistence. Второй worker делает один lookup; итоговые
counters: `dispatch=1`, `lookup=1`, `effects=1`.

## Не выполнено намеренно

- нет `AUTO`, ASSIST-to-effect или policy default;
- нет live provider/network/credential loader;
- нет endpoint/UI cutover;
- нет изменения Gmail/Google/Yandex execution code;
- нет migration до появления `a54f001c0a05`;
- нет push, merge или deploy.
