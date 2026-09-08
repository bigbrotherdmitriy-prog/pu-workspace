# PU Workspace v7 — frontend финансового прогноза

Дата: 2026-09-08

Ветка: `codex/v7-finance-forecast-frontend`

База: `06bf2cc2c4d21238702ea16474282606f8627140`

## Результат

Подготовлен независимый read-only компонент `FinanceForecastPanel` поверх существующего ответа
`GET /execution/overview`. Компонент намеренно не подключён к `App.tsx`: интегратор может разместить его
рядом с текущим финансовым модулем после объединения параллельных потоков.

Отображаются:

- план, законтрактовано, факт, прогноз и отклонение;
- сумма и первая дата кассового разрыва;
- агрегаты по `contract_id`;
- агрегаты по листовым и сводным узлам ГПР/WBS;
- состояние надёжности и полный список решений владельца/юриста.

## Инварианты

- Локальный `readonly_view_scope.project_id` обязан совпадать с выбранным проектом.
- Серверные итоги сверяются с суммой подтверждённых строк `approved/active/closed`.
- Единственная отображаемая валюта — RUB. Строки иной валюты не конвертируются и не маскируются:
  несогласованный итог закрывает весь компонент.
- `variance = forecast - planned`; расхождение больше половины копейки блокирует вывод.
- Связь строки бюджета с этапом обязана вести в тот же договор; сводный WBS-узел не может быть прямой
  финансовой строкой.
- Иерархия WBS проверяется: существующий parent, summary-parent, уровень и отсутствие цикла.
- Отрицательный кассовый разрыв требует точной даты; дата без разрыва также считается противоречием.
- Любое утверждение о платеже, проводке или автоматической конвертации закрывает компонент.
- Ошибочные исходные значения, идентификаторы и внутренние причины не выводятся пользователю.
- Компонент не содержит кнопок и не создаёт внешних эффектов.

## Изменённые файлы

- `frontend/src/modules/finance/financeForecastModel.ts`
- `frontend/src/modules/finance/FinanceForecastPanel.tsx`
- `frontend/src/modules/finance/financeForecast.css`
- `frontend/src/modules/finance/financeForecastFixtures.ts`
- `frontend/src/modules/finance/financeForecastModel.test.ts`
- `frontend/src/modules/finance/FinanceForecastPanel.test.tsx`
- `docs/audits/v7-finance-forecast-frontend.md`

## Проверки

- Целевые Vitest: `14 passed`.
- Полный frontend Vitest: `403 passed`.
- `npm --prefix frontend run check`: PASS.
- `npm --prefix frontend run build`: PASS.
- `git diff --check`: PASS.

Сборка сохранила существующее предупреждение Vite о chunk больше 500 kB; новых runtime-зависимостей
компонент не добавляет.

## Точка интеграции

```tsx
<FinanceForecastPanel overview={finance} projectId={projectId} />
```

Контроллер уже добавляет `readonly_view_scope` после успешной загрузки конкретного проекта. Для полного
WBS-rollup backend `execution/overview` должен отдавать уже существующие у `ScheduleItem` поля
`wbs_parent_id`, `wbs_level` и `is_summary`. Пока они отсутствуют в overview, компонент корректно трактует
этапы как корневые листовые строки и показывает только прямые связи.

## Ограничения

- Название/номер договора отсутствуют в FinanceOverview, поэтому используется безопасная подпись
  `Договор #<id>`.
- Компонент не оценивает вероятность и не показывает вымышленные проценты.
- Это frontend/read-model proof; он не заменяет PostgreSQL-проверку серверного финансового расчёта.
- `App.tsx`, backend, production, секреты и реальные финансовые данные не изменялись.
