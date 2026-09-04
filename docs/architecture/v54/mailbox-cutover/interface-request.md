# Interface request MBX-CUTOVER-01

Адресат: schema/identity/permissions/Gmail integrator. Статус: **BLOCKING**.

## Требуемый единый контракт

1. Stable verified Google account subject/tenant → общая ConnectionIdentity,
   credential reference/generation и MailConnection. Email/token/project не identity.
2. Append-only origin history Message→MailConnection→provider_message_id
   →SourceReference с org/epoch/state/actor/evidence/CAS/audit. Текущих nullable
   Message columns недостаточно доказать origin preservation после move.
3. Verified unique (mail_connection_id, provider_message_id); partial legacy
   unique для unresolved; порядок снятия global uq_message_source после перевода
   всех writers и directions на mailbox facade.
4. Unresolved legacy без fake project/mailbox и запрет attachment download/send.
   Отдельный план обязательного legacy project_id.
5. Reconciliation command: permissions, reason, expected Message/origin/context
   versions, idempotency/CAS, preview links, confirm/reject/unresolved/supersede.
   Identity reconcile не подтверждает Project/Contract.
6. Tenant + identity + mailbox + source + project ACL для list/read/analyse/
   reconcile/import/reply/send/service actors. Admin — отдельный break-glass.
7. Authority contract, различающий human operator и service actor.
8. Mailbox-scoped provider thread + RFC Message-ID/In-Reply-To/References:
   correlation candidates, не identity/merge proof.
9. Один approved contact identity store и multi-project relations. Message link
   только явный; inventory не читает email для догадки.
10. Org+connection-generation flags, old/new writer exclusion, shadow mismatch,
    safe rollback без удаления history.
11. Safe audit: origin before/after refs, actor/correlation, без raw provider ID,
    address, subject/body/token.
12. Online migration/index/FK/check, restore, two-mailbox concurrency, transfer
    preserving origin, revoke, crash/replay, rollback acceptance.

## Предлагаемые DTO — названия согласовать

~~~text
MailboxOriginPin:
  organization_ref
  message_ref + expected_message_version
  connection_identity_ref + binding_epoch
  mail_connection_ref + expected_record_version
  provider_message_id (internal only; never audit/report)
  source_reference_ref + expected_record_version

MailboxReconciliationCommand:
  decision_id / idempotency_key
  origin_pin
  outcome = CONFIRM | REJECT | LEAVE_UNRESOLVED
  evidence_refs
  reason_code
  requested_by + authority_version
  correlation_id

MailboxReconciliationResult:
  binding_ref + record_version
  state
  message_ref
  safe_conflict_code
  audit_ref
~~~

Payload не содержит subject/body/address/token/attachment/base64. Helper работает
в caller transaction без hidden commit. Provider verification выполняется до
mutation и pins identity generation.

## Текущие блокеры

- Gmail service выбирается по текущему Message/Project;
- lookup — global raw ID и direction-sensitive;
- нет durable Google account subject binding;
- нет append-only origin history;
- legacy confirm audit не имеет надёжного actor/before; bulk не per-message;
- ProjectContact не представляет multi-project relation;
- нет RFC fields/mailbox-scoped thread correlation;
- реальный inventory DB не запускался и не авторизован.

До закрытия MBX-CUTOVER-01 разрешены review expand proposal и synthetic tool.
Shadow writer, cutover, backfill и Gmail actions включать нельзя.
