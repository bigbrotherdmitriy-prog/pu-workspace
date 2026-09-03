# Expand → inventory → reconciliation → cutover → rollback

## Неподвижные инварианты

Origin ящика и бизнес-контекст независимы. Изменение Project/Contract не меняет
MailConnection/SourceReference. Один provider_message_id допустим в разных
verified identities и дедуплицируется внутри одной identity независимо от
direction/source_type.

Запрещён backfill по project_id, текущему OAuth, sender/domain, body, subject,
filename, thread/RFC ID, имени проекта/компании, contact, active project,
«единственному аккаунту» или hash содержимого. Не создавать фиктивный общий
проект и не солить raw Gmail ID.

Unresolved безопаснее догадки: запрещены attachment download/reply/send/reanalysis
через current project token. Legacy projection остаётся доступна только по прежним
правам; новых mailbox rights она не даёт.

## 0. Preconditions

- Backup/restore тестовой копии проверены; production data не используется для tooling.
- Утверждены ConnectionIdentity authority, mailbox ACL, provider account-subject,
  credential generation и operator break-glass.
- Evidence/Context/Action owners согласованы; не создаются второй registry,
  approvals, ledger или queue.
- Перечислены sync, ingest, inbox, confirm/bulk, attachment import, draft send,
  contacts, completion, jobs/automation.
- Метрики, alerts и stop criteria не содержат PII.

## 1. Expand, flags OFF

Отдельному migration-интегратору нужен append-only message origin binding или
согласованный эквивалент: organization, Message, ConnectionIdentity/MailConnection,
provider_message_id, SourceReference, binding epoch, state
unresolved/proposed/confirmed/rejected/superseded, evidence/origin, actor/time,
record_version, audit/correlation.

Verified current origin unique по (mail_connection, provider_message_id).
Partial legacy unique остаётся для unresolved до reconcile. Mailbox ACL и mapping
to Project отделены от ContextRelation. RFC fields scoped metadata, не merge proof.
Reconciliation decisions используют CAS и не содержат body/address.

Сохранить nullable bridge и legacy fields/constraint до перевода всех writers.
FK/check/index вводить online/NOT VALID по согласованному плану; данные не удалять.

Flags по organization + exact connection generation, изначально false:

- mailbox_identity_shadow_write;
- mailbox_identity_shadow_read_compare;
- mailbox_identity_primary_read;
- mailbox_identity_pilot_write;
- mailbox_identity_actions.

Глобальный env boolean недостаточен. Old/new writer одной mailbox/source не
работают параллельно без единого mailbox-aware idempotency facade.

## 2. Inventory

На отдельной восстановленной/test DB:

1. default dry-run и target fingerprint;
2. read-only inventory с новым report key;
3. повтор на неизменном snapshot — canonical JSON одинаков;
4. анализ unknown/partial, collisions, links, human-proof gaps;
5. candidate mapping готовится только внутри защищённого operator UI.

Opaque report не содержит данных для автоматического reconcile. Collision=0
может означать, что вторая строка была заблокирована старым unique.

## 3. Explicit reconciliation

Одна запись или явно выбранный batch:

- Message и expected record/context version;
- legacy projection только для сравнения;
- verified ConnectionIdentity + epoch + MailConnection namespace;
- exact provider_message_id и SourceReference из авторизованного adapter
  observation/provider export;
- evidence refs, actor authority, reason enum, decision/correlation ID;
- preview прямых Task/Draft/completion/context links без содержимого;
- outcome confirm/reject/leave_unresolved, никогда «первый кандидат».

Нужны tenant, mailbox.reconcile, source metadata и legacy project access.
Смена бизнес-контекста — отдельный Context confirmation; origin reconciliation
не переносит Project/Contract/Task/Draft.

CAS блокирует concurrent sync/correction. Confirm атомарно пишет binding/history/
audit. Повтор decision ID идемпотентен. Смена mailbox/epoch требует нового решения;
старое superseded, не overwrite.

При нескольких candidates, недоступном source, непроверенном account subject,
неполном export или только current project OAuth — оставить unresolved.

## 4. Shadow reads/writes

Для одного synthetic allowlisted mailbox:

1. ingress регистрирует verified identity/source и shadow origin;
2. legacy projection может обслуживать UI, dedup решает mailbox facade;
3. compare проверяет Message, connection/provider key, org, source/generation
   и origin после Context correction;
4. mismatch блокирует actions и пишет safe correlation;
5. retry/concurrency дают одну Message в mailbox;
6. два mailbox с одинаковым provider ID дают две Message;
7. thread/RFC совпадение ничего не объединяет;
8. body/attachments отсутствуют в job/audit/reconcile payload.

Shadow compare не repair. Unresolved legacy не участвует в download/send.

## 5. Controlled cutover

1. shadow mismatch=0 в согласованном окне, synthetic acceptance PASS;
2. PostgreSQL concurrency/crash, ACL/revoke, backup/restore PASS;
3. lookup/download/reply/import используют persisted origin, не Project token;
4. Context correction сохраняет origin; Task/Draft move не меняет source;
5. primary read только exact org/mailbox cohort;
6. затем pilot write; actions отдельно после общего Trust approval;
7. наблюдение collision/unresolved/denials без PII;
8. global unique меняется последней отдельной migration, когда old writers
   выключены для cohort и rollback readers готовы.

Direction email↔email_outgoing не новая Message identity. Send result пишется в
исходный mailbox scope. UNKNOWN send не retry вслепую и не success.

## 6. Rollback feature flag

Триггеры: cross-mailbox merge, ACL leak, origin drift, duplicate effect,
shadow mismatch, unexplained unresolved drop, retention breach.

1. выключить mailbox_identity_actions, затем pilot writes;
2. остановить новые jobs только cohort; committed receipts не отменять;
3. начатые atomic operations завершить/истечь по общему protocol;
4. primary reads вернуть к безопасной legacy read-only projection только старых
   объектов; new-only/unresolved не передавать legacy send/download;
5. сохранить bindings, decisions, relations, receipts, audit;
6. corrective mapping/cancel — отдельное approval;
7. shadow compare оставить только если policy разрешает source;
8. не rollback DDL/data и не удалять bindings обычным feature rollback.

Rollback не включает global dedup для новых mailbox IDs, не угадывает OAuth,
не удаляет Task. Повторный rollout начинается с нового inventory/reconcile version.
