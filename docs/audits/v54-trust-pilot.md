# v5.4 DeadlineClaim / CONFIRM Trust pilot

Дата: 2026-09-03. Статус: **изолированные facade + synthetic contract tests**.
**НЕ PRODUCT / RUNTIME PASS**: реальный Task helper, роли/ACL и worker wiring
принадлежат интегратору и этим commit не подключены. AUTO, external execution,
финансы, реальные сообщения, production/VPS выключены/не затрагивались.

## База и аудит до изменений

- Точный BASE_SHA: `34dcc8306acd6d1bacf85e9ce799330fba907ed9`, доступен локально.
- Создана новая чистая worktree `pu-workspace-v54-trust-pilot`, ветка
  `codex/v54-trust-pilot`; до создания одноимённой ветки/worktree не было.
- Основная worktree `pu-workspace-commercial-p2-yandex360` оставлена на ветке
  `codex/commercial-p2-yandex360`, HEAD `83774aac726acd4e27b349e9194f30783158bde8`.
- Её 7 пользовательских изменений не переносились и не индексировались:
  `backend/app/api/auth.py`, `backend/app/api/local_upload.py`,
  `backend/app/api/workspace.py`, `backend/app/schema.py`,
  `backend/app/static/app.js`, `docker-compose.yml`, `frontend/index.html`.
- Применимых AGENTS.md в дереве базы и родительских каталогах не найдено.
- Прочитаны foundation report, integration README/glossary/decisions/ownership/
  acceptance/migration handoff, synthetic pilot records, общие DTO/interfaces/
  refs/transactions/models, foundation tests и **неизменённая** общая fixture.

Foundation уже предоставляет immutable assertion models, unique business intent,
revision/command/approval/receipt bindings, PendingDispatch, canonical JSON/hash,
Resolver/PilotGate/TaskMutation и общий `append_audit`. До этого задания конкретных
DeadlineClaim/Trust writers, T1/T2 и подключения DB-only Task helper не было.
`ActionReceipt` unique на action — результат бизнеса, не таблица попыток.
Старые queue/domain helpers имеют собственные commit и не подходят для вызова в T2.

## Что реализовано

| Часть | Реализация / граница |
|---|---|
| DeadlineClaims.extract | Получает существующий общий DeadlineClaimInput. Stable anchor задаёт server caller, не hash текста. Sequential revisions; exact duplicate возвращается без сброса review; другая дата/pins в той же revision конфликтуют. Всегда unverified |
| DeadlineClaims.review | Отдельный human-authorized ReviewCommand, exact revision + record_version CAS; confirmed/rejected не заменяется confidence или Context confirmation. Correction — новая unverified revision |
| TrustFacade.freeze | Повторно валидирует общий ActionEnvelope, проверяет policy/claim/context/source binding, сохраняет canonical hash. Одна identity (tenant, message, claim anchor, type), независимо от wording/revision/jobs |
| approve / revoke | Exact action/revision/hash, свой immutable command key, явный expires_at без defaults. Epoch reviewer из live Resolver. Revoke окончателен, audited; не отменяет уже существующий receipt |
| Revision change | Новый seal/command для той же identity; старые GRANTED → INVALIDATED, старый PendingDispatch отключён. EXECUTING/UNKNOWN/SUCCEEDED не открываются новой revision/key |
| T1 request_dispatch | Проверяет live pins/approval/version, сохраняет exact PendingDispatch; caller commit делает intent durable. Ни enqueue, ни новой очереди |
| T2 execute | Authority/policy → action → Message/context → claim/evidence → Task; exact DispatchBinding, current worker_id/attempts/locked_at/live lease и привязанный pending.job_id. Вызывает переданный TaskMutation.apply |
| Atomicity | DB-only mutation, receipt APPLIED, DISPATCH_AUTHORIZED/ACTION_SUCCEEDED через общий append_audit и business projection в одной caller transaction. Ошибка должна выйти в caller rollback |
| Cancel | Новый action, новый approval, target Task record_version, assigned/internal-only guard, ссылка на исходный APPLIED create receipt. Исходная история не стирается |
| Idempotent receipt | Повтор exact binding читает существующий APPLIED receipt с проверкой read permissions, не делает mutate. Может читать receipt после expiry/job completion — это history read, не новое исполнение |
| UNKNOWN | Не разрешает новый mutate или смену key/revision; UNKNOWN/NOT_APPLIED receipts здесь не создаются. External сценарий только в отдельном fake-provider contract test |

Immutable evidence pins входят в hash; mutable EvidenceAssessment timestamps/
record_version туда не добавляются. Freshness-only обновление той же версии
не требует нового seal, но stale/revoked/unverified/TTL expiry блокируют live gate.
Новая revision claim блокирует старый confirmed claim даже при неизменённой дате.

Expiry проверяется по часам сервера при approve/T1/T2. Отдельного таймера,
автоматически записывающего EXPIRED, нет: истёкший GRANTED **неприменим** независимо
от label projection. Политика и срок grant явно ограничены переданными server
resolution/gate и immutable policy expiry; реальные TTL/роли/retention не назначались.

## Контракт подключения

Используются неизменённые `TrustWriter`, `TaskMutation`, `DispatchBinding`,
`DeadlineClaimInput`, `ReviewCommand`, `ActionEnvelope` и foundation модели.
Дополнительные методы фасада: `revoke(..., approval)` и
`execute(..., binding, mutation) -> receipt ObjectRef`. Результат extract/freeze —
VersionPin, approve — approval ObjectRef, T1 — None. Public helpers не commit,
rollback, close или enqueue. Все методы требуют начатую caller Session transaction.

Конструкторы получают локальную связку `Guards` с обязательными зависимостями:

```python
authorize(db, scope, operation, subject, *, lock=True) -> bool
gate(db, scope) -> PilotGate
resolver.resolve(db, *, scope, pin, operation, lock=True) -> Resolution
```

Это injection seam, **не новый permissions backend/общий DTO**. Авторизатор должен
брать общий tenant authority guard **первым**, в том числе для read/approve/revoke,
и отличать `claim.extract`, `claim.review`, `action.freeze`, `action.approve`,
`action.revoke`, `action.dispatch`, `action.execute`, `action.receipt.read`,
`task.assign`, `audit.append`. Только точный True означает разрешение. Все реальные
reviewers/role writers/revocation writers обязаны участвовать в том же протоколе.
Fake SyntheticAccess этого не доказывает и не назначает продуктовые роли.

Для TaskMutation:

- binding передаётся без смены actor, action/revision/hash, approval, job или key;
- helper читает уже sealed revision и изменяет только Task/TaskHistory по её payload;
- никаких provider/Obligation/финансовых side effects, commit/rollback/close;
- guard target/version и отсутствие внешних/финансовых зависимостей — часть его
  domain-контракта, не побочный вызов legacy API;
- возвращает существующий Task ObjectRef того же проекта; facade проверяет
  базовый результат create/cancel, затем сохраняет единственный receipt;
- caller обязан откатывать транзакцию при **любом** исключении, а не ловить его
  внутри `with db.begin()` и коммитить частично изменённую Session.

Проверка сохранения transaction identity обнаруживает ошибочный helper commit,
но не умеет отменить уже совершённый чужой commit. Поэтому это дополнительная
диагностика, не способ безопасно обернуть старый helper с внутренним commit.

Успешный возврат receipt после lease expiry — только авторизованный history read.
Без receipt устаревший binding не мутирует. Наличие lease само по себе не
разрешает action. Action reservation/fence и unique receipt защищают бизнес-identity;
queue completion принадлежит следующей транзакции интегратора.

## Проверки

Команды из `backend`, с явным `DATABASE_URL=sqlite+pysqlite:///:memory:` и Python
из существующего workspace `.venv-pu-workspace-tests`:

```text
python -m pytest tests/test_v54_task_claims.py tests/test_v54_action_trust.py tests/test_v54_action_trust_external_contract.py tests/test_v54_pilot_foundation.py -q --tb=short
python -m pytest tests -q --tb=short -rs
```

Из корня: `python docs/architecture/v54/integration/validate.py`, `git diff --check`.
Предварительный полный regression: **633 passed, 2 skipped**, 108.64 s.
После него дополнительная проверка времени обнаружила окно: grant мог истечь
при разрешении pins, TaskMutation вызывался, затем post-check откатывал T2.
Новый regression требовал **0 вызовов**, до исправления получил **1** (1 failed).
Исправлено: live gate/policy/grant/lease повторяются после pin resolution и audit,
непосредственно перед mutation; expiry также проверяется после authority resolver.
Тот же regression после исправления: **1 passed** (0.43 s).
**Финальный полный набор после исправления: 634 passed, 2 skipped, 4 warnings,
87.97 s.** Все 69 новых scoped тестов прошли в этом запуске.

Новые scoped тесты: 69, без собственных skip. Унаследованные пропуски:
`tests/integration/test_postgres_schema.py` требует PU_TEST_POSTGRES=1;
foundation PostgreSQL migration/runtime test требует явную изолированную БД.
4 унаследованных Alembic warnings о path_separator не подавлялись правкой config.
Документационный валидатор: PASS, records=37, actions=2, mutation_checks=4,
local_links=68, legacy_hashes=8. `git diff --check` — PASS.

Тесты используют PRAGMA foreign_keys=ON и настоящие ORM/SQL-транзакции SQLite,
не mocked Session. `SyntheticTaskMutation` создаёт/отменяет тестовую Task/History
в этой БД, но это **test double**, не реализация legacy DB-only helper. Общая
foundation fixture не менялась; дополнительные Context rows и новые claim/action
IDs создаются только локальным test harness, а foundation pending intent остаётся.

Проверяются high-confidence evidence без human claim review; revision/policy/hash/
command conflicts; revoke/expiry; freshness-only vs live denied evidence; rollback
mutation/receipt/audit/T1; cancel без нового approval/на изменённой Task; ownership
reclaim по штатным полям; разрешённый receipt read; отсутствие payload в audit/logs.
AST-проверка запрещает собственные commit/rollback/close/enqueue/logging и импорт
legacy Task/API/queue/provider в новых facade sources.

External fake-provider test использует отдельный счётчик эффектов и проверяет
общий fail-closed business-state guard при UNKNOWN, пустом поиске, lease generation
и позднем receipt. Он **не реализует** внешнюю отправку, реальное reconciliation,
межпроцессную durability, новые attempt tables или повторяемые attempt receipts.

## Ограничения / interface requests / оставшееся wiring

1. **Runtime auth/locks — интегратор + permissions owner.** Реализовать указанное
   authorize/gate соединение с реальными ролями, self-approval правилами и tenant
   authority guard. Resolver обязан проверять actor/project/mailbox/source/fragment
   ACL, версии, active policy assignment и свежесть под совместимыми блокировками.
   Все callbacks DB-only, без provider I/O/commit; refresh источника выполняет
   его владелец вне T2. Не обновлять документы через resolver внутри dispatch.
   Замена всей policy другой identity определяется Resolver: локальный max revision
   может обнаружить только новую revision той же policy lineage.
2. **TaskMutation — интегратор.** Выделить DB-only helper в разрешённом ему legacy
   модуле, обеспечить CAS/историю/запрет финансовых и provider effects. Этот поток
   не менял task_engine.py, api/tasks.py или их существующие маршруты.
3. **Pending recovery/enqueue — интегратор.** После T1 commit сканировать pending,
   enqueue существующей очередью в отдельной Session со стабильным command key,
   затем связать pending.job_id. Нужны проверка exact seal/approval при linking,
   ID-only payload, сохранение прежнего job при terminal redrive; никаких bytes/
   base64/tokens. В тесте attach_job — явная имитация этого ещё не готового wiring.
4. **Command namespace.** ActionRevision key уникален внутри tenant, очередь —
   глобально. Server key factory должна обеспечивать глобально уникальные ключи
   для enqueue либо интегратор должен согласовать namespace mapping; нельзя
   присвоить другому tenant чужой job с совпавшим key. Фасад проверяет pending/job/
   command binding, не придумывает схему job.payload или новое имя job handler.
5. **Entry-point/cutover.** Интегратор подключает handlers/scheduler/main/API и
   исключает обход нового facade старыми writers для pilot cohort. Scope/actor/
   correlation формируются сервером, не из model/email/документа. Claim anchor
   стабильно выдаёт ingress/task owner; неоднозначный matching требует человека.
6. **Context consumer.** После receipt отдельный Context writer идемпотентно создаёт
   communication.task. Его падение не запускает Task повторно. Тестовый Context
   seed не является работающим подтверждением/consumer другого потока.
7. **Common interface extensions при необходимости.** TaskMutation сейчас
   возвращает только ObjectRef; расширенный безопасный результат/target pin после
   исполнения, human deadline override provenance/reason DTO и policy assignment
   guard должны согласовываться с foundation owner. Общие DTO/models/fixture и
   миграции здесь не менялись, дублирующих схем не добавлено.
8. **PostgreSQL/crash gates открыты.** Docker, psql, pg_ctl не найдены в PATH;
   PUW_V54_TEST_DATABASE_URL не задана. Two-worker barriers, concurrent revoke,
   потеря соединения/процесса, API restart и реальные leases не проверены на PG.
   SQLite serial reclaim simulation не доказательство FOR UPDATE/race guarantees.
9. **Логи/ошибки.** Фасад не логирует payload, evidence content или exceptions;
   общий append_audit пишет refs, без details. У интегратора остаётся safe exception
   mapping и запрет SQL echo/raw exception payload в runtime observability.
10. **AUTO/external/finance остаются выключены.** Не расширять ActionReceipt до
    attempt журнала. Для внешнего исполнения требуется отдельный contract/schema
    review; данный guard не обещает exactly-once Gmail/Drive.

## Файлы

- `backend/app/task_claims.py`
- `backend/app/action_trust/__init__.py`
- `backend/app/action_trust/guards.py`
- `backend/app/action_trust/validation.py`
- `backend/app/action_trust/state.py`
- `backend/app/action_trust/facade.py`
- `backend/tests/test_v54_task_claims.py`
- `backend/tests/test_v54_action_trust_support.py`
- `backend/tests/test_v54_action_trust.py`
- `backend/tests/test_v54_action_trust_external_contract.py`
- `docs/audits/v54-trust-pilot.md`

Один итоговый commit в своей ветке; полный SHA передаётся в финальном сообщении.
Staged scope проверен: только перечисленные 11 файлов; ни одного изменения
общих DTO/models/fixture, миграций или файлов интегратора. Основная worktree
повторно проверена: прежние HEAD/ветка и тот же список пользовательских изменений.
Push, merge, deploy, production/VPS и чтение production .env не выполнялись.
