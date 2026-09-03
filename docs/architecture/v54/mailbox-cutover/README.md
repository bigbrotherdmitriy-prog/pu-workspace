# Mailbox-scoped identity cutover — безопасный DRAFT

Статус: аудит, read-only tooling и синтетические fixtures. Никаких ORM/Alembic/
handlers/data changes. Production enable запрещён.

## Подтверждённое состояние базы 4db9d514…

Message одновременно содержит legacy unique (source_type, source_external_id),
nullable bridge mail_connection_id/provider_message_id/source_reference_id,
mailbox unique (mail_connection_id, provider_message_id), обязательный project_id
и legacy content/attachments/context fields. CHECK допускает только полностью
пустой либо полностью заполненный origin bridge.

Production Gmail sync/ingest продолжает искать и создавать по raw Gmail ID и
project-scoped GoogleOAuthToken; bridge не заполняется этим handler. Один Gmail ID
в разных ящиках блокируется/ошибочно дедуплицируется до cutover.

GoogleOAuthToken unique по project и не хранит стабильный account subject.
IntegrationCredential имеет account_external_id/email, но комментарий модели
явно сохраняет Google на старом token store. Нельзя считать token row, project,
email или ciphertext identity ящика.

Ручной confirm-context меняет Message.project_id и связанные Task/ResponseDraft/
Risk. Legacy AuditLog содержит конечный project в свободном details, без actor и
надёжного before/after. Bulk audit относится к Project и не даёт безопасной
per-message истории. Перенос доказуем только по ContextRelation; повторные legacy
audit события — сигнал, не доказательство.

Task, ResponseDraft и TaskCompletionSuggestion имеют прямой message_id.
ProjectContact имеет organization-wide normalized_email и один project_id, но
Message не имеет contact FK. Нельзя инвентаризировать связь через source_sender.
Source thread сохраняется, RFC Message-ID/In-Reply-To/References отсутствуют.

context_confirmed=true мог выставляться алгоритмом по confidence. Даже v5.4
confirmed_by — User FK, но User не классифицирован как trusted human/service actor.
Инструмент отдельно считает recorded approver и отсутствие trusted-human proof.

## Read-only inventory

Скрипт: scripts/audits/v54_mailbox_inventory.py. Он не импортирует product ORM,
не загружает .env, не обращается к Gmail и не имеет исправляющих команд.

### Offline fixtures

~~~powershell
python scripts/audits/v54_mailbox_inventory.py --fixture mailbox_cutover.json
python -m unittest scripts.audits.tests.test_v54_mailbox_inventory -v
~~~

Fixture mode не является DB inventory. Фиксированный каталог и строгая схема
не принимают body/subject/address/token. Результат — aggregates и HMAC opaque IDs.

### Безопасная тестовая PostgreSQL

URL и HMAC key передаются только через явно названные переменные процесса.
Production .env не читать.

~~~powershell
$env:PUW_MAILBOX_TEST_DATABASE_URL = 'postgresql+psycopg://inventory_user:<test-password>@localhost:5432/puw_mailbox_test'
$env:PUW_MAILBOX_OPAQUE_KEY = '<new-random-test-value-at-least-32-characters>'
python scripts/audits/v54_mailbox_inventory.py --database-url-env PUW_MAILBOX_TEST_DATABASE_URL
python scripts/audits/v54_mailbox_inventory.py --database-url-env PUW_MAILBOX_TEST_DATABASE_URL --execute-read-only --opaque-key-env PUW_MAILBOX_OPAQUE_KEY
Remove-Item Env:PUW_MAILBOX_TEST_DATABASE_URL
Remove-Item Env:PUW_MAILBOX_OPAQUE_KEY
~~~

Первый вызов — default dry-run и не подключается. Второй проверяет PostgreSQL и
exact schema head a54f001c0a02, открывает REPEATABLE READ / READ ONLY transaction,
задаёт statement/lock timeout и max messages, выбирает allowlisted metadata и
всегда rollback. URL, host, database/user, raw ID и DB exception не выводятся.
Query/fragment options и небезопасное имя database отвергаются, чтобы URL не мог
подменить search_path или параметры сессии.

Non-loopback host, имя без test/ci/stage/dev/local/sandbox marker либо с prod/live
считаются production-like. Обычный и dry-run вызов отказывают. --describe-target
без соединения показывает opaque fingerprint и причины.

Реальное чтение production-like target в этой работе не выполнять. Технический
break-glass требует одновременно --allow-production-like, exact confirmation
READ_ONLY_MAILBOX_INVENTORY:<fingerprint>, --execute-read-only и HMAC key.
Это не разрешение: дополнительно нужны владелец данных, ticket, read-only DB role
и отдельная авторизация. Не помещать production URL в shell history.

### Метрики и ограничения

Считаются unknown/partial origins, legacy/mailbox collisions, raw provider ID
между directions, cross/unknown mailbox threads, relation-proven transfers,
legacy reconfirmation signals, approver gaps, Task/Draft/completion links и
project mismatch. Один HMAC key даёт детерминированный отчёт на неизменном
snapshot; новый key разрывает корреляцию.

- collision=0 при старом unique не доказывает поддержку двух mailbox;
- AuditLog.details и contact email/normalized_email намеренно не читаются;
- текущая связь Contact↔Message помечается неизмеримой; общий count контактов
  не выдаётся за связанность;
- текущий origin виден, но его неизменность после transfer без history недоказуема;
- RFC Message-ID collision не измеряется: поля нет;
- OAuth/project/email/active project никогда не используются для выбора mailbox;
- opaque samples не являются API для массового backfill.

## Документы

- [План reconcile/cutover](reconciliation-plan.md)
- [Синтетические сценарии](synthetic-fixtures.md)
- [Interface request](interface-request.md)
- [Итоговый аудит](../../../audits/v54-mailbox-cutover.md)
