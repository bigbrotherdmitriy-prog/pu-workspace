# ADR: единая история управленческих сущностей MVP-3

Дата: 2026-09-20
Статус: **ACCEPTED**

## Решение

Для MVP-3 принят существующий в `main` Design A: единая полиморфная таблица
`management_history` и общий helper `append_management_history()`.

Мы не переносим параллельные `obligation_history` и `governance_history` из
старой review-ветки. Это создало бы два конкурирующих аудиторских контура,
дублировало уже применённый `record_version` и усложнило сводную историю
проекта.

## Почему

- дизайн уже работает в production и используется obligations, meetings,
  notifications, risks, decisions, tasks и project contacts;
- одна таблица сохраняет `organization_id`, `project_id`, actor, reason,
  evidence и полный JSON before/after diff;
- новый тип сущности подключается без новой history-таблицы;
- общий проектный audit читается одним запросом без `UNION`;
- optimistic concurrency остаётся на самих сущностях через
  `record_version` и `expected_record_version`.

## Обязательные инварианты использования

1. Любая подтверждённая пользователем мутация управленческой сущности должна
   записывать `management_history` в той же транзакции.
2. Запись обязана содержать правильные tenant/project, actor, action,
   resulting `record_version`, before/after и evidence/reason, когда они
   применимы.
3. Чтение истории всегда проверяет доступ к проекту и фильтрует одновременно
   по `project_id`, `entity_type` и `entity_id`.
4. При CAS-конфликте ни бизнес-сущность, ни история не изменяются.
5. Код приложения не обновляет и не удаляет строки истории.

## Принятые ограничения

- В текущей схеме нет отдельного per-entity `sequence`; порядок определяется
  `created_at` и `id`.
- Append-only пока обеспечивается контрактом приложения и тестами, а не
  database trigger.
- Поиск переходов статуса читает `old_values`/`new_values`, а не отдельные
  `from_status`/`to_status` колонки.

Эти ограничения не создают второй контур в рамках MVP-3. Если регуляторные
требования потребуют DB-level WORM/append-only или строгую нумерацию, это
будет отдельная совместимая миграция Design A, а не возврат к Design B.

## Отклонённый вариант

Design B из
`docs/audits/mvp1-management-history-architecture-comparison.md` отклонён
для интеграции в `main`: он требует двух дополнительных таблиц, не содержит
`organization_id`, не хранит общий field diff и связан со старой,
конфликтующей цепочкой миграций.

## Последствия

- Все новые MVP-3 области (meeting proposals, saved views, digests, resource
  catalog) используют `management_history` либо обычный `AuditLog` для
  невeрсионируемых технических событий.
- Финальные PostgreSQL и browser gates должны проверять атомарность
«состояние + история» и отсутствие истории у проигравшей CAS-транзакции.
