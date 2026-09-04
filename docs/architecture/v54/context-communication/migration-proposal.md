# Migration proposal — без Alembic и без исполнения SQL

Это перечень изменений для согласования. Ветка не меняет модели, handlers или
данные. Существующие integer PK не заменяются массово на UUID: общий
[ObjectRef](../integration/glossary.md) явно маркирует kind, без неявного cast.
Не переносить schema/миграции из соседних веток без решения интегратора.

## Предлагаемое хранение

### ContextRelation

Одна новая реляционная таблица `context_relations` с полями из [контракта](contract.md).
`source_type/id`, `target_type/id`, `scope_kind/id` — typed references;
их целостность проверяет resolver allowlist, поскольку полиморфный FK не даёт
обычного SQL foreign key. Где target конкретен (Message, Project, Contract),
интегратор может добавить typed FK columns/checks без универсальной graph DB.
Нельзя полагаться только на пришедший organization_id; tenant каждой цели
проверяется сервером и повторно на записи.

Ограничения: PK relation_id; unique(lineage_id, revision); confidence null или
0..1; непустые evidence refs для новых claims; allowed lifecycle; CAS record_version.
Индексы (organization_id, source_type, source_id, relation_type, state) и
(organization_id, target_type, target_id, relation_type, state). Partial unique
для primary communication.project/contract по organization/source/type,
state=confirmed (не касается multi contact.project). Подтверждение альтернативы
и supersede старой выполняются атомарно под lock source Message.

Assertion payload immutable. Изменение lifecycle хранит event ref общего Ledger,
а прежние assertion rows не удаляются. Invalidation доступности источника не
обнуляет historical confirmation. Для live business projection используются
только confirmed + applicability=current с проверенными versions.

### Mail connection / Message

`mail_connections`: mailbox extension с connection_identity_ref, namespace,
state и record_version. Unique(connection_identity_ref, namespace).
Account key/credential generation принадлежат общему identity registry;
здесь не создаётся конкурентный credential/account master.
Mailbox ACL владеет существующий permissions-поток; требуются проверяемые
read/analyze/reconcile/send grants с project ограничениям. Простая строка
`created_by_user_id` не заменяет ACL shared mailbox.

Additive поля существующего Message:

- mail_connection_id nullable FK RESTRICT, identity_state verified/legacy_unresolved;
- provider_message_id nullable, rfc_message_id nullable, in_reply_to nullable,
  references_json и participants_json (строго структурированные адреса);
- direction_metadata и provider_labels (SENT/INBOX могут сосуществовать);
- source_reference_ref/version по контракту Evidence owner, received_at;
- context_version, primary_project_relation_id/primary_contract_relation_id nullable;
- analysis_required и processing_state (registered/blocked/needs_review/processed).

Оригинальный source_thread_id переиспользуется, индекс дополняется connection_id.
source_external_id остаётся legacy/compatibility identifier, не mailbox identity.
content/attachments_json сохраняются для старого пути, но новые pilot записи
содержат только разрешённую representation/metadata. Retention/cache и хранение
attachment принадлежат SourceReference owner. Не добавлять таблицу с base64.

`project_id` допускает null для новых mailbox intake records **только после**
адаптации read/permission handlers; legacy строки сохраняют прежний project_id.
`contract_id` nullable уже есть. Нельзя создавать фиктивный «общий проект» и тем
самым расширять видимость неразобранных писем. Временный общий inbox работает
по mailbox ACL, а не по правам на любой проект организации.

Unique(mail_connection_id, provider_message_id) WHERE identity_state=verified
заменяет для нового пути глобальный uq_message_source. Для unresolved legacy
оставить эквивалентную partial unique(source_type, source_external_id).
CHECK verified требует ненулевых connection_id/provider_message_id; resolver
проверяет tenant Message = tenant connection. NULL не должен обходить unique.
Перед заменой ограничения проверить collisions/direction duplicates и raw-ID
lookup во **всех** legacy consumers; смешанный rollout без защиты опасен.
Один provider_message_id в разных connections допустим, один в том же connection
возвращает существующее сообщение независимо от source_type/направления.

### Processing и analysis

`mail_sync_state`: PK (connection_id, scope_id), query_definition_ref/hash,
checkpoint_version, cursor/history_marker, page_started_at, last_success_at,
rescan_from, gap_state, safe_error_code. Один live cursor на scope; CAS.

`communication_analysis_runs`: id UUID, message_id FK, actor_id, input_fingerprint,
source_version_refs, context_version, extractor/provider/model/prompt versions,
generation, analysis_required/state, job_id nullable, result_refs, safe_error_code.
Unique(message_id, input_fingerprint, context_version, generation). Это processing
checkpoint, не очередь. Recovery использует существующие BackgroundJob и scheduler.

`communication_intent_links`: organization_id, message_id, claim_anchor_ref, action_type,
intent_key, proposal_ref, proposal_revision, analysis_run_id, execution_ref nullable,
target_type/id nullable. Unique(organization_id, message_id, claim_anchor_ref,
action_type); никакого собственного approval_status/approval token.
Revision/payload и execution state остаются у общего Proposal/Execution owner.
Эта таблица только dedup mapping; ContextRelation связывает итоговые объекты.

### ProjectContact и ожидание ответа

ProjectContact переиспользуется как contact identity с нынешней unique
(organization_id, normalized_email), без автоматического merge по name/company.
Связи с несколькими проектами выражаются **теми же** ContextRelation contact.project,
не второй таблицей с конкурирующей истиной. Старые project_id/contract_id остаются
legacy primary projection до отдельной UI-миграции; новый resolver читает все
подтверждённые отношения. Если общий контур выбирает `project_contact_links`,
он должен быть единственным write store с ContextRelation projection, а не dual master.
Это уточняет более ранний migration proposal Gmail-аудита.

`response_expectations`: id UUID, organization_id, mail_connection_id,
outbound_message_id, execution_ref, expectation_kind, task_id nullable, expected_participants_ref,
due_at, timezone, deadline_version, state, policy_ref, received_reply_relation_id
nullable, record_version. Unique(execution_ref, expectation_kind) для ответа.
Tick-история и исполнение эскалации — existing notification + shared Action Ledger.

Task/ResponseDraft не получают второй lifecycle. Для pilot требуется ссылка на
shared proposal/execution identity (через mapping выше) и версии записи для
preconditions. Существующие TaskHistory/TaskDueDateHistory переиспользуются как
бизнес-история; общий Ledger связывает их с action receipt. Нынешние hashes
source_excerpt не считать доказательством immutable version approval.

## Legacy identity и безопасное внедрение

1. **Inventory, read-only.** Снять counts/duplicates, проекты/контакты с ручным
   подтверждением, Message без mailbox provenance, source_type collisions,
   связанные Task/Draft и состояние sent. Не выгружать тела в отчёт.
2. **Expand, feature flag OFF.** Добавить nullable refs/таблицы/indexes, ничего
   не переназначать. Backup/restore gate и dry-run отчёт миграции обязательны.
3. **Legacy protection.** Каждый старый Message сохраняет project_id, contract_id,
   status, content и связи. `identity_state=legacy_unresolved`; даже доступный
   текущий GoogleOAuthToken не доказывает исходный mailbox после переноса проекта.
   `context_confirmed=true` не доказывает автора: код выставлял его и по confidence.
   Import relation получает origin=import/applicability=legacy_unverified, не
   выдуманный human approver. Existing display projection не меняется.
4. **Reconcile, только явно.** Оператор с доступом к исходнику и обоим scope
   подтверждает match по provider identity и source evidence. Preview показывает
   affected message/task/draft IDs; ничего не объединяет по одному RFC ID,
   одинаковому тексту, имени проекта или sender. CAS и audit фиксируют решение.
   Несколько возможных mailbox → оставлять unresolved, запрет auto-download/send
   по текущему project_id. Отдельный новый compose draft возможен после своего approval.
5. **Shadow pilot.** Один allowlisted synthetic mailbox и одна организация;
   registered refs → analysis/evidence/hypotheses, без tasks/send. Сопоставить
   expected candidates, duplicates и права с fixtures. Legacy writer не должен
   одновременно запускать старый ingest для того же сообщения.
6. **Controlled pilot writes.** После согласования owners включить confirmed
   relations и intents общего action контракта. Все routes для pilot objects
   (`ingest`, confirm-context/bulk, task/draft mutations, attachment import,
   send) знают pinned mailbox/version и единый gate. Остальные проекты остаются
   на старом пути. Legacy global dedup queries исключают новый path либо идут
   через mailbox-aware facade; только после этого менять unique constraint.
7. **Roll back feature, не историю.** Выключить новые ingress/execution для scope,
   разрешить текущим atomic операциям завершиться/провериться владельцем.
   Сохранить новые Message/relations/receipts read-only; не передавать queued
   pilot intents legacy send и не удалять уже созданные tasks. Не rollback
   DDL с данными; corrective действия только отдельным approval.

Исторические пользовательские связи не пересчитываются массово. Нет массового
rename, move, copy, удаления source и автоматического заполнения mailbox по active
project. Jobs payload не получает body/attachment при migration/recovery.

## Минимальные точки адаптации существующего кода

| Существующий путь | Переиспользовать | Нужная будущая граница |
|---|---|---|
| api/gmail.py: _headers/_message_text/_attachments | MIME parsing, bounded metadata, bulk filter | Нормализованный envelope + connection/source refs до ingest |
| sync_gmail_project | Gmail fetch, counts/error types | page/cursor, mailbox upsert, исключить прямой analysis side effect для pilot |
| import_gmail_attachment | Size/extraction guard | Pinned origin connection и Source/Evidence owner; не читать по target project |
| send_gmail | Формирование MIME/transport, sent_external_id | Invoke только из общего Execution; RFC headers + mailbox scope + reconcile unknown outcome |
| ai_secretary: project_candidate/ingest_message | Детерминированные признаки, suppression, объяснения | Для pilot result→hypotheses/intents; не создавать Task напрямую до action |
| confirm_context/bulk | Явный выбор, проверки организаций | CAS, relation history, invariant mailbox, invalidate pending approvals; не молча двигать исполненные задачи |
| project_contacts.py | normalize_email, CRUD/discovery | Multi-project relation resolver, сохранение explicit deactivate |
| TaskCompletionSuggestion/review | Предложение, ручной review и проверка проектов | Общий action для completed; reply != completed |
| api/tasks.py, api/responses.py | Валидация исполнителя/срока/текста, TaskHistory | Shared gate и immutable payload version, без обхода через legacy approved |
| integrations/contracts.py, actions.py | AIProviderAdapter, ActionAdapter, publish_actions | Не объявлять нынешний sync_tasks/calendar общим Execution/Ledger |
| jobs/queue.py | enqueue/lease/retry/fencing | Processing IDs, recovery commit-before-enqueue; не новая очередь |

## Решения интегратора до реализации

| ID | Нужно согласовать | Предложение этого потока / блокировка |
|---|---|---|
| I-01 | Evidence ref/version, claim_anchor и availability API | Использовать только owner refs; отсутствие стабильного anchor блокирует auto-dedup reanalysis |
| I-02 | SourceReference read/attachment/ACL/retention | Никакого локального surrogate staging; source недоступен → blocked |
| I-03 | Общий intent/proposal/action schema и названия action types | Наши logical types маппятся на owner schema; без второго API |
| I-04 | Атомарный audit для relation transitions и side effects | Owner transaction/outbox contract; без него EXECUTE не включать |
| I-05 | Tenant и mailbox ACL; global admin текущей базы | Pilot проверяет tenant scope явно; глобальный admin не обход доказательства доступа |
| I-06 | Устойчивый mailbox account key и credential epoch | Google profile identity contract должен быть проверен владельцем adapter; mock в пилоте |
| I-07 | Nullable project и UI mailbox intake | Не включать mixed path до адаптации consumers |
| I-08 | Типы версий ObjectRef и tombstone/retention | Старые integer IDs сохраняются; lifecycle не CASCADE историю |
| I-09 | CONFIRM internal task/send, разрешённая AUTO policy | Pilot default CONFIRM; confidence не изменяет policy |
| I-10 | Date-only deadline и timezone/рабочий день | Пилот Europe/Moscow, явные 18:00 только в synthetic evidence |
| I-11 | Владение новыми job kinds/handler и recovery scan | BackgroundJob существующий; реализация отдельным согласованным срезом |
| I-12 | Контактные связи и единственный write store | ContextRelation contact.project, без дублирующей link-table истины |
| I-13 | Фактический runtime gate | PostgreSQL two workers/CAS, fake provider effects, audit round-trip; unit mocks недостаточно |

Все I-01…I-13 — открытые решения интерфейсов/реализации, не утверждения о
готовности соседних потоков. API owners согласуют версии перед coding task.
