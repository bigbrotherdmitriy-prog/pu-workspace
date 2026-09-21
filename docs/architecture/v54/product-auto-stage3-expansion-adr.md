# ADR: Stage 3 — безопасное расширение Product AUTO

**Статус:** ACCEPTED — владелец разрешил реализацию только `notification.internal.create`; остальные кандидаты остаются `CONFIRM`

**Дата:** 2026-09-21
**Область:** Product AUTO allowlist после `task.internal.create`

## 1. Контекст и неизменяемая граница

Текущий узкий AUTO-контур допускает только `task.internal.create` при
высокой confidence, ограниченном TTL и квоте, с exact policy/action/payload
binding, owner authority epoch, live recheck, durable dispatch, immutable receipt
и audit. Внешние, финансовые, юридические, access и необратимые
действия остаются `CONFIRM` или `DENY`.

Этот ADR разрешает только узкий `notification.internal.create` с описанными
ниже ограничениями. Любое другое будущее включение требует отдельного
решения владельца, реализации, набора негативных тестов и отдельного
production rollout.

### Предварительная проверка

Перед реализацией сверены актуальный `main` и доказательная база Stage 1:

1. `task.internal.create` подключён к default-off product runtime;
2. Stage 3 добавляет независимый default-off флаг для notification AUTO;
3. внешние и повышенно-рисковые эффекты не расширяются и остаются `CONFIRM`.

## 2. Критерии допуска в AUTO

Новое действие может попасть в allowlist, только если одновременно:

- эффект только внутри БД PU Workspace;
- action и effects заданы закрытым серверным каталогом;
- отсутствуют provider call, финансовый, юридический, access и
  необратимый эффект;
- повтор exact command возвращает тот же receipt, а другой payload с тем же
  idempotency key даёт conflict;
- domain mutation и receipt фиксируются в одной транзакции;
- target/evidence/policy/authority версии повторно проверяются под
  блокировкой непосредственно перед mutation;
- есть узкая quota, короткий TTL, owner kill switch и fail-closed downgrade
  в `CONFIRM`;
- после сбоя не возникает второго бизнес-эффекта.

Confidence не снижает risk class. Она является лишь одним из
дополнительных allow-условий.

## 3. Кандидат 1 — `notification.internal.create`

### Решение

**Рекомендовать первым.** Это самый малый шаг от уже доказанного
DB-only AUTO.

### Разрешённый эффект

Создать одно in-app уведомление из фиксированного серверного шаблона:

- только в PU Workspace, без email/Telegram/Google/provider outbox;
- только для текущего enabling owner или текущего исполнителя точно
  закреплённой задачи;
- только по verified source/event pin;
- содержимое — тип события, domain ref, срок и безопасная ссылка
  внутри проекта; модель не пишет произвольный actionable text;
- exact effects: `notification.create`, `management_history.append`.

Существующая таблица `notifications` уже имеет unique
`(user_id, dedupe_key)`, `record_version`, project/user scope и UI read-state. Это
хорошая domain-основа для exactly-once mutation; Trust receipt остаётся
авторитетным action receipt.

### Ограничения policy

- confidence: `>= 0.95` или deterministic server rule;
- TTL решения: не более 6 часов;
- quota: не более 3/час на проект, 1 на `(recipient, entity, event)` и 10/сутки
  на получателя;
- канал жёстко `in_app`, не из `NotificationPolicy.channels`;
- duplicate dedupe key возвращает исходный receipt;
- отозванная policy, expired TTL, changed authority/source/target version дают
  `CONFIRM` или `DENY`, но не fallback AUTO;
- никаких escalation jobs и никакого внешнего channel fan-out.

### Риски и контроли

| Риск | Контроль |
|---|---|
| Спам/усталость от уведомлений | Тройная quota, dedupe и только fixed templates |
| Ложная срочность | Не повышать priority и не создавать escalation; показывать «создано AUTO» |
| Неверный получатель | Серверный recipient resolver, live membership/authority recheck |
| Prompt injection в тексте | Не пропускать model-authored title/body; только template + refs |
| Сбой между mutation и receipt | Одна БД-транзакция с unique dedupe key |

## 4. Кандидат 2 — `task.internal.annotation.append`

### Решение

**Допустим вторым, но только после отдельного domain-контракта аннотаций.**

Это не общий `task.internal.update`. Действие только добавляет к задаче
отдельную внутреннюю аннотацию с evidence refs. Оно не меняет:

- status, assignee, due date, priority, title и description задачи;
- `result_note` и completion evidence;
- provider IDs и external action status;
- связанное обязательство.

Точные effects: `task_annotation.append`, `management_history.append`.

`TaskHistory.details` нельзя незаметно превращать в хранилище комментариев: сейчас это
история бизнес-изменений задачи. Перед AUTO нужен явный малый
append-only domain object или уже согласованный канонический comments API.

### Ограничения policy

- target — только task в том же project, с exact `record_version`;
- confidence `>= 0.95`, verified source/evidence pin;
- TTL — 6 часов;
- quota — 2/сутки на task, 5/час на project;
- maximum 500 символов, без HTML, URL, mentions и executable instructions;
- UI явно маркирует annotation как AUTO и показывает source;
- удаление/редактирование самой annotation не входит в AUTO: исправление
  создаёт новую annotation, а старая остаётся в аудите.

### Риски и контроли

| Риск | Контроль |
|---|---|
| Ложная аннотация выглядит как решение человека | Отдельный type/visual label; не менять status/result |
| Загрязнение карточки | Квота, dedupe по source/event, bounded length |
| Prompt injection | Текст — данные, не instructions; sanitizer; evidence link |
| Stale task | `record_version` CAS и live source/target recheck |

## 5. Кандидат 3 — узкий `task.internal.reschedule`

### Решение

**Не включать в первую волну. Оставить в `CONFIRM` до отдельного
доказательства.**

Общий `task.internal.update` непригоден для AUTO, потому что текущий
`PATCH /tasks/{id}` смешивает:

- перенос срока;
- смену исполнителя;
- завершение/отмену задачи;
- изменение result note и completion document;
- сброс уже одобренного/запущенного external action status.

Эти эффекты имеют разный risk profile и не могут быть одним allowlisted
action type.

Если возвращаться к кандидату позже, допустим только отдельный
`task.internal.reschedule` с точным effects
`task_due_date.update`, `task_due_date_history.append`, `task_history.append`.

Минимальная будущая граница:

- только task, созданная тем же AUTO intent/policy;
- status `assigned`, нет human update после create, exact `record_version`;
- нет Obligation, provider IDs и external action status равен `not_requested`;
- только correction срока по более сильному verified evidence;
- TTL 30 минут от create, не более одного reschedule на task;
- смена срока не более чем на 2 календарных дня;
- compensation — exact restore прежнего due date только при
  неизменившейся target version.

Даже с этими ограничениями due date меняет обязательство и может
повлиять на уведомления. Поэтому он не равен по риску annotation/notification.

## 6. Google Tasks / S10 sink-only

### Решение

**Не добавлять в Product AUTO allowlist на этом этапе.**

S10 sink-only — ценный паттерн проверки UNKNOWN: один dispatch,
scoped lookup вместо слепого retry, доказанный APPLIED и cleanup. Но это
контур приёмки изолированного bridge, а не разрешение product action.

S10 прошёл отдельную live-provider приёмку в защищённом sandbox-контуре
(run `35634121782`, create → lookup → cleanup). Это доказывает bridge и
reconciliation-паттерн, но не разрешает Product AUTO: реальный Google Task
задевает внешний provider и видимую клиенту
учётную запись. Для Product AUTO понадобятся отдельные доказательства:

- provider idempotency и scoped lookup для реального Google Tasks adapter;
- credential generation/capability/authority live recheck;
- безопасная compensation без потери чужих изменений;
- понятный UI для `UNKNOWN`, reconciliation и late cleanup;
- отдельный provider AUTO ADR и owner opt-in.

До этого Google Tasks остаётся `CONFIRM`.

## 7. Сводное решение

| Кандидат | Внешний эффект | Идемпотентная основа | Blast radius | Решение |
|---|---|---|---|---|
| `notification.internal.create` | Нет | Existing unique `(user_id, dedupe_key)` + receipt | Низкий | **Первый** |
| `task.internal.annotation.append` | Нет | Нужен append-only object + receipt | Низкий | **Второй, после domain-контракта** |
| `task.internal.reschedule` | Нет, но меняет обязательство | CAS/history есть; compensation нужно доказать | Средний | **Оставить CONFIRM** |
| Google Tasks / S10 | Да | Sandbox pattern, не product proof | Средний/высокий | **Оставить CONFIRM** |

## 8. Рекомендованный порядок

1. **Сначала `notification.internal.create`.** Один action type, только fixed
   templates, in-app only, owner/assignee recipient, 6-hour decision TTL и жёсткая quota.
2. Провести shadow и production pilot на одном проекте: снача hypothetical
   decisions, затем owner opt-in с малой квотой. Условия расширения:
   zero duplicate effects, zero cross-tenant/recipient effects, measured dismiss/read rate,
   no external job creation, successful kill switch/revocation tests.
3. **Затем `task.internal.annotation.append`**, но только после явного
   comments/annotations domain contract. Не переиспользовать скрыто `TaskHistory.details`.
4. `task.internal.reschedule`, любые другие task update и Google Tasks не
   включать без нового owner ADR и отдельной живой приёмки.

## 9. Gates первого кандидата

Для реализации согласованы и проверены:

1. exact action/effect catalog и policy schema revision;
2. владельца transaction для Notification + receipt + audit;
3. recipient resolver и live project membership/authority recheck;
4. fixed template catalog и запрет arbitrary model-authored body;
5. TTL/quota counters с PostgreSQL concurrency tests;
6. unique dedupe/replay/conflict semantics;
7. negative tests: revoked policy, stale owner epoch, stale target/source, cross-tenant,
   wrong recipient, quota exhaustion, duplicate workers, crash before/after commit;
8. доказательство, что action не создаёт BackgroundJob для email/Telegram/provider;
9. audit/UI показывают AUTO, policy revision, source ref, receipt и safe reason;
10. default-disabled migration, shadow mode, owner opt-in, kill switch и rollback plan.

## 10. Итоговое решение и доказательства

- В AUTO allowlist добавлен только **`notification.internal.create`**.
- Runtime имеет отдельный default-off флаг; решение ограничено TTL не более
  6 часов, квотой 3 операции/час на проект и 10/сутки на получателя.
- Повтор команды возвращает исходный receipt; unique `(user_id, dedupe_key)`
  и PostgreSQL-тест гонки подтверждают один domain effect.
- Durable recovery проверен для разрыва между enqueue и сохранением marker.
- Целевая группа: `140 passed, 4 skipped`; полный backend regression:
  `1881 passed, 82 skipped, 0 failed, 0 errors`; изолированный PostgreSQL:
  `18 passed`, включая конкурентную квоту и dedupe; Alembic имеет одну head
  `a54f001c0a10`.
- Затем рассмотреть **`task.internal.annotation.append`** как отдельную
  append-only возможность, а не часть общего task update.
- **Не разрешать AUTO** для общего `task.internal.update`, task completion,
  assignee changes, due-date changes, Google Tasks и любых внешних эффектов в
  рамках этого ADR.
