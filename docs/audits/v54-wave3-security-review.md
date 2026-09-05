# V5.4 Wave 3 adversarial integration review

Дата: 2026-09-04

Ветка: `codex/v54-wave3-security-review`

Проверенная база: `26dd79c794194b123c4f7af7a8eb39948da5d07f`

## Вердикт

Активируемого P0/P1 bypass в текущем состоянии не найдено. Все новые effect/read
контуры, которым ещё не передана production authority/composition, fail closed:

- local upload API не пишет и worker не читает без явно установленного
  `LocalUploadRuntime`;
- Gmail attachment import не читает provider body без установленного
  `GmailAttachmentLifecyclePort` и повторной mailbox authorization;
- Evidence API не возвращает fragment content без server-installed
  `FragmentStore` и live DB authority;
- provider action runtime принимает только synthetic adapter/test DB и не
  устанавливается startup-кодом.

Wave 3 можно интегрировать с сохранением этих default-off границ. Включать
production local-upload processing до закрытия двух P1 rollout blockers ниже
нельзя.

## P1 rollout blockers для local upload

### 1. Нет retention recovery для failed/dead-letter materialization

`run_local_upload_job` после processing failure намеренно оставляет ciphertext и
`DERIVED` materialization до `retention_until`. Однако scheduler вызывает recovery
только для Gmail attachment staging; у local lifecycle нет scheduled scan,
`expire`/`purge` reconciliation или terminal outcome hook. После исчерпания queue
attempts такой объект может остаться зашифрованным, но бессрочным.

Это не создаёт live exposure на проверенной базе, потому что production runtime
не установлен. До enable нужен отдельный service-authorized retention owner,
который независимо от бывшего user grant выполняет bounded scan и выдерживает
порядок `durable EXPIRED -> idempotent delete -> durable PURGED`. Частичный user-
scoped sweeper не добавлялся: после revoke он сам fail closed и не обеспечивает
retention guarantee.

### 2. Lease fence не охватывает legacy business commits

Текущий worker проверяет `(job_id, worker_id, attempt, locked_at, lease)` до
plaintext read и повторно перед finalization. Между ними processor вызывает
несколько legacy helpers с собственными commits. Tasks, drafts и governance rows
защищены unique constraints, но `documents` не имеет unique constraint на
`(project_id, external_id)`. Если heartbeat потерян после read authorization,
новый attempt может начать обработку параллельно со старым и оба могут вставить
Document для одного stable local source до повторной claim-проверки.

На default-off базе эффект недостижим. Корректное включение требует либо
DB-enforced document identity (с migration), либо operation/claim fence,
удерживаемого до первого durable idempotency point. Migration не добавлялась,
поскольку `a07` развивается параллельно и была явно исключена из review scope.

## Проверенные security invariants

- Mailbox rollout transition использует exact current credential generation,
  binding epoch, mailbox authority version и flags CAS; invalid lattice и
  cross-organization rows не проходят.
- Gmail attachment queue payload содержит только opaque `staging_id`; provider
  locator и attachment metadata остаются в server-side binding. Authorization
  повторяется непосредственно перед provider read, каждым staged read и commit
  derived output.
- Local upload queue payload содержит только opaque `staging_id`. Filename
  нормализуется до basename, content/checksum/KEK/path/client idempotency value не
  попадают в job result или audit/log boundary.
- A05 materialization связывает tenant/project/owner, exact SourceVersion,
  Evidence, representation handle, storage descriptor и current worker claim;
  cleanup decision коммитится до ciphertext delete.
- Evidence endpoint возвращает одинаковый non-cacheable unavailable projection
  для not-found, cross-tenant, revoked authority, stale lineage и missing store;
  store read выполняется только после ACL/policy/retention/residency validation.
- Autonomy policy не позволяет AUTO для external/high-risk/unknown effects;
  AUTO internal task требует exact low-risk effect set, live owner epoch и
  повторный exact candidate/payload/envelope recheck.
- Provider outbox резервирует durable attempt до adapter call. Recovery из
  `DISPATCHING`/`UNKNOWN` выполняет lookup, а не повторный dispatch; payload,
  recipient, body, tokens и DSN не представимы в queue DTO.
- Production Gmail send и прочие legacy paths не были неявно переключены на
  synthetic a06/autonomy policy: новая runtime остаётся изолированной и
  default-off, поэтому alternate-provider cutover отсутствует.

## Выполненные проверки

- объединённый Wave 3 backend target: `121 passed, 4 skipped`;
- полный backend: `1087 passed, 15 skipped`;
- CI scripts: `110 passed`;
- Evidence frontend target: `47 passed`;
- Alembic a06 head/offline PostgreSQL render вошёл в backend target;
- `git diff --check` — clean перед commit.

PostgreSQL-only проверки пропущены без disposable test DSN. Production, push,
merge и deploy не выполнялись.
