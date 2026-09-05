# v5.4 — Identity / Source / Evidence synthetic facades

Дата: 2026-09-03. Статус: **SYNTHETIC FACADE / PostgreSQL CONDITIONAL**.
Это не PRODUCT PASS, не запуск CONFIRM execution и не включение реальных tenant.

## База, изоляция и аудит

- Точная база: `34dcc8306acd6d1bacf85e9ce799330fba907ed9`.
- Новая worktree: `pu-workspace-v54-source-evidence-pilot`.
- Новая ветка: `codex/v54-source-evidence-pilot`. Перед созданием одноимённых
  ветки/worktree не было; reset/cherry-pick пользовательских изменений не выполнялся.
- Основная worktree: `pu-workspace-commercial-p2-yandex360`, ветка
  `codex/commercial-p2-yandex360`, HEAD
  `83774aac726acd4e27b349e9194f30783158bde8`.
- Сохранены её семь незакоммиченных файлов: backend/app/api/auth.py,
  backend/app/api/local_upload.py, backend/app/api/workspace.py,
  backend/app/schema.py, backend/app/static/app.js, docker-compose.yml,
  frontend/index.html. Ни один не копировался, не редактировался и не индексировался.
- Проверены AGENTS.md в репозитории/родительских каталогах и worktree:
  применимых инструкций не обнаружено.
- Прочитаны foundation report, весь integration package, общие v54_refs,
  v54_dto, v54_interfaces, v54_transactions, models/v54_pilot и foundation fixture.
- Production .env, реальные аккаунты/документы, Google/Яндекс API и VPS не использовались.
  Push, merge, deploy запрещены и не выполнялись.

| Existing | Reuse | Минимальное добавление | Ограничение |
|---|---|---|---|
| Foundation ConnectionIdentity / MailConnection | Те же таблицы и integer tenant/project PK | IdentityFacade; MailConnection только чтение | Нет OAuth/credential writer |
| SourceReference / Version / Current | Scoped FK, observation UUID revision=1 | SourceEvidenceFacade, CAS через source.record_version | Нет provider I/O и legacy cutover |
| Evidence / EvidenceAssessment | Immutable assertion и отдельная projection | Создание, review CAS, recheck | Только synthetic metadata, нет фрагментов |
| ObjectRef / VersionPin / RequestScope / Resolution | Общий wire contract | Resolver / AssessmentWriter-compatible методы | Unknown → deny, не production ACL backend |
| AuditLog + append_audit | Единственный существующий writer | Подготовка enum event/sequence под owner lock | Не создаётся второй журнал |

Общие модели, DTO, schema.py, миграции, fixture и соседние потоки не менялись.
Alembic head остаётся `a54f001c0a01`; новых миграций не требуется.

## Публичные точки подключения

Все методы принимают **caller Session**; caller открывает транзакцию.
Helpers могут flush и брать row locks, но не вызывают commit/rollback/close,
enqueue, provider API, сеть или передачу AI. После любого исключения в mutation
caller обязан откатить всю транзакцию, а не продолжать частичную запись.

### IdentityFacade

- `register(db, scope, account_key)`: provider жёстко `synthetic`; возвращает
  ObjectRef ConnectionIdentity. Дедуп tenant/provider/account, без active-project
  эвристик. Повтор проверяет verified/generation/epoch, не оживляет legacy/revoked.
- `refresh(db, scope, identity, account_key, expected_version)`: CAS
  record_version; тот же аккаунт сохраняет UUID, binding_epoch и generation.
  Чужой account не перезаписывает запись.
- `revoke(db, scope, identity, expected_version)`: state revoked,
  binding_epoch/generation увеличиваются; связанные источники не перепривязываются.
- `replace_account(...)`: revoke старой identity + register другой в одной
  caller transaction. Другая identity не получает автоматически namespace,
  MailConnection или новые grants. Для неё нужна новая явная synthetic policy
  с известным binding_epoch; MailConnection создаёт поток Communication.

Возврат к ранее отозванному account автоматически не разрешён. Без отдельного
контракта re-verification это безопасный отказ, не попытка угадать credentials.

### SourceEvidenceFacade

- `register_source(db, scope, identity, namespace, external_id, object_kind,
  parent=None, incarnation=1)` → Source VersionPin с record_version.
  Только message/attachment. Attachment требует parent message того же
  tenant/account/namespace. External ID не уникален глобально:
  identity + namespace + external_id + incarnation.
- `observe(db, scope, source, identity, namespace, observation_key,
  provider_revision)` → (новый SourcePin, immutable SourceVersionPin).
  CAS SourceReference и смена SourceCurrent входят в ту же транзакцию, что
  append observation и audit. SourceCurrent нельзя переставлять отдельным
  незащищённым методом.
- Exact retry observation_key возвращает тот же current observation без
  продления freshness и без второго audit. Несовпадающая revision либо уже
  вытесненный observation → version_conflict. Новый key со старым CAS → конфликт.
- Отсутствие provider revision создаёт observation consistency=unknown:
  запись допустима для диагностики, но review/dispatch gate не проходит.
  Совпадающая строка revision при новом observation key не доказывает
  эквивалентность assertions: создаётся новый UUID и старое evidence становится stale.
- `mark_unavailable(db, scope, source, availability)`: CAS состояния
  access_denied/provider_unavailable/deleted/unknown, без удаления истории.
- `create_evidence(db, scope, source, version, evidence_id)` → EvidencePin.
  Exact caller UUID обеспечивает дедуп. Неверный source/version/tenant запрещён.
  Immutable запись имеет whole_object synthetic locator, extractor fixture/1,
  confidence unknown, без текста, digest, OCR или representation bytes.
  Initial assessment unverified; создание не означает юридическую проверку.
- `review(db, scope, command: ReviewCommand)`: live review gate,
  expected_record_version CAS assessment, actor/time из server scope,
  confirmed → verified, rejected → unverified. Не выполняет Task/actions.
- `resolve(db, scope, pin, operation, lock=False)` → foundation Resolution,
  привязанный к actor/project/exact pin/operation. Source, source_version,
  evidence поддержаны; чужие типы не получают fallback.
- `check_evidence_before_dispatch(db, scope, evidence, lock=True)`:
  отдельный свежий resolve + foundation require_resolution. Не является
  approval, Task execution или проверкой полного action envelope.
- `recheck_evidence(db, scope, evidence, expected_assessment_version,
  expected_source_version, observed_provider_revision)`: только явное
  синтетическое подтверждение ТОЙ ЖЕ revision/current. Здесь
  expected_source_version означает **SourceReference.record_version**.
  CAS source и assessment обновляет last_seen/last_checked/checked_at/TTL,
  но не Evidence revision/locator/extractor/source_version и не approval hash.
  Unavailable не превращается в available этим методом.

Reading metadata не даёт fragment permission. `fragment` всегда deny: нет
разрешённого хранения/материализации содержимого. Evidence lineage для этого
среза — синтетическая ссылка на целый объект, не OCR-доказательство конкретной даты.

## Полномочия, версии и fail closed

`SyntheticPolicy` — исключительно явно переданный сервером тестовый объект,
не HTTP DTO, не содержимое письма и не production seed/default.
Все поля обязательны: tenant/project, pin, точные actor/operation grants,
account/namespace allowlists, identity binding epochs, expiry, freshness TTL,
authority epoch, ACL, retention/residency и synthetic_only.
Нет неявных admin, wildcard, active-project fallback или роли из текста источника.

Проверяются существующий actor/project/tenant, неархивный проект, verified
identity с известными generation/epoch и verified_at, active MailConnection,
origin project источника, точное policy pin binding и synthetic residency.
Одинаковые external IDs разных аккаунтов/namespace не смешиваются.

Unknown ACL/version/freshness/retention/residency/TTL/epoch, недоступность,
отозванный mailbox/identity, сменившийся current и unverified assessment
блокируют dispatch. Повторно проверяется наличие review grant у проверявшего;
отзыв такого grant лишает assessment допуска. Новый более короткий TTL считается
от последней проверки, а не от текущего resolve. Время в будущем не считается fresh.

Отрицательный Resolution содержит только caller refs и состояния;
foundation require_resolution обязан проверяться потребителем. Нельзя считать
один acl=allow достаточным: version/freshness/verification могут запрещать действие.
Ошибки границы — resource_unavailable/version_conflict, без SQL parameters,
source content, external IDs и secrets. Логирования содержимого нет.
Correlation ID должен быть canonical UUID, генерируемым сервером.

## Транзакции и аудит

- Локальный порядок locks: project guard → identity → MailConnection →
  SourceReference → Current/Version → Evidence/Assessment. Повторная загрузка
  использует populate_existing; перед ней flush сохраняет pending caller changes
  даже при caller no_autoflush.
- CAS выполняет UPDATE WHERE record_version=expected и проверяет rowcount.
  SourceCurrent меняется только после успешного source CAS.
- Owner locks защищают allocation audit sequence; unique constraint foundation
  остаётся последней защитой от чужого некорректного writer.
- `source_evidence/common.py` не пишет AuditLog/Extension самостоятельно:
  делегирует **существующему v54_transactions.append_audit** и передаёт
  явный authorization callback. Audit failure откатывается caller вместе с данными.
- SOURCE_OBSERVED используется для observe/register/refresh/recheck,
  EVIDENCE_REVIEWED — только human review, BLOCKED — для revocation/unavailable.
  Тексты, fragments, external IDs, account keys и credentials не добавляются.

Сквозной T2 lock ordering других потоков здесь **не доказан**. До Task execution
интегратор обязан согласовать предварительную фазу authority locks с общим
порядком из integration/ownership-transactions.md. Нельзя впервые брать identity
lock после action lock, если другой writer берёт их в обратном порядке.

## Проверки

Команды из backend, явно `DATABASE_URL=sqlite+pysqlite:///:memory:`:

1. `python -m pytest tests/test_v54_source_evidence_pilot.py tests/test_v54_source_evidence_postgres.py -q --tb=short -rs`.
2. `python -m pytest tests -q --tb=short -rs`.
3. `python -m alembic -c alembic.ini heads`.
4. `python ../docs/architecture/v54/integration/validate.py`.
5. Из worktree: `git diff --check` и staged equivalent перед commit.

Целевые тесты: **42 passed, 1 skipped**. SQLite fixture включает FK enforcement.
PostgreSQL opt-in skip — не PostgreSQL PASS.
Полный regression: **607 passed, 3 skipped, 4 warnings**, 97.92 s.
Три skip: существующий PostgreSQL integration gate, foundation migration runtime,
новый source concurrent CAS runtime. Все требуют отдельной тестовой PostgreSQL.
Четыре warning — существующая настройка Alembic без path_separator; конфигурация
не менялась ради подавления предупреждений.
Документационный validator: PASS, 37 records / 2 actions / 4 mutations /
68 links / 8 legacy hashes. Единственная Alembic head: a54f001c0a01.

| Поведение | Доказательство |
|---|---|
| Same-account refresh, account replacement, legacy unresolved | Facade DB tests |
| Account/namespace scoped IDs, parent attachment | Facade DB tests |
| Cross-tenant/source/version/project deny | Facade DB tests |
| Immutable evidence + assessment CAS | Snapshot of all evidence columns; stale CAS |
| Stale/unavailable/revoked/unknown/version change | Negative resolver/dispatch tests |
| Freshness-only update | Same evidence pin/current observation/hash, assessment version advances |
| Shortened TTL / reviewer grant revocation | Regression first failed, then fixed |
| Replay actor/project/pin/operation | Shared require_resolution rejects mismatched result |
| Audit failure + caller rollback | Neither partial source nor audit survives |
| No commit/rollback/close inside helpers | Methods patched to fail during facade scenario |
| No content/secrets in errors/audit | Synthetic marker absent; details=None |
| Concurrent PostgreSQL CAS | CONDITIONAL: test provided, runtime not available |

При разработке сначала зафиксированы падающими regression-тестами:
legacy register bypass, revoked reviewer grant, shortened TTL и потеря pending
caller изменений при populate_existing/no_autoflush. После исправления — PASS.
Это исправления нового facade, не заявления о дефектах/проверке production.

### PostgreSQL opt-in

Локально docker/psql/pg_ctl не обнаружены, выделенный URL не задан.
Тест `test_v54_source_evidence_postgres.py` запускается только при явном
`PUW_V54_SOURCE_TEST_DATABASE_URL`: PostgreSQL, localhost/127.0.0.1/::1,
database с префиксом `puw_v54_test_`, без URL query options.
Нельзя подставлять production URL.

Тест создаёт собственную random schema, search_path только в неё, синтетические
таблицы/данные и две реальные connections. Barrier управляет гонкой; lock timeout
8 s, statement timeout 15 s. Ожидает одного победителя и один CAS conflict,
ровно один новый observation/current. Удаляет только созданную им schema в finally.
Никакого DROP database/public и глобальной очистки. Это create_all/CAS test,
не проверка production migrations. Миграционный runtime остаётся foundation gate.

## Interface requests / зависимости интегратора

1. **Durable authority.** Shared schema не содержит Source ACL/retention policy
   registry и mutable authority grant epoch. SyntheticPolicy — in-memory
   авторизованный fixture snapshot; его pin не выдаётся за ActionPolicy rules.
   Нужен согласованный server authority loader/revocation lock protocol.
   Вызов с прежним in-memory policy после внешнего revoke сам по себе не знает
   о смене grants; real tenant execution до этого запрещено.
2. **Assessment binding.** Нет persistent assessment policy revision/
   identity binding epoch на момент проверки. Сейчас применяются immutable
   evidence policy pins + явная текущая epoch allowlist + live identity state.
   Для исторической reauthorization нужны поля/DTO от foundation owner.
   Не добавлять их неявно через произвольный JSON.
3. **Audit semantics.** В общем AuditAppend нет IDENTITY_REGISTERED/
   IDENTITY_REFRESHED/IDENTITY_REVOKED/EVIDENCE_RECHECKED и отдельного
   EVIDENCE_REJECTED. Нужны поддержанные enum и safe decision metadata,
   чтобы после нескольких review восстановить каждое решение из истории.
   Сейчас rejected хранится как unverified, а human event EVIDENCE_REVIEWED
   не различает confirm/reject. Не считать это полным юридическим audit trail.
4. **Wiring/locks.** B владеет MailConnection, C — Trust/Task/ActionPolicy;
   они получают A refs и resolver, но не пишут A таблицы. Общий T2 guard phase
   и concurrent revoke/dispatch тесты — отдельный integration gate.
5. **Content/provenance.** Для fragment access, locator bbox/message spans,
   extractor/model versions и разных representation retention нужны общие
   typed inputs/authorized storage contract. Здесь только fixture/whole_object;
   никаких скачиваний/стейджинга/алгоритмов OCR. Не передавать реальные bytes.
6. **Legacy/cutover.** Не резолвить legacy null по active project или credentials.
   Не считать Source registry решением global legacy Message dedup.
   API ingress, jobs и другие adapters не подключались.
7. **CAS retry.** Caller откатывает failed transaction и заново читает guards;
   запрещён слепой retry с новым observation key ради обхода stale current.
   Registration unique race между проектами может безопасно завершиться отказом;
   собственных commit/retry loops facade не делает.

Новых migration requests в этой ветке не реализовано. AUTO/external execution
выключены; полноценный синтетический Communication-to-Action pilot зависит от B/C.

## Файлы и передача

1. backend/app/integrations/connection_identity.py
2. backend/app/core/v54_permissions.py
3. backend/app/source_evidence/__init__.py
4. backend/app/source_evidence/common.py
5. backend/app/source_evidence/facade.py
6. backend/tests/test_v54_source_evidence_pilot.py
7. backend/tests/test_v54_source_evidence_postgres.py
8. docs/audits/v54-source-evidence-pilot.md

Один итоговый commit поверх BASE_SHA; полный SHA указан в сообщении передачи
(самореференциальный SHA внутрь коммита не записывается).
Интегратор переносит только этот commit на основу с foundation 34dcc83.
Никаких изменений общих моделей, миграций, Core domain logic, Context/Task/Trust,
jobs, frontend, legal, OAuth, credentials и legacy integrations нет.
