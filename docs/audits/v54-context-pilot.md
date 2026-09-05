# v5.4 Context/Communication pilot — implementation handoff

Статус: **изолированный synthetic facade реализован; integration/runtime CONDITIONAL**.
Не PRODUCT PASS, не Gmail rollout, не доказательство PostgreSQL concurrency.

## База, аудит до изменений и scope

- Точная база: `34dcc8306acd6d1bacf85e9ce799330fba907ed9`.
- Новая worktree: `pu-workspace-v54-context-pilot`.
- Новая ветка: `codex/v54-context-pilot`; до создания ветка и каталог отсутствовали.
- Основная worktree `pu-workspace-commercial-p2-yandex360` осталась на
  `codex/commercial-p2-yandex360`, HEAD `83774aac726acd4e27b349e9194f30783158bde8`.
- Её семь пользовательских изменений не переносились и не редактировались:
  `backend/app/api/auth.py`, `backend/app/api/local_upload.py`,
  `backend/app/api/workspace.py`, `backend/app/schema.py`,
  `backend/app/static/app.js`, `docker-compose.yml`, `frontend/index.html`.
- Применимых AGENTS.md в репозитории/родительских каталогах не найдено.
- Прочитаны foundation report, весь integration package, общие refs/DTO/interfaces,
  transactions/models, shared fixture, текущие Message/Task и foundation tests.

Foundation уже содержит ConnectionIdentity/MailConnection, Source/Evidence,
ContextRelation, Message origin/context CAS, Action/Receipt и AuditExtension.
Context service в базе отсутствовал. Общие модели, DTO, fixture и миграция
`a54f001c0a01` сохранены без изменений. Legacy `uq_message_source` и non-null
`Message.project_id` НЕ сняты и НЕ обходятся.

## Изменённые файлы

1. `backend/app/context_communication/__init__.py` — экспорт facade/error.
2. `backend/app/context_communication/service.py` — DB-only facade.
3. `backend/tests/test_v54_context_communication.py` — synthetic contracts/negative tests.
4. `backend/tests/test_v54_context_communication_postgres.py` — opt-in PG barriers.
5. `docs/audits/v54-context-pilot.md` — этот handoff.

Никаких изменений Gmail API, Task/Trust, jobs, shared DTO/models/fixture,
frontend, OCR, storage, legal, production/.env/VPS. Новые модели и миграции не нужны
для поддерживаемого локального среза. Push/merge/deploy не выполняются.

## Реализованные операции

| Метод ContextCommunication | Результат / границы |
|---|---|
| extend_mail_connection(source pin) | Namespace extension существующей verified synthetic ConnectionIdentity, unique identity+namespace; не создаёт account/credentials. Namespace берётся из разрешённого SourceReference. Новая запись blocked до resolver review; revoked/blocked existing не активируется автоматически |
| register(mailbox, source, attachment pins) | Регистрирует synthetic Message с точным raw external ID и immutable origin по foundation validation. Должен быть один явно переданный attachment child той же identity/namespace. Тело/bytes не копирует; legacy text fields пустые; attachment принадлежит Source owner |
| propose(message, expected_context_version, project/contract/evidence pins) | Только hypotheses; deterministic UUID от message/context/target/evidence даёт replay без дубля, не зависит от wording/model. Evidence того же message или прямого attachment; проверяется pinned current observation |
| confirm(ContextConfirmation) | Lock Message, expected context_version, revision + record_version каждой выбранной relation, принадлежность contract выбранному project; атомарная primary projection + аудит |
| correct(ContextConfirmation старых primary, новые target/evidence pins) | Обязательные CAS всех старых primary; supersession обеих; новые immutable assertions с lineage/revision и provenance.supersedes. contract=None очищает старую связь; несовместимый contract отклоняется |
| handoff(message, ActionEnvelope, TrustWriter) | Read-only preflight context/origin/source/evidence/claim binding → только общий Trust.freeze. Не approve, не request_dispatch, не Task execution |
| project_receipt(receipt ObjectRef) | Читает сохранённый APPLIED create receipt, seal/approval binding, Task и права; unique receipt_id на communication.task. Retry не создаёт Task/Receipt. При более новом context проекция historical/stale, Task не переносится |
| analysis_payload(message) | Только message_ref, expected_context_version, correlation_id. Не enqueue и не новая очередь |

Ручное подтверждение не переписывает confidence, не review-ит DeadlineClaim,
не меняет ActionApproval и не создаёт задачу. Старое `context_confirmed=true`
без связей не импортируется как доказательство human decision.
Получение/отправка ответа, ожидания, эскалация и drafts здесь не исполняются:
это narrower foundation CONFIRM task pilot, не расширенный standalone design.

## Транзакции, история, безопасность

Все public helpers требуют уже открытую caller transaction. Нет скрытых
begin/commit/rollback/close/enqueue/provider calls. **При любой ошибке caller
обязан откатить transaction целиком**; нельзя поймать audit exception и commit
частичные записи. Проверены rollback confirm/correct и receipt projection.

Локальные mailbox writers сериализуются identity → mailbox → Message; Message
читается заново с populate_existing после lock. Далее target/evidence проверки
и SQL UPDATE с условием expected version/rowcount=1. Удаление/перезапись assertion
не используется. История — предыдущие ContextRelation + общий append_audit,
без второго ledger. Audit включает refs старых и новых primary и инициатора.
Используются разрешённые SOURCE_OBSERVED / CONTEXT_PROPOSED / CONTEXT_CONFIRMED
events; коррекция различима по supersession/provenance, не по новому enum.

Late analysis с прежней версией отклоняется; даже при актуальной версии анализ
не заменяет уже human-confirmed context. Повтор ingress после смены проекта
сохраняет текущий project/contract и исходный mailbox, повторно проверяя права.
Contact/rule-learning не выполняется: ручная правка относится только к Message.

Resolver проверяется через общий require_resolution: tenant/actor/project/pin/
operation binding, explicit ACL, current version, fresh/available, policy,
retention/residency, epochs и TTL. Никакого admin/unknown fallback. Дополнительно
проверяются DB tenant, contract.project_id и source parent/mailbox identity.
Права Task/action/source повторно проверяются даже на replay уже готовой проекции.

Input body/attachment не принимаются facade как команды. Prompt injection в
существующем synthetic Message не влияет на scope/policy/action. AuditLog.details
пустой; в provenance/job payload только refs/технические поля. SQL/Pydantic/
dependency exceptions преобразуются в безопасные reason codes без тела/SQL
parameters/токенов. RequestScope и correlation_id должны формироваться сервером;
они не извлекаются из текста письма. Новых HTTP parsing/error handlers здесь нет.

## Точный handoff интегратору

1. Подключить реальный `Resolver.resolve(db, scope, pin, operation, lock)` потока A
   и server audit authorization callback. Сейчас в тестах SyntheticResolver
   строго возвращает foundation Resolution; **это не реализация production ACL**.
   Для создания mailbox нужен review нового blocked extension; нельзя подменять
   отсутствие этой политики authorize=True в продукте.
2. Конструировать ContextCommunication с явными Resolver, PilotGate,
   authorize_audit, UTC clock. Gate выключен по умолчанию, допускается только
   подтверждённый synthetic cohort; facade дополнительно требует provider=synthetic.
3. A создаёт SourceReference/Version/Evidence одной synthetic message/attachment
   через свой writer. B получает pins, вызывает extend_mail_connection/register
   в caller transaction. Source bytes и parser остаются у A; B принимает уже
   выделенные candidate refs, не содержит LLM/OCR extractor и не выполняет I/O.
4. propose возвращает реальные relation VersionPins, не фиксированные UUID
   документационного примера. UI/интегратор читает их record_version и
   Message.context_version, строит общий ContextConfirmation. Начальная
   context_version=1, после confirm=2, после correct=3 и т.д.
5. Для correct команда содержит **старые** обе primary + их CAS. Отдельные
   project/contract/evidence arguments задают новый выбор. Нельзя передать только
   old project и молча инвалидировать unknown-version contract. После смены
   проекта следующий RequestScope должен относиться к актуальному проекту.
6. C отдельно пишет/review-ит DeadlineClaim. B только читает pin/Message binding;
   context confirmation не меняет verification или дату. C формирует общий
   ActionEnvelope с текущими relation pins, context_version, identity, source
   observations и evidence. Snapshot envelope из shared fixture с context=1
   нельзя посылать после реального confirm=2 без перестроения и нового seal.
7. `handoff(..., trust=TrustWriter)` вызывает **только freeze** и возвращает
   action VersionPin. B не берёт row locks перед Trust, чтобы не инвертировать
   порядок action→Message. C обязан в freeze заново проверить/заблокировать
   context, claim review, policy и immutable payload, обеспечить intent/command
   dedup. RecordingTrust в тестах симулирует только этот Protocol, не Task execution.
8. C/интегратор владеют approval, PendingDispatch, queue, T2, Task/History и
   business receipt. После их commit B вызывается с **ObjectRef существующего
   receipt**, не со словарём, обещающим успешный результат. B читает approved seal
   и реально существующий Task; checkpoint — unique ContextRelation.receipt_id.
   Audit failure откатывает только новую проекцию, следующий consume восстанавливает
   её; execution заново не вызывается.

## Ограничения / запросы общих изменений

| Блокер | Что требуется владельцу |
|---|---|
| Global legacy unique | При одинаковом source_type/raw ID другого mailbox возвращается legacy_mailbox_cutover_required. Нет salting IDs или фиктивного source_type. Новый registry сам по себе не даёт cross-mailbox Message support; нужен общий consumer/index cutover |
| Required project | Intake только в явный существующий source origin project с правами; это временная неподтверждённая projection, не автоматическое определение проекта. Нет mailbox-only null project / общего служебного проекта |
| Legacy unknown origin | legacy_origin_unresolved, без вывода identity из active project/токена/email; нужен отдельный разрешённый reconciliation flow |
| Resolver Message pin | В локальном bridge VersionPin(message, record_version=N) проверяет Message.context_version=N: общей generic record_version у Message нет. A/интегратор обязаны поддержать этот bridge явно или изменить frozen interface централизованно; не выдавать разрешение по несуществующему столбцу |
| Atomic authority/revocation | Локальные locks/CAS не доказывают совместимость с будущими A/C revocation writers. Нужны согласованный authority/identity lock order и PG races против revoke/dispatch, не только B↔B |
| Correction after pending action | B не переписывает approval/receipt/claim. Новая context_version/pins делают старый envelope непригодным; C обязан enforce на freeze/approve/dispatch. После execution Task остаётся на прежнем проекте, исправление — отдельный action |
| Historical receipt / stale evidence | Прочитать актуальные source/Task права обязательно; stale/unavailable evidence может отложить projection. Нельзя повторять Task ради восстановления read projection; reconcile принадлежит интегратору |
| Live writers/UI/jobs | Facade не подключён к HTTP/Gmail/jobs, не заменяет legacy APIs. Перед rollout pilot cohort должен route/deny legacy writes. analysis_required recovery и pagination этой веткой не подключаются |
| Durable analysis identity | Stable hypothesis UUID покрывает exact pinned replay. Общая persistable AnalysisRun/cursor модель отсутствует; новая версия evidence не является автоматически новым action, C dedup использует stable claim anchor |
| PostgreSQL | Нет доступного локального сервера/явной тестовой БД. B↔B concurrent tests подготовлены, но runtime gate не закрыт |

Никаких общих изменений для снятия этих ограничений в ветке не выполнено.

## Проверки

Команды из `backend`, явно `DATABASE_URL=sqlite+pysqlite:///:memory:`:

```
python -m pytest tests/test_v54_context_communication.py tests/test_v54_context_communication_postgres.py -q --tb=short -rs
python -m pytest tests -q --tb=short -rs
```

Из корня: `python docs/architecture/v54/integration/validate.py` и `git diff --check`.

- Собственный набор: **44 passed, 4 skipped** (PG только, не skip ошибок).
- Полный финальный regression после последней правки порядка locks:
  **609 passed, 6 skipped, 4 warnings**, 100.60 s.
  Skip: четыре новых PG barrier/consumer tests, существующий opt-in PostgreSQL
  schema integration и foundation PG upgrade/downgrade. Предупреждения —
  существующий Alembic config без path_separator, не изменялся ради подавления.
- `git diff --cached --check`: **PASS**. В staged allowlist ровно пять файлов;
  shared DTO/models/fixture/migration не отличаются от точной базы.
- Integration documentation validator: **PASS**, records=37/actions=2/
  mutation_checks=4/local_links=68/legacy_hashes=8; это не runtime.
- В tests есть ownership AST guard и runtime запрет hidden commit/rollback/close.
  Foundation fixture и существующие assertions не изменены.

PostgreSQL opt-in:
`PUW_V54_CONTEXT_TEST_DATABASE_URL=postgresql+psycopg://<test-user>:<test-password>@127.0.0.1/puw_v54_test_context`.
Только явно выделенная локальная БД с prefix `puw_v54_test_`; не брать DATABASE_URL
из production .env. Выполнить только новый postgres test file. Каждый тест создаёт
уникальную `context_test_<uuid>` schema и оставляет её для инспекции; автоматического
DROP/очистки нет. Connection timeout 5 s, barriers bounded. Это проверки модели/locks
через create_all, не подмена отдельной проверки Alembic upgrade/downgrade.

Четыре PG сценария: concurrent confirm; concurrent correct с supersession;
две ingress доставки; два receipt consumers. Независимые Session/соединения,
stale identity map и барьеры проверяют реальные блокировки. Пока не выполнены,
SQLite serial PASS не закрывает INT-09/12. Общий end-to-end INT-01, two-worker
T2/lease, revocation races и source materialization также не заявлены выполненными.
