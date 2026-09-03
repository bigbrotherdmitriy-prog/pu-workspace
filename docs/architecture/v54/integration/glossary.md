# Общий словарь и wire contract v54.integration.1

## ObjectRef — единственное определение

`ObjectRef = {namespace, type, tenant_id, id}`. Все четыре поля обязательны,
дополнительные поля запрещены. Namespace пилота `pu`; type из реестра ниже.
`tenant_id` и `id` — TaggedId: `{kind:"int", value:"1"}` либо
`{kind:"uuid", value:"00000000-0000-4000-8000-000000000001"}`.

Для int value — положительная десятичная строка без плюса/ведущих нулей,
максимум signed bigint. Для uuid — canonical lowercase UUID с дефисами.
JSON number, "project-1", null, смешение тега и значения отклоняются; bool не int.
Existing integer PK/URLs остаются как есть: перевод в TaggedId выполняет
граничный resolver нового wire-контракта, а не миграция всех PK/старых API.
Тип PK выбирается по серверному реестру, не угадывается по строке.

| Типы пилота | ID kind | Единственная namespace |
|---|---|---|
| organization, user, project, contract, message, task, response_draft, background_job | int (существующие PK) | pu |
| connection_identity, mail_connection, source, source_version, evidence, deadline_claim, context_relation, action, policy, approval, receipt, ledger_event | uuid (новые records пилота) | pu |

Это реестр wire типов пилота, не предписание новых моделей каждому типу.
Source/Evidence standalone integer-примеры — старый design shorthand, не новый
wire. Legacy mapping сохраняет исходный kind; если будущий источник использует
integer PK, расширение type registry согласуется без неявного UUID cast.
Organization ref: id равен tenant_id. User ref — пользователь в tenant scope,
не новая пользовательская identity; membership проверяет сервер.
Нельзя принять tenant от клиента без сопоставления серверному владельцу объекта.
Равенство ObjectRef требует всех полей; внешние provider IDs не ObjectRef.
Ref и даже известный UUID не дают никаких прав на existence/read/execute.

## VersionPin

`VersionPin = {ref: ObjectRef, version_kind, value}` — ровно эти поля.
`value` positive integer; version_kind = revision либо record_version.
SourceVersion — отдельный immutable объект-observation, его revision всегда 1;
provider revision — opaque string внутри observation, не VersionPin.value.
Evidence revision в пилоте 1: исправление создаёт новый evidence ID и supersedes
ссылку; никто не переписывает fragment/source/extractor старого evidence.

| Объект / версия | Семантика | Approval binding |
|---|---|---|
| Source / record_version | Mutable locator/display/sync/current observation | ID и pinned SourceVersion; не весь mutable record_version |
| SourceVersion / revision=1 | Immutable observation конкретного read, не DocumentVersion ordinal | Exact ref; смена bytes/недоказанная эквивалентность блокирует |
| Evidence / revision=1 | Immutable source/version/locator/provenance | Exact evidence pin |
| Evidence assessment / record_version | Freshness, access, verification; не evidence revision | Не хешируется; проверяется live при approve/dispatch |
| DeadlineClaim / revision | Утверждение: дата, timezone, evidence set, source/override provenance | Exact claim revision; correction сохраняет anchor identity |
| ContextRelation / revision | Immutable assertion в lineage; correction новый relation ID/revision | Exact relation pin + Message.context_version |
| ContextRelation / record_version | CAS state/applicability; confirm не меняет assertion revision | Live confirmed/current; технический check не новая revision |
| Task/Project/Contract / record_version | Предусловие изменения доменного объекта | Exact целевая версия/версии контекста |
| Action / revision | Immutable sealed envelope | Exact action ID/revision/SHA-256 |
| Policy / revision | Версия правил и их hash | Exact revision/hash; изменённая policy требует нового freeze/approval |

Lineage UUID и revision Context не CAS. Confirm требует record_version и
Message.context_version, блокирует Message, обновляет primary project/contract
и общий ledger атомарно. Correction supersedes старую связь; late analysis
не может заменить подтверждённую связь. Applicability независима от state.

## Что инвалидирует approval

В envelope входят action/type/schema/executor/renderer versions, requester,
tenant/project/contract, exact effects/payload, target preconditions,
source-version/evidence/claim/relation pins, context_version, connection identity,
policy revision/hash, risk/autonomy/reversal и command idempotency key.
Правила байтовой канонизации: [pu-action-c14n-v1](../action-trust/contract.md#5-канонический-payload-и-version-binding).
Hash покрывает весь envelope. Typed refs теперь часть этого envelope; старые
standalone envelope hashes не переносятся в новый формат.

Новое approval обязательно при изменении payload, assignee/срока/проекта/договора,
target, account, effect set, evidence/claim/relation assertion, pinned observation,
policy/executor semantics либо action revision (даже при равном значении payload).
EXECUTING/UNKNOWN/SUCCEEDED нельзя превратить новой revision в второй effect.

Только last_checked_at, продление свежести в пределах той же policy, смена display
имени или успешный secret refresh НЕ требуют нового approval. Hash не включает
assessment.record_version и timestamps freshness. Source owner должен доказать
тот же original/version; новая проверка хранит assessment/check observation,
но не заменяет pinned SourceVersion в envelope. Неизвестная эквивалентность = BLOCKED.
Revoked ACL/expired TTL/unavailable evidence блокируют dispatch при прежнем hash.
После revalidation той же версии разрешён новый gate с прежним неистёкшим grant
только если он не REVOKED/INVALIDATED и authority/policy условия снова выполнены.
Отзыв самого grant окончателен. Freshness не даёт бессрочного права.

## Identity и claim

ConnectionIdentity — единственный registry у integration identity owner:
tenant + provider + verified provider_account_key. Namespace (mailbox/drive/bucket)
подчинён аккаунту; Source key = identity/provider/namespace/external_id/incarnation.
MailConnection — unique(identity_ref, mailbox_namespace) extension, без собственного
account key/token master. Credential reference и credential_generation находятся
у credential owner; generation меняется при credential replacement/revoke,
binding_epoch при смене полномочий/reauth. Refresh того же verified account
не меняет identity, но worker проверяет current generation/ACL. Другая учётная
запись — новая identity, не update старой. На dispatch generation берётся заново
после identity match; секрет в proposal не фиксируется. Legacy null/unresolved
не восстанавливается из active project/email/hash. Нужен явный reconcile.

Message unique(mail_connection, provider_message_id); RFC ID/thread/domain —
только evidence/hints, не глобальная identity. ACL — пересечение tenant,
mailbox, project, contract/source и fragment policy, до поиска/ранжирования.

DeadlineClaim пишет Task domain claim facade, НЕ Evidence и НЕ Context.
Минимум: anchor ObjectRef, revision, value(date/timezone), evidence pins[],
verification(unverified/confirmed/rejected), reviewer ref, override reason/ref.
Несколько evidence поддерживают один claim; наличие ссылок/высокий confidence
не подтверждает claim. В пилоте отдельный human review точного срока; context
confirm сам по себе его не подтверждает. Task пока не создаётся. Это узкий
domain-owned record, не универсальный движок утверждений/юридических истин.
Reanalysis использует стабильный anchor, не hash текста; ambiguous match → review.

## Независимые состояния и полномочия

Единые action type имена: create-internal-task → task.internal.create;
prepare-response-draft → response.draft.prepare (DRAFT, не EXECUTE);
send-external-message → message.external.send (вне первого среза).
task.internal.cancel — отдельная compensation. Старые logical aliases из Context
не становятся вторым каталогом исполнителей.

Relation: hypothesis/confirmed/rejected/superseded. Claim: unverified/confirmed/rejected.
Evidence: immutable + assessment verified/unverified, fresh/stale/unknown,
available/access_denied/provider_unavailable/deleted/unknown; UI status производный.
Proposal: DRAFT/PROPOSED/SUPERSEDED/WITHDRAWN. Action business и approval переходы
определены единожды в [Action contract](../action-trust/contract.md#3-state-machines).
Job lifecycle не business outcome. Receipt APPLIED/NOT_APPLIED/UNKNOWN не status Task.

Context confirmation, action approval и пользовательский факт оплаты — разные
решения. Выписка не обязательна для оплаты, но финансы вне пилота. Privacy
local_only != autonomy ASSIST/CONFIRM/AUTO. Unknown policy/capability/evidence
fail closed даже для admin. Email/OCR/model instructions — данные, не authority.
