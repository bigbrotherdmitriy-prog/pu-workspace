# v5.4 synthetic CONFIRM pilot — foundation handoff

Дата: 2026-09-03. Статус: **FOUNDATION / runtime CONDITIONAL**, не PRODUCT PASS.
AUTO, реальные tenant, внешнее исполнение и весь пользовательский pilot не включены.

## Git и границы

- Точный **BASE_SHA: `2a3a3e194abf55d95041d0118fedb8ba14161326`**.
- Worktree: `pu-workspace-v54-pilot-foundation`.
- Ветка: `codex/v54-pilot-foundation`, создана заново без reset.
- Исходная worktree: `pu-workspace-commercial-p2-yandex360`,
  ветка `codex/commercial-p2-yandex360`, HEAD
  `83774aac726acd4e27b349e9194f30783158bde8`.
- Существующие изменения основной worktree не копировались, не изменялись,
  не индексировались: `backend/app/api/auth.py`, `backend/app/api/local_upload.py`,
  `backend/app/api/workspace.py`, `backend/app/schema.py`,
  `backend/app/static/app.js`, `docker-compose.yml`, `frontend/index.html`.
- Проверены ветки/worktrees и AGENTS.md в репозитории/родительских каталогах;
  применимых AGENTS.md не обнаружено.
- Один итоговый foundation commit; полный SHA передаётся в финальном сообщении.
  Для следующей волны **база = этот foundation commit**, не исходный BASE_SHA.
- Push/merge/deploy/VPS не выполнялись. Production .env/БД/аккаунты не читались.

Изучены integration glossary/decisions/ownership/acceptance/migration-handoff,
pilot.json/validate.py и исходные Source/Evidence, Context/Communication,
Action Trust contracts. Integration wire имеет приоритет над standalone shorthand.

## Existing → reuse → minimal additions → blockers

| Existing | Reuse | Минимальное добавление | Что пока блокирует runtime |
|---|---|---|---|
| Organization/User/Project/Contract/Message/Task/TaskHistory | Integer PK и доменные таблицы | Record versions; nullable Message origin + context CAS | Legacy writers ещё не участвуют в v54 CAS/cohort gate |
| IntegrationCredential | Ссылка на credential owner, без копии secrets | ConnectionIdentity + MailConnection namespace | Нет реального account verification/ACL epoch resolver |
| Document/DocumentVersion/SourceFolder/WorkspaceSnapshot | Существующее хранение и legacy version bridge | SourceReference/SourceVersion/SourceCurrent | Text ordinal не external revision; no-copy cutover не выполнен |
| OCR metadata/review | Не изменяется | Evidence + отдельная EvidenceAssessment | Версия/fragment permissions требуют owner resolver |
| Message context projection | Единственное сообщение, без второго inbox | DeadlineClaim + ContextRelation | Нет ingress/analysis/context service в этом потоке |
| BackgroundJob | Единственный transport | Action/revision/approval/receipt + PendingDispatch index | Enqueue сам commit; T1/T2/recovery ещё не подключены |
| AuditLog | Единственный журнал | 1:1 AuditExtension + DB-only append helper | Нужны role/retention policies и авторизованный writer/cohort routing |

Нового storage, graph, claim engine, scheduler, provider adapter и task executor нет.
Ветка staging `372b661eefebb9c154dd847e8c331acc2b128d94` не переносилась.
Snapshot → safe-copy не изменён и не объявлен reference-only.

## Общие типы и интерфейсы

Единые определения:

- `backend/app/core/v54_refs.py`: StrictDTO, TaggedId, ObjectRef, VersionPin,
  require_same_tenant. Extra fields, JSON-number IDs, bool/int ambiguity,
  ведущие нули, bigint overflow, неканонический UUID, неверные type/kind,
  unknown namespace/type отклоняются.
- `backend/app/core/v54_dto.py`: ActionEnvelope, CreateTaskPayload,
  CancelTaskPayload, DeadlineClaimInput, canonical_json/hash, parse_envelope_json.
  Только два CONFIRM internal action types; external publish/Obligation запрещены.
  Политики/claims/source/evidence/context pins включены в envelope.
  Дубликаты JSON keys, floats/NaN, невалидный Unicode и неканонические числа
  отклоняются; массивы pins уникальны и упорядочены, не нормализуются молча.
- `backend/app/core/v54_interfaces.py`: RequestScope, Resolver/Resolution,
  require_resolution, PilotGate, ContextConfirmation, ReviewCommand,
  DispatchBinding; Protocols ContextWriter, AssessmentWriter, TrustWriter,
  TaskMutation, AuditWriter/AuditAppend.
- `backend/app/core/v54_transactions.py:append_audit`: единственный добавленный
  concrete mutation helper. Добавляет AuditLog + AuditExtension через flush,
  **не делает commit/rollback/close/enqueue/provider call**. Требует начатую
  транзакцию и явный server authorization callback, принимает только True,
  не truthy/unknown. Safe event enum и ID refs вместо текста/PII/details.

RequestScope строится сервером после auth, не из письма/модели/API body.
ObjectRef валиден структурно, но не даёт прав. Resolver обязан проверить
реальное существование, server tenant, actor/project/mailbox/source/contract ACL,
точную версию и policies до выдачи metadata/count/fragment.

Resolution связывает результат с **конкретными actor, project, pin, operation**.
Unknown ACL/version/freshness/retention/residency/epoch/TTL и истёкший TTL
отказывают с одинаковым resource_unavailable. Dispatch evidence/claim требует
verified. Metadata resolution не заменяет fragment/dispatch разрешение.
Это исполняемая проверка результата resolver, **не реализованный ACL backend**.

PilotGate по умолчанию запрещает всё; даже явно synthetic-разрешение не позволяет
AUTO, ASSIST-execute, external или finance. Тестовые сроки/права не seed/default
реального tenant. Source policies отсутствуют по умолчанию, что блокирует
materialization; одно human review не заменяет source version/access.

Время payload пока date-only, явно UTC или Europe/Moscow в synthetic DTO.
Нет time-of-day scheduling и неявного UTC midnight. Расширение timezone/типов
payload — запрос foundation owner, не самостоятельная правка соседнего потока.

## Схема и единственные writers

Все новые модели находятся **в одном shared файле**
`backend/app/models/v54_pilot.py`; новые таблицы имеют префикс `v54_`.
Это не разрешение разным потокам менять этот файл.

| Модели | Семантика / ограничения | Последующий writer |
|---|---|---|
| ConnectionIdentity | UUID registry, tenant+provider+account unique; credential reference/generation, binding epoch; account/provider immutable | A: identity |
| MailConnection | UUID, unique(identity, namespace), scope FK, blocked default | B: communication, через A identity |
| SourceReference | UUID, origin project, scoped account+namespace+external ID+incarnation; locator, parent-source, policies, residency, sync/freshness | A |
| SourceVersion | UUID revision=1, immutable observation, run-key unique, provider revision/consistency, optional DocumentVersion bridge | A |
| SourceCurrent | Отдельный указатель source→version, composite FK к той же source/tenant; CAS через SourceReference.record_version | A |
| Evidence | UUID revision=1, composite source/version/tenant FK, locator/extractor/confidence/representation descriptor, без fragment bytes | A |
| EvidenceAssessment | Текущая verification/freshness/access projection; record_version CAS, явный reviewer/time для verified | A, review history через общий audit |
| DeadlineClaim | Stable UUID anchor; PK **(id, revision)**; evidence pins, date/timezone/provenance, review отдельно от Context | C: Task claim facade |
| ContextRelation | UUID assertion+lineage/revision; отдельный record_version/state/applicability; один confirmed project/contract на Message | B |
| ActionPolicy | Immutable (id, revision), CONFIRM-only synthetic policy artifact/hash, explicit expiry; не универсальный policy engine | C |
| PilotAction | Stable intent unique(tenant,message,claim anchor,action type), reservation fence/business projection | C |
| ActionRevision | Immutable (action_id,revision), envelope/hash, exact claim/policy, tenant+command key unique | C |
| ActionApproval | Exact action/revision/hash FK, command dedup, approver/expiry/epoch; mutable state только через audited Trust facade | C |
| ActionReceipt | Один immutable business receipt на action, approval/revision/hash FK, job/fence/target reference | C |
| PendingDispatch | Индекс T1 intent с exact approval/seal; pending/job_id; НЕ вторая очередь | C; enqueue recovery — интегратор |
| AuditExtension | UUID wire event → существующий integer AuditLog; unique tenant/subject/sequence; subject/action pins, approval/receipt/job/relation refs | Один writer C через append_audit |

Новые UUID не заменяют существующие integer ID. Дополнены только:

- Message: nullable mail_connection_id/provider_message_id/source_reference_id,
  context_version=1, analysis_required=false; composite scope FK и all-or-none origin.
- Project/Contract/Task: record_version=1; Project/Message scoped unique keys.
- models/__init__.py registration и schema.py ожидаемая head.

ContextRelation scope/target JSON имеют общий typed ref contract; узкий allowlist:
communication.project/contract/task/draft. Нет universal relation traversal.
FK/unique/check ограничения дополняются ORM structural validation: tenant refs,
evidence pins, context target type, seal/action/claim/policy binding.
Parent-source должен принадлежать той же identity/namespace.
Message origin проверяется против source/mailbox identity+namespace+external ID.
Обычная ORM правка immutable assertions и удаление истории запрещены.
Прямой SQL не является разрешённым write API; DB roles/retention writer — отдельный gate.

## Транзакционный контракт для трёх потоков

Все методы Protocol принимают caller Session. **Caller owns transaction**.
Helpers могут flush; commit/rollback/close, HTTP, provider calls и enqueue внутри
общей mutation transaction запрещены.

1. A записывает metadata observation/evidence; всё materialization отдельно
   авторизуется до I/O. Не полагаться на confidence или nullable legacy identity.
2. B подтверждает обе primary relations под lock Message + expected_context_version,
   плюс CAS record_version каждой relation. ContextConfirmation содержит
   expected_project_relation_record_version и optional contract counterpart.
   Договор сверяется с выбранным проектом resolver-ом. Аудит в этой же транзакции.
3. C Task-claim facade отдельно review exact DeadlineClaim revision; Context confirm
   не подтверждает срок и не создаёт Task.
4. C Trust T1 сохраняет exact revision/approval + PendingDispatch. Caller commit.
5. Wiring owner вызывает существующий queue.enqueue **в отдельной Session**
   со стабильным `ActionRevision.command_key`; job payload только IDs/pins.
   Crash commit-before-enqueue восстанавливается сканированием PendingDispatch.
   Terminal job не обходится новым key; нужен существующий authorized redrive.
6. C Trust T2 блокирует authority/policy → action → Message/context →
   claim/evidence в canonical порядке → Task. Все revocation writers обязаны
   участвовать в том же протоколе. Проверяются live ACL/versions/expiry и queue
   ownership: worker_id, attempts, locked_at, live lease. DispatchBinding использует
   **эти существующие поля**, не выдуманный queue lease_token.
7. DB-only Task helper, TaskHistory, receipt и audit — **один T2 commit**.
   Queue completion отдельно. Audit failure откатывает всю бизнес-транзакцию.
8. B создаёт communication.task по receipt id идемпотентно, не повторяет Task.
   Cancel — новый action+approval с expected Task version/status=assigned.

Здесь реализован append_audit и ограничения структуры, но **не реализованы**
T1/T2 executors, authority locks, context confirmation или domain mutations.
Receipt — business result, не журнал всех attempts. Для DB-only pilot неуспешная
транзакция откатывается без receipt; NOT_APPLIED/UNKNOWN не должны производиться
как повторяемые attempt receipts. External attempts/reconciliation требуют
отдельного контракта/расширения перед включением, не обхода unique receipt.

## Файловая передача следующей волне

| Поток | Исключительная область реализации | Что получает / ограничения |
|---|---|---|
| A — Identity + Source/Evidence | backend/app/integrations/connection_identity.py; backend/app/core/v54_permissions.py; backend/app/source_evidence/**; backend/tests/test_v54_source_evidence*.py | Общие ORM/refs/Resolver; account/ACL/freshness resolution. Не менять storage/OAuth/OCR/common models |
| B — Context/Communication | backend/app/context_communication/**; backend/tests/test_v54_context_communication*.py | Message origin, ContextRelation/CAS DTO, A resolver, C claim facade. Не пишет Task/approval/receipt/свой ledger |
| C — Task claim + Trust/Audit | backend/app/task_claims.py; backend/app/action_trust/**; backend/tests/test_v54_action_trust*.py, backend/tests/test_v54_task_claims*.py | DeadlineClaimInput, ReviewCommand, ActionEnvelope, TrustWriter, TaskMutation, append_audit. Нет legacy Task execution до отдельно согласованного DB-only helper |

Все common DTO/models/миграции/schema.py и текущий fixture/test foundation
заморожены за foundation owner; запросы изменений передаются ему.
Только интегратор после объединения владеет main.py, jobs/handlers.py,
jobs/scheduler.py и адаптацией существующих API/task_engine/Task writers.
В этой волне API/Gmail ingress/UI не назначаются двум исполнителям одновременно.
Первоначально A/B/C могут тестировать pure facades с synthetic resolvers;
результат stub tests не объявляется runtime PASS.

Общая fixture: `backend/tests/v54_pilot_fixture.py`, читает существующий
integration/pilot.json; seed только внутри теста с caller transaction.
Synthetic DB seed останавливается **до Task execution** на pending intent.
Сквозная docs fixture с create/cancel receipts остаётся ожидаемым сценарием,
а не доказательством реально выполненного pipeline.

## Миграция

Перед созданием подтверждена единственная head `f360a1b2c3d4`.
Новая единственная head: **`a54f001c0a01`**, down_revision=`f360a1b2c3d4`.
Файл: `backend/migrations/versions/a54f001c0a01_v54_pilot_foundation.py`.
Это frozen explicit DDL, без импорта текущих product models.
Исторические миграции не переписаны. Backfill, seed policy и provider calls отсутствуют.

Upgrade добавляет 16 небольших таблиц + nullable origin/CAS поля и ограничения;
старое uq_message_source сохранено. Две новые scoped unique на существующих
таблицах требуют оценки блокировок перед реальным rollout; production не менялась.

Downgrade сначала проверяет пустоту всех pilot tables и отказывается удалять
данные/историю. Offline destructive downgrade запрещён. Обычный operational
rollback — отключение pilot writers, а не DROP. Тестовый downgrade только до
предыдущей head на отдельной пустой БД, затем повторный upgrade.

## Проверки

Команды из backend с явной `DATABASE_URL=sqlite+pysqlite:///:memory:`:

- `python -m pytest tests/test_v54_pilot_foundation.py tests/test_schema_revision.py -q`.
- `python -m pytest tests -q --tb=short -rs`.
- `python -m alembic -c alembic.ini heads`.
- Из корня: `python docs/architecture/v54/integration/validate.py`.
- `git diff --check`.

Финальные результаты на этом дереве перед commit:

- Полный backend suite: **565 passed, 2 skipped**, 79.81 s.
- Foundation + single-head tests: **90 passed, 1 skipped**, 2.28 s.
- Пропуски: существующий tests/integration/test_postgres_schema.py требует
  PU_TEST_POSTGRES=1; новый test PostgreSQL требует явную изолированную БД.
- Alembic heads: ровно **a54f001c0a01**. git diff --check: PASS.
- 4 warnings полного набора: существующий Alembic config без path_separator;
  конфигурация не изменялась ради подавления предупреждения.

Документационный валидатор: PASS, records=37/actions=2/mutations=4/links=68.
Целевые тесты проверяют parsing/ID/version semantics, exact canonical hashes,
fail-closed gates, source/account/tenant constraints, immutable assertions,
dedup intent/command/receipt, wrong approval binding, primary context unique,
audit authorization/rollback и запрет destructive downgrade с данными.
SQLite работает с **PRAGMA foreign_keys=ON** в новой fixture.

При разработке выявлены и исправлены: порядок составного PK claim
(явно id,revision) и SQLite строковый boolean default (заменён SQL false).
Это дефекты добавляемого foundation, не заявления об исправлении production.

**PostgreSQL runtime — CONDITIONAL.** docker/psql/pg_ctl в PATH и стандартные
установки Docker/PostgreSQL не обнаружены; выделенная тестовая БД не задана.
Ничего не устанавливалось и production URL не использовался.
Подготовлен opt-in тест `test_postgresql_upgrade_downgrade_only_on_explicit_empty_test_db`:
`PUW_V54_TEST_DATABASE_URL`, localhost-only, database prefix `puw_v54_test_`,
проверка отсутствия таблиц перед стартом; полный upgrade, seed/rollback,
реальный вызов guard downgrade на непустой транзакции, downgrade→upgrade.
Не запускать на существующей БД. После теста остаётся пустая тестовая схема;
глобальной очистки/удаления БД тест не выполняет.

Offline PostgreSQL SQL generation PASS — **не** выполнение DDL/constraints на PG.
INT-01…23, two-worker crash/fence/lease/revocation races, provider/OAuth и browser
здесь не проверены и не помечены PASS.

## Блокеры интегратора

1. **Legacy Message cutover.** Global uq_message_source/raw-ID lookup остаются:
   два реальных mailbox с одинаковыми legacy source_type/external_id пока не
   поддержаны Message ingress. Source identity registry различает аккаунты,
   но это не закрывает legacy inbox migration. Нет mailbox-only Message с null
   project: требуется адаптация readers/auth до снятия этого ограничения.
2. **Authority/policy.** Нет утверждённых real roles, self-approval rules,
   retention/TTL, mailbox scopes, grant/revoke epoch writers. Resolver неизвестное
   запрещает, явный synthetic True не становится production policy.
3. **Version/immutability.** Новые record_version — структура, не магическая
   защита старых writers. Все pilot mutation paths должны использовать CAS/locks;
   legacy paths для pilot cohort должны route в facade либо deny. Polymorphic
   target existence/ACL/version проверяется resolver-ом, не одним JSON/FK.
4. **Execution wiring.** Existing enqueue/task helpers имеют собственные commit;
   их нельзя вызывать внутри T2 как будто они atomic. Task/Claim/Trust реализации,
   pending recovery и safe audit endpoints — следующая волна.
5. **Migration/CI.** Не выполнять production rollout по SQLite PASS.
   После PG review интегратор обязан обновить жёстко ожидаемую старую head в
   `.github/workflows/docker-smoke.yml:125` и
   `scripts/ci/durable_queue/run.py:127` (или обоснованно сделать ожидание
   runtime-schema-aware). Эти файлы в данной ветке не изменены.
6. **No-copy/staging/retention.** Не подключать source bytes/quotes/cache/OCR/staging
   по умолчанию. Representation здесь только descriptor; lifecycle/purge/backup
   replay не реализованы. Audit append без контента не заменяет DB roles и
   retention procedure. Ordinary delete blocked; sanctioned purge отдельным owner.
7. **Внешние execution/AUTO.** Выключены и не считаются выполненной приёмкой.
   Exactly-once Google/Gmail, external UNKNOWN reconciliation не доказаны.

## Изменённые файлы

1. backend/app/core/v54_refs.py
2. backend/app/core/v54_dto.py
3. backend/app/core/v54_interfaces.py
4. backend/app/core/v54_transactions.py
5. backend/app/models/v54_pilot.py
6. backend/app/models/__init__.py
7. backend/app/models/ai_secretary.py
8. backend/app/models/project.py
9. backend/app/models/organization_contract.py
10. backend/app/models/task.py
11. backend/app/schema.py
12. backend/migrations/versions/a54f001c0a01_v54_pilot_foundation.py
13. backend/tests/test_v54_pilot_foundation.py
14. backend/tests/v54_pilot_fixture.py
15. docs/audits/v54-pilot-foundation.md

Frontend, существующие API/CI/Compose/jobs, OCR, storage adapters и legal не менялись.
