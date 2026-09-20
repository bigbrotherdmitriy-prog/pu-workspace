# MVP-3 M3-10: project search and saved views preflight

Дата: 2026-09-20
Статус: **READ-ONLY PREFLIGHT COMPLETE / IMPLEMENTATION NOT STARTED**

## Фактическое состояние

Текущий поиск выполняется только во frontend по уже загруженным массивам
документов, договоров, задач и сообщений. Он не является полным серверным
поиском: исходные списки ограничены пагинацией, результаты дополнительно
обрезаются, нет серверного cursor, фильтров и сохранённых представлений.
Для сообщений локальный поиск также не гарантирует строгую привязку к текущему
проекту, потому что inbox может содержать доступные, но ещё не подтверждённые
сообщения-кандидаты.

На текущем HEAD отсутствуют backend service/router/model для общего поиска,
таблица saved views и соответствующая миграция. Существующий document-only
поиск не заменяет общий контур M3-10.

## Минимальная архитектура

1. Server-side read model по Project, Document, Contract, Task, Obligation,
   Risk, Decision и Message с точным tenant/project scope.
2. Allowlist типов и фильтров: query, types, date range, contract и
   counterparty; bounded scan, стабильный opaque cursor и признак
   `scan_truncated`.
3. Минимальная проекция результата без body/excerpt/summary, provider IDs,
   attachments, произвольных source URL и evidence content. Переходы — только
   по server-generated entity navigation.
4. Одна новая versioned-таблица `saved_search_views`: organization/project,
   owner, name, filters JSON, active/deleted state, record_version и timestamps.
5. Saved views доступны только владельцу, изменяются через CAS и soft delete.
   История пишется атомарно через существующий `ManagementHistory`; отдельную
   таблицу истории создавать нельзя согласно принятому ADR.
6. Generic management-history endpoint не должен раскрывать приватные filters
   другим участникам проекта: требуется owner-check либо редактированная
   history projection без значений фильтров.

## Проверки

- cross-tenant, same-tenant/other-project и admin-without-membership isolation;
- exact-project filtering для unconfirmed messages;
- redaction результата, escaping `%`, `_`, `\\`, bind parameters и allowlists;
- cursor tamper/replay/stability и лимиты;
- owner isolation, CAS, soft delete и атомарная история saved views;
- одна Alembic head, PostgreSQL upgrade и concurrent CAS;
- frontend debounce, stale response, project switch, loading/error/truncated;
- сохранение/применение/переименование/удаление view;
- browser smoke для Ctrl+K, фильтра, пагинации и открытия результата.

## Граница

Historical implementation из commit `2442be9` можно использовать только как
поведенческую справку. Переносить её целиком нельзя: отдельная history-таблица
противоречит принятому ADR и текущей миграционной цепочке.

Реализация начинается только после отдельного подтверждения владельцем новой
saved-view сущности, миграции и frontend-интеграции.
