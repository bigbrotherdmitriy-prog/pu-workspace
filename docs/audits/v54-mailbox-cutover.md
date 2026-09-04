# v5.4 mailbox cutover — audit и tooling result

Дата: 2026-09-03. Вердикт: **tooling/fixtures PASS; production cutover BLOCKED**.

## Исходное состояние

Worktree: pu-workspace-v54-mailbox-cutover.
Ветка: codex/v54-mailbox-cutover.
Точная база: 4db9d51496e25d7916ecc75a5dfdf61a930c8637.
Перед работой status был чистым. Одноимённых branch/worktree не существовало.
Применимых AGENTS.md в проверенном repository/workspace hierarchy не найдено.

Основная dirty worktree не менялась. Её исходное и финальное состояние:
branch codex/commercial-p2-yandex360, HEAD
83774aac726acd4e27b349e9194f30783158bde8, изменения:

- backend/app/api/auth.py
- backend/app/api/local_upload.py
- backend/app/api/workspace.py
- backend/app/schema.py
- backend/app/static/app.js
- docker-compose.yml
- frontend/index.html

Ни один файл не копировался и не коммитился.

Прочитаны v54-pilot-integration.md, gmail-project-validation.md,
Context Communication и Source/Evidence contracts/migration proposals. Изучены
Message/v5.4/OAuth/credential/contact/Task/Draft/completion/audit models,
Gmail sync/import/send, AI Secretary ingest/confirm/bulk/completion, contacts и
automation entrypoint. Реальные mailbox, DB, .env и credentials не читались.

## Подтверждённые legacy риски

| Область | Факт на базе | Следствие |
|---|---|---|
| Message identity | global uq(source_type, source_external_id) и mailbox bridge существуют одновременно | legacy Gmail writer не mailbox-scoped; collision=0 не доказательство |
| Gmail lookup | raw Gmail ID среди email/email_outgoing | разные mailbox могут конфликтовать, direction ведёт себя непоследовательно |
| OAuth | GoogleOAuthToken unique по Project, stable account subject отсутствует | current project/token не origin evidence |
| Move | confirm/bulk меняют Message/Task/Draft/Risk project | import/send затем берут current project service, origin может потеряться |
| Legacy audit | details с final project, без actor/before; bulk audit не per-message | перенос и human decision исторически не доказуемы |
| v5.4 history | ContextRelation содержит revisions/confirmed_by | project move можно доказать только где relation history существует |
| Human proof | confirmed_by указывает User, но нет trusted-human/service classification | recorded approver != доказанный human approver |
| Contacts | org-wide email row и один Project; Message contact FK нет | multi-project contact нельзя искать без PII/новой подтверждённой relation |
| Thread/RFC | source_thread_id есть; RFC Message-ID/References отсутствуют | raw thread ID не identity и не безопасный cross-mailbox matcher |
| Links | Task/Draft/Suggestion имеют message_id | project mismatch можно считать без содержимого |
| Source preservation | Message хранит только current mail_connection_id | после move origin history не доказуема без append-only binding |

## Реализовано в разрешённой области

1. Read-only inventory script без product ORM и без .env loading.
2. Явный DB URL environment name; PostgreSQL-only; exact expected schema head.
3. Default dry-run без соединения.
4. REPEATABLE READ, READ ONLY, timeouts, max rows и unconditional rollback.
5. Production-like classifier и отдельный exact confirmation break-glass.
6. Allowlist запросов: IDs/scope/state/relations; исключены content, names,
   sender/address, URL, attachment metadata, audit details, OAuth/credentials/tokens.
7. Агрегаты + HMAC opaque IDs, deterministic canonical JSON.
8. Отдельно: unknown origin, collisions, thread groups, proven move, weak legacy
   reconfirm signal, recorded approver gap, trusted-human proof gap, Task/Draft/
   completion links/mismatch. Текущий Contact не читается/не угадывается:
   без approved direct relationship связанность помечена неизмеримой.
9. Strict offline fixture, fixed path, без PII/content.
10. Expand→inventory→explicit reconciliation→shadow→cutover→rollback plan.
11. Blocking interface request MBX-CUTOVER-01.

Инструмент не исправляет, не удаляет и не backfill данные. В нём нет mutation SQL,
Gmail/provider calls или возможности читать произвольный fixture path.

## Синтетическое покрытие

mailbox_cutover.json и отдельный cutover_cases.json моделируют:

- одинаковый Gmail ID в mail-a/mail-b;
- legacy unknown mailbox и отдельный ambiguous unresolved;
- один synthetic contact identity в двух project links;
- thread collision между mailbox и unknown origin;
- Message с ContextRelation history project-a→project-b и сохранённым current origin;
- direction collision в одном mailbox;
- linked Task, Draft mismatch и completion;
- recorded approver и отсутствие trusted-human proof.

Fixture может представлять post-expand состояние, невалидное под старым global
unique; это oracle для cutover, не ORM fixture production schema. Никаких
реальных адресов, subject/body, client names, tokens или production IDs нет;
единственный RFC адрес использует example.test.

## Проверки

Среда: Python 3.12.13, Windows.

| Проверка | Результат |
|---|---|
| unittest scripts.audits.tests.test_v54_mailbox_inventory -v | 18 passed |
| Offline fixture CLI | exit 0, deterministic report |
| Default invocation без URL | exit 2, explicit_database_url_env_required |
| Safe test-like URL dry-run | exit 0, will_connect=false |
| Production-like URL без gate | exit 2, URL/host/user/password отсутствуют в error |
| Describe + exact gate без execute | только dry-run, соединения нет |
| Unsafe fixture paths | отказ |
| Extra content field fixture | отказ |
| PII/raw synthetic IDs в report | отсутствуют |
| SQL allowlist | mutation/content/credential fields отсутствуют |
| Partial origin | unresolved count растёт, mailbox не угадывается |
| git diff --check | PASS |
| Реальная PostgreSQL/test DB | NOT RUN: явный безопасный URL не предоставлен |
| Production DB/Gmail | не вызывались |

Unit tests проверяют 14 функций/границ; counts выше не суммируются с CLI.
SQL transaction semantics подготовлены, но без реального PostgreSQL не доказаны.
Schema head a54f001c0a04 проверяется перед inventory после mailbox cutover.

## Cutover sequence

Полный план: [reconciliation-plan.md](../architecture/v54/mailbox-cutover/reconciliation-plan.md).

1. Expand и scope flags OFF; append-only origin/reconciliation history.
2. Read-only inventory на restored test DB.
3. Только explicit reconciliation с identity subject/evidence/CAS/actor authority.
4. Unknown/ambiguous остаются unresolved; никаких sender/project/OAuth guesses.
5. Shadow на synthetic exact mailbox cohort; old/new writer mutual exclusion.
6. Controlled primary reads → writes → отдельно approved actions.
7. Global unique изменяется последней отдельной migration.
8. Rollback выключает actions/writes, сохраняет history/receipts и не передаёт
   new/unresolved records в legacy send/download.

## Блокеры и MBX-CUTOVER-01

Точный request: [interface-request.md](../architecture/v54/mailbox-cutover/interface-request.md).

До следующего интегратора требуются:

- durable Google account subject → shared ConnectionIdentity/MailConnection;
- append-only origin binding и CAS reconciliation decision;
- mailbox ACL и human/service authority;
- coexistence/partial unique + снятие global constraint;
- unresolved representation при обязательном legacy project_id;
- адаптация всех Gmail lookup/import/reply/send/dedup paths;
- scoped thread/RFC contract;
- один contact identity/multi-project relation owner;
- org+connection feature flags и safe rollback;
- PostgreSQL migration/concurrency/revoke/crash/restore acceptance.

Предлагаемые privileged DTO не содержат body/address/token; provider_message_id
доступен только внутри команды и не попадает в report/audit. Origin reconcile
не подтверждает бизнес-контекст.

## Изменённые файлы

- scripts/audits/v54_mailbox_inventory.py
- scripts/audits/tests/fixtures/mailbox_cutover.json
- scripts/audits/tests/fixtures/cutover_cases.json
- scripts/audits/tests/test_v54_mailbox_inventory.py
- docs/architecture/v54/mailbox-cutover/README.md
- docs/architecture/v54/mailbox-cutover/reconciliation-plan.md
- docs/architecture/v54/mailbox-cutover/synthetic-fixtures.md
- docs/architecture/v54/mailbox-cutover/interface-request.md
- docs/audits/v54-mailbox-cutover.md

ORM, Alembic, Gmail handlers, models, CI, frontend и business data не изменены.
Push, merge, PR, deploy и production access не выполнялись. Итоговый commit SHA
сообщается после создания одного commit, чтобы не создавать self-reference.
