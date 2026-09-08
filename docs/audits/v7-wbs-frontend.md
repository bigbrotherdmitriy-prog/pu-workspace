# PU Workspace v7 — frontend WBS tree editor

Дата проверки: 2026-09-08

Ветка: `codex/v7-wbs-frontend`
База: `73178e0db3be8f4f2aadde339c17725b605e88be`

## Результат

Подготовлен изолированный редактор иерархии ГПР/WBS. Он не подключён к
`App.tsx`: включение в основной экран допустимо только вместе с backend-моделью,
последовательной Alembic-миграцией и подтверждённым API-контрактом.

Поддерживается:

- строгая runtime-проверка `wbs_parent_id`, `wbs_order`, `is_summary` и
  `wbs_level`;
- дерево глубиной до пяти уровней (`0..4`) с визуальными отступами;
- разделы (summary), обычные работы и вехи;
- создание локальных разделов и работ без скрытой отправки;
- перемещение между разделами и изменение порядка среди соседей;
- локальный запрет циклов, родителя-работы и превышения глубины;
- leaf-only проверка календарного плана: summary не может попасть в расчёт;
- WBS-ссылки на новые объекты через `wbs_parent_ref`, без выдуманных ID;
- fail-closed при неполном ответе и блокировка автоматического повтора после
  неоднозначного результата сохранения;
- read-only режим для утверждённой версии и отсутствующих прав.

## Wire contract

Ожидаемые дополнительные поля каждого элемента ответа:

```text
wbs_parent_id: number | null
wbs_order: non-negative integer
is_summary: boolean
wbs_level: integer 0..4
```

Summary обязан иметь `duration_days=null`, `is_milestone=null` и
`predecessor_ids=null`. В `plan.tasks` и `plan.topological_order` должны быть
ровно leaf-элементы. При сохранении новый родитель адресуется
`wbs_parent_ref`; существующий — `wbs_parent_id`.

## Проверки

- новые unit/component тесты: `21 passed`;
- полный набор schedule-модуля: `95 passed`;
- TypeScript `tsc --noEmit`: PASS;
- production build `vite build --configLoader runner`: PASS;
- `git diff --check`: PASS.

Полный frontend suite был запущен, но общий процесс прекратился в текущем
Windows-окружении после первых тяжёлых integration-тестов без итогового
протокола. Это не засчитано как полный regression PASS; при интеграции нужен
обычный полный frontend CI.

## Изменённые файлы

- `frontend/src/modules/schedule/wbsReadModel.ts`;
- `frontend/src/modules/schedule/wbsReadModel.test.ts`;
- `frontend/src/modules/schedule/WbsTreeEditor.tsx`;
- `frontend/src/modules/schedule/WbsTreeEditor.test.tsx`;
- `frontend/src/modules/schedule/wbsTree.css`;
- `docs/audits/v7-wbs-frontend.md`.

## Ограничения и интеграция

1. Компонент не вызывает endpoint сам: интегратор передаёт функцию `save` и
   обрабатывает актуальный путь API, cookies/CSRF и HTTP status.
2. Удаление существующих строк оставлено текущему защищённому rows endpoint;
   WBS-компонент не обходит серверные блокировки финансовых и source-связей.
3. Календарные даты, rollup summary и прогресс не рассчитываются браузером.
4. Browser E2E и живой backend не запускались в этом потоке.
5. После backend-интеграции нужно заменить плоский `ScheduleRowsEditor` или
   разместить WBS рядом с ним под одним общим dirty/write lock; два независимых
   редактора одной ревизии одновременно включать нельзя.
