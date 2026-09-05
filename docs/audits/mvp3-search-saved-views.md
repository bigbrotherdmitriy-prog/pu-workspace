# MVP3 M3-10 — поиск по проекту и сохранённые представления

Дата: 2026-09-05

Ветка: `codex/mvp3-search-saved-views`

База: `7f9699bfe668551d77f59fa6d6d932592f30875b`

## Результат

Реализован минимальный project-wide search без отдельного поискового движка,
графа или очереди. Поиск выполняется параметризованными SQLAlchemy-запросами по
существующим моделям и до чтения данных проверяет одновременно организацию,
проект и явное членство пользователя в проекте.

Поддержаны типы `project`, `document`, `contract`, `task`, `obligation`, `risk`,
`decision`, `message`; фильтры по строке, типу, дате, договору и контрагенту.
Для сообщений поиск намеренно ограничен названием источника: body, summary,
sender и attachments не возвращаются и не используются для широкого результата.

Ответ содержит только краткую проекцию сущности и проверенные ссылки на саму
сущность, договор и/или exact Evidence fragment. Тексты документов, evidence
excerpt, письма, адреса, provider IDs и PII в журнал поиска не записываются.

## Инварианты безопасности

- проект обязан принадлежать переданному `organization_id`;
- пользователь обязан быть явным участником проекта; глобальный admin не
  получает неявного cross-tenant search-доступа;
- типы и ключи сохранённых фильтров проходят allowlist;
- строковые значения передаются bind-параметрами, `%`, `_` и `\\` экранируются;
- `limit` ограничен диапазоном 1–100, query/counterparty — 200 символами;
- SQL scan ограничен 1000 строками на тип, а ответ явно сообщает
  `scan_truncated` и `scan_cap_per_type`;
- cursor кодирует только дату сортировки, тип, integer ID и fingerprint запроса;
  он связан с tenant/project/actor/filters, имеет предел 512 байт и fail-closed
  отклоняется при подмене или повторном использовании с другими фильтрами;
- сортировка детерминирована: дата по убыванию, затем тип и ID;
- никакие внешние действия не создаются (`external_actions_created=false`).

## Saved views

`SavedSearchView` — текущее owner-scoped состояние. Изменение и soft-delete
используют CAS по `record_version`. `SavedSearchViewHistory` хранит отдельный
snapshot каждой версии и защищён ORM-событиями от update/delete. В основной
`AuditLog` пишутся только безопасные IDs, версия и названия ключей фильтра — без
значений query/counterparty.

API:

- `GET /api/search/projects/{project_id}`;
- `GET|POST /api/search/projects/{project_id}/views`;
- `PATCH|DELETE /api/search/projects/{project_id}/views/{view_id}`;
- `GET /api/search/projects/{project_id}/views/{view_id}/history`.

Frontend использует отдельный `useProjectSearch`, debounce 250 ms, request
sequence против late response и fail-closed read model. В `App.tsx` заменена
только локальная сборка результатов на вызов hook; сохранена маршрутизация всех
ранее поддержанных типов.

## Миграция и обязательное действие интегратора

В этой изолированной ветке создана временная последовательность:

```text
a54f001c0a10 -> a54f001c0a12
```

Revision `a54f001c0a11` зарезервирован параллельным потоком ContractVersion.
Интегратор **обязан** после переноса ContractVersion изменить только
`down_revision` миграции `a54f001c0a12` на `a54f001c0a11`, получить ровно одну
цепочку `a10 -> a11 -> a12`, обновить schema/runtime pins на `a12` и повторить
PostgreSQL upgrade. Две Alembic heads оставлять нельзя; merge migration здесь не
создавалась намеренно.

## Regression-first и проверки

- RED: `ModuleNotFoundError: app.mvp3.search` до реализации;
- целевые backend/migration/legacy search: **12 passed**;
- первый полный backend: **1205 passed, 19 skipped, 1 fail** — сохранённый
  legacy-контракт требовал тип «Письмо»; исправлено добавлением безопасного
  message-name поиска, тест не ослаблялся;
- повторный полный backend: **1206 passed, 19 skipped**;
- frontend: **96 passed**;
- CI contract/harness: **153 passed** с ASCII `basetemp`; первый запуск из
  OneDrive-пути с кириллицей дал 3 path-encoding mismatch в shell-fixtures, не
  дефект продукта;
- TypeScript check: **PASS**;
- frontend production build: **PASS**; generated `react_dist` восстановлен и в
  коммит не включён;
- offline Alembic SQL `a10:a12`: **PASS**;
- PostgreSQL runtime: **не выполнялся**, отдельный `TEST_POSTGRES_DSN` не задан;
- production, DNS, production DB, push, merge и deploy не затрагивались.

## Ограничения

- Это bounded relational search, а не полнотекстовый индекс. При
  `scan_truncated=true` пользователь должен сузить фильтры; для больших корпусов
  потребуется согласованный PostgreSQL FTS-план, но не второй search service.
- Exact Evidence ссылки появляются только при существующей и принадлежащей тому
  же tenant/project evidence-привязке.
- Saved view API готов, но отдельный визуальный редактор сложных фильтров не
  добавлялся; текущая строка поиска уже использует новый endpoint.
- PostgreSQL CAS/concurrency и upgrade после будущей `a11` остаются обязательной
  интеграционной проверкой.
