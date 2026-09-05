# MVP2 durable provider outbox

Дата проверки: 2026-09-05

Ветка: `codex/mvp2-provider-outbox`

База: `f300f258270a4ba9389d4a0bf0a05395cd2b61a6`

Область: Gmail send, Google Tasks upsert, Google Calendar upsert. Реальные API и production не использовались.

## Краткий аудит до изменений

| Поток | Было | Риск |
|---|---|---|
| `POST /response-drafts/{id}/send-gmail` | Gmail `messages.send().execute()` внутри FastAPI | потеря результата при restart, повторная отправка после неопределённого сбоя |
| `POST /tasks/{id}/approve-external` | Google Tasks/Calendar вызывались синхронно через `publish_actions` | подтверждение не было связано с неизменяемой версией команды |
| `/tasks/sync-actions`, `/tasks/sync-actions-project/{id}` | синхронный `publish_actions` | нет durable recovery и единого receipt |
| изменение уже опубликованной задачи | автоматический provider update | внешний эффект без нового точного подтверждения |
| Telegram-команды изменения задачи | автоматический provider update | эффект без нового подтверждения и зависимости от API-процесса |

Существующий `ProviderActionRuntime` уже содержал ledger, approval, outbox, attempt-before-I/O,
observation и UNKNOWN reconciliation, но был жёстко ограничен synthetic provider. Поэтому
новая очередь и новый ledger не создавались: добавлен product bridge к существующему runtime и
существующему `BackgroundJob`.

## Реализованный контракт

- API только фиксирует точный human CONFIRM и ставит ID-only job в durable очередь.
- Job payload содержит только `organization_id`, `action_id`, `revision`.
- Тема/тело письма, адрес, текст задачи, OAuth token и provider payload остаются в существующих
  доменных строках и не попадают в job payload или audit.
- `payload_hash`, `envelope_hash`, `command_key`, `idempotency_key`, evidence pins, authority epoch,
  capability version и credential generation фиксируются неизменяемо.
- Непосредственно перед T2 worker повторно загружает источник, проект, роль человека, scopes,
  mailbox identity/authority/cutover flags и сравнивает точный payload hash.
- Роль global admin сама по себе не даёт право выполнить эффект: нужна роль `manager`/`owner`
  конкретного проекта. Service worker не может создать approval.
- Gmail получает детерминированный RFC Message-ID; Tasks — непрозрачный command marker;
  Calendar — детерминированный event ID и private marker.
- После входа в adapter произвольный сбой считается `UNKNOWN`, а не разрешением на blind retry.
  Повторный claim сначала делает provider lookup. Ручной reconciliation сам является durable job.
- External ID записывается в существующий `ExternalResourceLink`, legacy-поля и receipt/observation.
- Повтор HTTP-запроса использует scoped idempotency key и тот же action/job. Потерянная связь
  `outbox.job_id` восстанавливается по idempotency key без создания второй команды.
- Изменение опубликованной задачи увеличивает `record_version`, возвращает её в `proposed` и
  требует нового CONFIRM; API и Telegram больше не обновляют provider автоматически.
- Calendar без срока отклоняется до постановки задания.

## Lifecycle

```text
domain row + manager CONFIRM
  -> immutable ProviderAction/Approval/DispatchOutbox
  -> BackgroundJob (IDs only)
  -> worker claim + lease fence
  -> live authority/policy/payload recheck
  -> ProviderExecutionAttempt (T1, before I/O)
  -> Google adapter T2
  -> APPLIED / NOT_APPLIED / UNKNOWN observation + receipt + safe audit
  -> UNKNOWN: lookup/reconciliation, never blind resend
```

## Изменённые файлы

- `backend/app/provider_actions/product.py` — product material, adapter, queue/recovery/reconciliation.
- `backend/app/provider_actions/contracts.py` — allowlisted product envelopes and safe precondition.
- `backend/app/provider_actions/runtime.py` — explicit product runtime mode and product job kind.
- `backend/app/models/v54_provider_action.py` — ORM definition of the required broadened CHECK.
- `backend/app/jobs/handlers.py` — dispatch/reconciliation handlers.
- `backend/app/api/provider_actions.py` — authenticated reconciliation enqueue endpoint.
- `backend/app/api/gmail.py` — Gmail send becomes durable enqueue.
- `backend/app/api/tasks.py` — Tasks/Calendar become durable enqueue; edit requires new approval.
- `backend/app/api/telegram.py` — removes direct provider mutation after task edit.
- `backend/app/main.py` — reconciliation router.
- `backend/tests/test_mvp2_provider_outbox.py` — regression and product bridge tests.
- Compatibility expectations updated in `test_mvp5_pilot_acceptance.py` and
  `test_v54_mailbox_identity.py`.

## Tests

- Final Gmail/Tasks/mailbox targeted set: `43 passed`.
- Product outbox regression within that set: `12 passed`.
- Alembic inventory: exactly one current head, `a54f001c0a13`.
- Full backend except the timing-sensitive performance smoke: `1256 passed, 19 skipped`.
- Performance smoke, rerun alone after the parallel machine load subsided: `1 passed in 3.43s`.
  During the loaded full run it took 16.44s against a 10s threshold; this was reproduced alone
  once at 19.98s, then passed unchanged. No product code was modified to mask the timing issue.
- Fixtures are synthetic; no mail was sent and no Google service was contacted.

Covered cases include Gmail content-free enqueue, Tasks+Calendar split commands, exact-once
within a durable action, UNKNOWN after timeout-after-effect, lookup without resend, durable
manual reconciliation, authority revocation, stale payload, deterministic Calendar identity,
new approval after edit, HTTP replay and outbox/job binding recovery.

## Required schema integration (blocker)

No migration was created because schema ownership belongs to the integration stream. The ORM
definition is ready, but the physical PostgreSQL constraint at base head still accepts only
synthetic rows. This branch sees base head `a54f001c0a13`; the integrator reports its current
head as `a54f001c0a14`, so it must create the next sequential migration (`a54f001c0a15`) which
replaces `ck_v54_provider_confirm_synthetic` with the equivalent rule:

```sql
mode = 'CONFIRM' AND (
  (synthetic_only = true AND provider = 'synthetic' AND action_kind LIKE 'synthetic.%') OR
  (synthetic_only = false AND provider = 'google_workspace' AND action_kind IN (
    'gmail.message.send', 'google.tasks.upsert', 'google.calendar.upsert'
  ))
)
```

Until that migration is applied and tested on clean PostgreSQL, product dispatch is
**CONDITIONAL** even though SQLite regression tests pass. Do not deploy the code without the
constraint migration.

## Remaining limitations and acceptance needs

- Project-scoped legacy Google tokens do not expose a durable rotation generation. The bridge
  pins token row ID, exact scope set and project record version; complete rotation semantics
  require a credential-generation model analogous to mailbox identity.
- Task lookup scans a bounded provider list when no stored external ID exists. A production
  adapter should validate provider pagination/rate-limit behaviour in a sandbox account.
- PostgreSQL concurrency, lease crash recovery and the physical CHECK migration were not run in
  this local flow.
- Live Gmail/Tasks/Calendar sandbox acceptance was intentionally not run.
- UI integration for `queued`/`unknown` states is outside this backend-only task.
- Legacy helper `publish_actions` remains for compatibility, but no audited Gmail/Tasks/Telegram
  mutation endpoint calls it.

## Decision

**CONDITIONAL** — implementation and synthetic regression coverage are ready for integration;
the sequential PostgreSQL migration and isolated PostgreSQL/runtime acceptance are mandatory
before merge or deployment.

Production, OAuth secrets, production database, push, merge, restart and deploy were not touched.
