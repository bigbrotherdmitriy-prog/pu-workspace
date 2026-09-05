# MVP3 meeting/action extraction and digest foundation

Дата: 2026-09-05

Ветка: `codex/mvp3-meeting-digest`

База: `80188920f07042b77c8259b7db59e6e14a4ca3e9`

## Исходный риск

Legacy `PATCH /management/meetings/{meeting_id}` после сохранения протокола
синхронно вызывает эвристические `create_tasks_from_files` и
`create_governance_items`. Этот путь не требует immutable Evidence pin и не
отделяет предложение от подтверждённого управленческого действия. В рамках
данного безопасного среза legacy route не переписывался: новый контракт
добавлен отдельно и пока не активирован в production API.

Существующие `Meeting`, `Notification`, `Obligation`, `Decision`, `Task`,
Source/Evidence и `BackgroundJob` достаточны для foundation-среза. Новая
таблица и миграция `a11` не понадобились; в репозитории осталась единственная
Alembic head `a54f001c0a10`.

## Реализовано

### M3-05 — meeting/message → proposed action

- `MeetingProposalService` принимает только строгие структурированные
  кандидаты; поля raw minutes/message/content отсутствуют в DTO.
- Источник должен быть завершённой встречей того же проекта либо сообщением с
  уже подтверждённым контекстом.
- Для сообщения каждый Evidence обязан указывать на его SourceReference или
  непосредственный дочерний источник (например, attachment).
- Все pins повторно проверяются существующим `ManagementLifecycle.evidence`:
  tenant, project, current SourceVersion, availability, freshness и assessment.
- Результат — только `needs_confirmation` Obligation/Decision. Кандидат Task
  представлен предложенным Obligation; строка `Task` до подтверждения не
  создаётся.
- Подтверждение требует роли manager и CAS. Только после него создаётся
  идемпотентная внутренняя Task с `external_action_status=proposed`; Google IDs
  и другие provider effects отсутствуют.
- Повтор одинакового извлечения возвращает ту же запись. Попытка связать тот же
  evidence с несовместимыми данными закрывается `evidence_already_bound`.
- Audit содержит только тип/ID origin и тип/ID предложения; title, протокол,
  сообщение и evidence content туда не записываются.

### M3-07 — internal digest foundation

- `MeetingDigestService` собирает агрегат из существующего explainable
  `attention_page` и создаёт только внутренний `Notification`.
- В notification записываются агрегированное количество и переход в центр
  управления; названия объектов, документы, письма и excerpts не копируются.
- Один digest на пользователя/проект/локальную дату благодаря стабильному
  `dedupe_key` и существующему unique constraint.
- Учитываются IANA timezone, quiet-hours (включая интервал через полночь) и
  `in_app|disabled` channel preference. Внешних каналов этот срез не вызывает.
- Durable transport — существующий `BackgroundJob`, kind
  `mvp3.management_digest`. Payload содержит только project/user IDs,
  timezone, quiet-hours, channel и local date.
- Unknown/extra payload, stale local date, неверный timezone и чужой scope
  закрываются отказом; retry не создаёт второй notification.

## Проверки

- Regression-first: до реализации новый модуль не импортировался.
- Новые synthetic tests: `14 passed`.
- MVP3 foundation + migration + durable queue regression: `46 passed`.
- Полный backend с отдельным `--basetemp`: `1170 passed, 19 skipped` за
  `303.94s`.
- Alembic: одна head `a54f001c0a10`.
- Python compilation и `git diff --check`: PASS.
- PostgreSQL runtime не запускался; SQLite не доказывает конкурентный unique/CAS.
- Реальные Gmail/Telegram/Drive/AI и production данные не использовались.

## Изменённые файлы

- `backend/app/mvp3/meeting_digest.py`;
- `backend/app/jobs/handlers.py`;
- `backend/tests/test_mvp3_meeting_digest.py`;
- `docs/audits/mvp3-meeting-digest.md`.

## Статус и оставшиеся задачи MVP3

| ID | Статус после среза | Осталось |
|---|---|---|
| M3-05 | FOUNDATION PASS | Подключить новый сервис отдельными API/UI handlers; отключить legacy auto-create после миграции клиентов; PostgreSQL concurrency и browser acceptance |
| M3-07 | FOUNDATION PASS / PRODUCT PARTIAL | Durable scheduler cadence, сохранённые пользовательские preferences, UI и выбранный live-channel acceptance; внешняя отправка остаётся запрещённой |
| M3-08 | NOT STARTED HERE | Immutable версии договоров, корректное редактирование/архивирование, неразрушающее удаление, Contract card/graph со связанными документами |
| M3-09 | NOT STARTED HERE | Mailbox/project-scoped Company/Person/Contact identity, duplicate/conflict review, correction history и tenant isolation |
| M3-10 | NOT STARTED HERE | Permission-filtered project-wide search, stable pagination и saved views по project/contract/counterparty/type/date |
| M3-11 | BLOCKED BY M3-08..10 | Единая synthetic + PostgreSQL + browser + selected live-channel acceptance, restart/replay/correction и доказательство отсутствия unapproved external effect |

## Ограничения и интеграционный handoff

1. Этот срез не меняет production API; legacy extraction остаётся известным
   небезопасным путём до отдельной миграции.
2. Channel preference передаётся как строго проверенный job input, но пока не
   хранится в БД. Поэтому миграция `a11` сознательно не создавалась.
3. Digest handler использует общий worker и общую очередь; второй scheduler,
   queue, graph или ledger не добавлены.
4. При интеграции сохранить последовательность `a09 → a10`; schema pins не
   менялись.
5. Production/DNS/production DB не изменялись. Push, merge и deploy не
   выполнялись из-за действующей EU cutover freeze.
