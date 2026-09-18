# MVP-2: проверка статуса внешних Task/Calendar после ручного cleanup

Дата: 2026-09-18

База: `main` `6cdb23c46ef601ff8b41b51af8cdd95706ff1faf` (PR #30)

Среда: изолированный `puw-mvp2-live-test`, проект 1, тестовая задача 2. Не production.

## Статус и границы

**Подтверждено расхождение для Google Task.** После ручного удаления Google Tasks API вернул для того же сохранённого external ID HTTP 200 с `deleted=true`, но реестр PU Workspace продолжил показывать `external_action_status=executed`, внешний ID и локальную связь `sync_status=synced`.

**Удаление Calendar event не подтверждено provider read.** По сохранённому external ID запрос `events.get(calendarId="primary", eventId=...)` повторно вернул HTTP 200, `status=confirmed`. Поэтому нельзя утверждать, что именно связанное с этой задачей событие удалено или отменено. Локальная Calendar-связь также осталась `synced`.

Это не отменяет подтверждённый живой сценарий MVP-2 «письмо → предложение → подтверждение → создание Google Task/Calendar». Это отдельный пробел *post-success provider reconciliation*, важный для Трека E. Синхронное создание не доказывает exactly-once после timeout внешнего эффекта.

## Метод проверки

- В контейнере изолированного стенда выполнен одноразовый Python-процесс без замены файлов работающего приложения.
- PostgreSQL-сессия: `SET TRANSACTION READ ONLY`; результат `SHOW transaction_read_only = on`; в конце `rollback`.
- Из существующих связей тестовой задачи взяты точные external ID и task list. Токен был расшифрован только в памяти процесса; `GoogleWorkspaceAdapter.credentials()` не вызывался, чтобы не допустить его `db.commit()` при refresh. Токен, email, external ID, тело письма и полный ответ провайдера не выводились и не сохранялись в документ.
- Прямые provider GET: Google Tasks `tasks.get(tasklist, task)` и Google Calendar `events.get(calendarId="primary", eventId)`; никаких provider mutation, повторной публикации или пересоздания.
- Реестр перепроверен через существующий `app.api.tasks.list_tasks` с участником проекта в той же read-only PostgreSQL-транзакции. Это чтение сериализатора API, не отдельный HTTP-запрос с браузерной сессией.

| Источник | Результат повторного чтения |
|---|---|
| Google Tasks, exact linked ID | HTTP 200, ID совпал, `status=completed`, `deleted=true` |
| Google Calendar, exact linked ID в `primary` | HTTP 200, ID совпал, `status=confirmed`; `deleted` отсутствует |
| PU Workspace, задача 2 | `external_action_status=executed`; оба external ID присутствуют; обе локальные связи `sync_status=synced` |
| Сериализация реестра `list_tasks` | `external_action_status=executed`, оба external resource типа (`task`, `calendar_event`) по-прежнему возвращаются |

## Где возникает расхождение

- `backend/app/api/tasks.py:list_tasks` (сейчас строки 44–87) собирает реестр из `Task` и `ExternalResourceLink`, не выполняя provider GET.
- `backend/app/integrations/external_resources.py:external_id_for` возвращает сохранённый ID, пока локальный `sync_status` не равен `deleted`; удаление у Google само по себе это поле не меняет.
- `backend/app/google_tasks.py:sync_tasks_to_google` и `backend/app/google_calendar.py:sync_tasks_to_calendar` выполняют insert/patch/delete в ответ на локальный publish/update, но не являются фоновым read/reconciliation успешных ранее созданных объектов.

Следствие: **текущий реестр не обнаруживает автоматически последующее удаление или изменение внешнего объекта**. Для Task это доказано живым provider read (`deleted=true` при локальном `synced`). Для Calendar механизм имеет ту же архитектурную границу, но сам факт удаления связанного события пока не доказан: provider всё ещё возвращает `confirmed`. Не следует смешивать оба уровня доказательства или маркировать Calendar cleanup как PASS.

## Требование к Треку E (не реализовано этим аудитом)

Durable outbox должен учитывать не только `UNKNOWN` после timeout при создании, но и drift **после ранее успешного действия**: читать точный provider ID в нужном account/container scope, различать `deleted=true` / `cancelled` / 404 / 410 от временной ошибки, сохранять наблюдаемый статус и время проверки, показывать расхождение пользователю и не пересоздавать объект автоматически без отдельного подтверждённого решения. Нужны audit и тесты на ручное удаление/редактирование внешних Task и Calendar.

До такого механизма локальное `executed/synced` означает «создание когда-то было подтверждено», а не «объект существует сейчас». Calendar cleanup остаётся отдельным открытым пунктом: владелец должен сверить удаление именно связанного события в `primary` либо повторить provider read после фактического удаления.
