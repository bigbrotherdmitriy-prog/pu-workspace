# Ownership, транзакции и lifecycle

Все writers ниже — роли будущей реализации, не уже работающие сервисы.
Только один writer каждой сущности; совместная транзакция вызывает его helper,
а не обходит владение прямой записью из чужого модуля.

| Сущность | Единственный writer | Читатели | Переходы | Transaction owner / граница |
|---|---|---|---|---|
| ConnectionIdentity | Integration identity facade | Source, Communication, Action | verified→revoked; другой account новая identity | Identity owner; credential binding + epoch + audit |
| MailConnection | Communication facade | Source, permissions, Action | active/blocked, namespace неизменен | Communication; unique identity+namespace, ссылка на registry |
| Source/SourceVersion/representation | Source facade | Evidence, Context, Action | observation append; source current/degraded/tombstone | Source; source/version/descriptor refs атомарны, provider read вне DB txn |
| Evidence + assessment | Evidence facade | Claim, Context, Action/UI | immutable evidence; CAS assessment/revoke/purge | Evidence; проверка pinned version + assessment + audit |
| DeadlineClaim | Task claim facade | Communication, Action | unverified→confirmed/rejected; correction revision+1 | Task claim; pin/evidence/review/CAS + audit, без Task mutation |
| Message/checkpoint/analysis intent | Communication facade | Context, Source, Action | registered→needs_review/processed/blocked | Communication; accepted refs + analysis_required, затем enqueue |
| ContextRelation/primary projection | Context facade | Message/UI/Action | hypothesis→confirmed/rejected; correction→superseded + новая revision | Context; lock Message + CAS + обе primary relations + audit |
| Action revision/policy decision/approval/intent | Trust facade | Communication/UI/worker | freeze/approve/revoke/request_dispatch | Trust; version binding + durable pending_dispatch |
| Task/TaskHistory | Existing Task domain helper | Context/Trust/UI | assigned→cancelled в пилоте | Trust transaction вызывает DB-only helper; Task+history+receipt+ledger |
| ResponseDraft | Existing draft domain helper | Communication/Trust/UI | DRAFT; edit новая draft revision | Draft owner; никаких sent/approved проекций от анализа |
| Execution reservation/receipt | Trust facade | Queue/Context/UI | READY→EXECUTING→SUCCEEDED/NOT_APPLIED/UNKNOWN | Trust; unique tenant/action, active attempt, fence, receipt |
| Ledger/AuditLog extension | Единый audit writer | Все владельцы и ACL UI | append event; controlled retention event | Принимает caller DB session; НЕ commit, не второй ContextLedger |
| BackgroundJob | Existing queue | Scheduler/Trust/Communication | queued/running/retry/terminal по текущему контракту | Queue владеет своим commit, только ID payload |

## Реальные окна commit

В базе `jobs/queue.py:enqueue`, `task_engine.py:create_tasks_from_files` и
`api/tasks.py` имеют собственные commit; некоторые helpers создают Obligation
или вызывают publish_actions. Их вызов внутри обёртки не создаёт атомарности.

1. Ingress transaction (Communication): tenant/mailbox dedup, source origin,
   processing intent + analysis_required, checkpoint CAS; commit только после
   durable refs всей страницы. Не считать item fetch ошибку успешным анализом.
2. Context/Claim confirmation — разные команды; можно один UI flow, но две
   явные decision записи. Confirmation Context блокирует Message и обе связи;
   audit writer append в той же session. Ошибка ledger откатывает confirm.
3. Trust transaction T1: замороженная revision, проверенное approval и
   pending_dispatch на стабильном action identity. Commit до enqueue.
4. Вызвать existing enqueue в отдельной session со стабильным key. Crash до
   enqueue/после enqueue до отметки виден recovery scan pending_dispatch.
   Повтор key возвращает job; terminal job требует штатного authorized redrive,
   не нового key ради обхода. Pending-dispatch — индексируемое состояние action,
   не второй транспорт/очередь. Scheduler использует существующий запуск.
5. Worker получает job, не полномочия approve. Trust transaction T2 блокирует
   action, сверяет live lease/fence, grant, epochs/ACL, semantic pins и target CAS.
   Порядок locks общий: tenant authority/policy guard → action → Message/context →
   claim/evidence guards в порядке canonical ref → Task. Все writers revocation
   обязаны участвовать в этом протоколе; иначе race-safety остаётся блокером.
6. В T2 DB-only helper создаёт Task/TaskHistory, audit writer — DISPATCH_AUTHORIZED
   и SUCCEEDED, Trust — unique receipt и business projection. Один commit.
   Никаких queue.commit/provider calls внутри T2. Audit failure откатывает всё.
   Crash до commit: нет эффекта/receipt. После commit: есть оба; повтор читает
   receipt, а не создаёт вторую задачу. Queue completion отдельна: её потеря
   не теряет business receipt. Internal effect без receipt запрещённый результат,
   а не штатная разновидность UNKNOWN.
7. communication.task relation публикуется идемпотентным Context consumer по
   receipt ID; отставание проекции не повторяет Task. Consumer сохраняет
   собственный checkpoint+relation+audit одной транзакцией.
8. Cancel — новый action, новый approval и target Task version/status=assigned.
   Такой же T2; нет внешней публикации/финансовых зависимостей. Исходное create
   SUCCEEDED остаётся, Task становится cancelled. COMPENSATABLE, не стирание.

Business identity unique(tenant, message, claim anchor, action type) → action ID.
Revision, job_id, lease и wording AI не создают новую business identity.
Execution command key immutable связывает revision/hash; после dispatch нельзя
обойти reservation другим key/revision. Rejected/reconciled intents не воскресают
при повторном анализе. Legitimate новый effect требует отдельного решения/intent.

## Внешний UNKNOWN — только будущий fake-provider контракт

Для внешнего действия reservation/attempt коммитится до вызова provider.
Timeout/crash после dispatch → UNKNOWN; lease expiry разрешает reconciliation,
не повтор mutate. Пустой eventually-consistent поиск не NOT_APPLIED. Только
authoritative proof absence позволяет новый gate/retry. Поздний stale worker
не обновляет business projection; receipt observation принимает тот же audit
writer через ограниченную reconcile-команду после сверки identity/key/hash.
Fence не останавливает уже отправленный network request. Exactly-once Gmail/Drive
не доказано и не обещается. В first slice external effect executor отсутствует.

## Audit и retention

Один ledger writer расширяет существующий AuditLog, streams могут быть action
или context/claim subject: unique(tenant, stream ref, sequence), actor/time/
correlation/typed subject/version/reason, nullable action ref для pre-action events.
Context не фабрикует action ради аудита и не создаёт второй журнал. Payload,
email/quote/PII/секреты не копируются в append events; ACL-controlled refs вместо текста.

Tombstone сохраняет минимальную identity и unavailable, не прежнее имя/quote.
Purge удаляет разрешённым retention owner содержимое/embeddings/search/cache,
не перепривязывает историю к latest. Hash тоже может быть чувствительным: хранение
по policy, не исключение из purge. Ledger append-only для обычного writer;
специальная контролируемая retention процедура redacts/purges с безопасным событием,
не бесконечное хранение PII. Legal hold не возвращает revoked live access.
Restore обязан replay tombstones/revocations до открытия reads. Conflicting
retention/legal hold → блокировка и решение владельца. Универсальный TTL не назначен.
