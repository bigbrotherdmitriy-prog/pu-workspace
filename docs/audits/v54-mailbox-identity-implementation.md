# v5.4 mailbox identity cutover — implementation result

Дата: 2026-09-04. Ветка: `codex/v54-mailbox-identity-cutover`.
База: `b166dcda862a351861070eed598babf83c65a3f3`.

## Аудит до изменений

- `Message` имел nullable mailbox bridge, но legacy writer использовал global
  `(source_type, source_external_id)` и project-scoped OAuth.
- Google OAuth не сохранял криптографически подтверждённый account subject.
- attachment/reply выбирали токен по текущему `Message.project_id`/`Draft.project_id`.
- append-only origin decision/binding, CAS current, generation bridge, mailbox
  authority и scoped flags отсутствовали.
- read-only inventory и cutover design уже существовали; они не изменяли данные.

## Реализовано

1. Проверка Google OIDC ID token: подпись через Google verifier, audience, issuer,
   expiry и обязательный `sub`. До успешной проверки credential row не создаётся.
2. `sub` является единственным `ConnectionIdentity.account_key`; email, project,
   token row, sender/domain/contact не участвуют в identity resolution.
3. Immutable credential-generation bridge, exact binding epoch и generation.
   Повтор того же subject создаёт новую pinned generation через CAS; другой subject
   для уже связанного token требует explicit revoke.
4. Append-only `MailboxOriginDecision` и `MailboxOriginBinding`, одна CAS current
   projection, положительные версии/epoch и tenant-scoped FKs.
5. Human-only mailbox authority. Service principal и global admin не имеют bypass.
6. Flags scoped по organization + MailConnection + credential generation и
   создаются выключенными: shadow write/read compare, pilot write, primary read,
   actions — `false`.
7. Gmail pilot ingress дедуплицирует по `(mail_connection_id,
   provider_message_id)` независимо от incoming/outgoing direction. Legacy rows
   сохраняют partial unique только при `mail_connection_id IS NULL`.
8. Attachment и reply для mailbox-origin Message разрешаются только через
   persisted origin generation. При `actions=false`, revoked/stale generation или
   unresolved origin provider не вызывается; project-token fallback запрещён.
9. Перенос business project/context не меняет persisted origin.
10. Audit/error summaries не содержат provider message/attachment ID, email,
    subject/body или token. Reconciliation работает в caller transaction, делает
    только flush и не commit/rollback.

## Миграции

- `a54f001c0a03_v54_mailbox_identity_expand.py`: additive schema, без seed/backfill.
- `a54f001c0a04_v54_mailbox_dedup_cutover.py`: сначала partial legacy unique,
  затем снятие global unique; guarded downgrade отказывается восстанавливать
  global unique при collision.

Единственная head: `a54f001c0a04`.

## Проверки

- mailbox/Gmail/schema/inventory/foundation target: PASS, 167 passed, 1 PostgreSQL skip;
- mailbox identity module: PASS, 18 tests;
- offline PostgreSQL Alembic SQL generation through `a54f001c0a04`: PASS;
- Alembic heads: PASS, одна head;
- `git diff --check`: PASS;
- полный backend без единственного запрещённого к исправлению CI-pin assertion:
  771 passed, 9 skipped, 1 deselected. Полный неизменённый запуск подтверждает
  один оставшийся
  out-of-scope CI pin failure, описанный ниже;
- реальный PostgreSQL migration/concurrency/guarded downgrade: NOT RUN — явно
  выделенная test DB не предоставлена.

Все provider/OAuth операции в тестах mocked; используются только synthetic IDs и
`example.test`. Production, `.env`, реальные mailbox, tokens и user data не читались.

## Flags и rollout

Все flags default false. Наличие identity/binding не включает sync/action. Порядок
включения: shadow compare → allowlisted pilot write → primary read → отдельный
actions flag. Reconciliation не подтверждает Project/Contract.

## Оставшиеся blockers

1. Существующие CI runtime scripts/workflow на базе всё ещё жёстко ожидают
   `a54f001c0a02`. По прямому запрету менять CI они не изменялись. Интегратору
   необходимо отдельным CI-коммитом заменить pin на `a54f001c0a04` и повторить
   Docker/PostgreSQL runtime. Это причина одного ожидаемого full-suite failure в
   `test_runtime_schema_expectations_follow_foundation`.
2. PostgreSQL acceptance обязательна для доказательства concurrent ingress/CAS,
   partial indexes, composite FKs и обоих guarded downgrade.
3. Scoped flags не имеют публичного toggle endpoint намеренно; rollout требует
   отдельного operator/approval потока.
4. Shadow compare сохраняет legacy behaviour и не чинит mismatch автоматически;
   production cohort нельзя включать до runtime acceptance.

Статус: **CONDITIONAL** до отдельного CI head-pin и реального PostgreSQL прогона.
