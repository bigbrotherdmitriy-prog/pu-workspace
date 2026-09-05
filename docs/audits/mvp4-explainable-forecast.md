# MVP4: объяснимый прогноз сроков и ДДС

Дата: 2026-09-05

Ветка: `codex/mvp4-explainable-forecast`

База: `45d7ef80e9b7569e94d76be559c4100aef14ad4e`

## Результат

Создан изолированный read-only модуль прогноза. Он считает сроки, бюджет и накопительный план ДДС только из существующих записей проекта и возвращает:

- точную формулу каждого расчёта;
- список полей и идентификаторы записей-источников;
- exact Evidence pin, SourceVersion, страницу и координаты, когда такая связь фактически есть;
- confidence по строке и по всему прогнозу;
- причины рисков;
- стабильный SHA-256 `forecast_id`, к которому должно быть привязано будущее ручное подтверждение.

Прогноз всегда имеет `publication_state=draft`, `advisory_only=true`, `can_trigger_actions=false` и `requires_human_confirmation=true`.

## Формулы

### Сроки

1. Если есть факт завершения, используется он.
2. При факте 100% без даты берётся дата среза.
3. При факте 1–99% и известном фактическом старте: `ceil(elapsed_days × (100 - actual_progress) / actual_progress)`.
4. Без факта сохраняется дата текущего утверждённого baseline с пониженной confidence.
5. Невалидный факт прогресса блокирует расчёт.

### Бюджет

Оценка до завершения по строке: `max(plan, committed, actual, declared_forecast)`. Это консервативное правило: известный факт или обязательство не может быть скрыто меньшим планом.

### ДДС

Записи сортируются по фактической дате для `paid/received`, иначе по плановой. Остаток: `sum(inflow) - sum(outflow)`. Это не банковский остаток, а чистая позиция по внесённому в PU Workspace ДДС.

## Evidence и confidence

- Evidence считается exact только при цепочке `Document -> DocumentVersion -> SourceVersion -> Evidence` в том же project/organization.
- В API и UI из locator попадают только page и числовые coordinates. URL, путь и provider locator не выдаются.
- Непроверенное Evidence не может поднять confidence выше 0.69.
- Отсутствие exact Evidence ограничивает confidence строки значением 0.65.
- Общая confidence — среднее по фактически участвующим строкам ГПР, бюджета и ДДС.

## Безопасность

- Нет INSERT/UPDATE/DELETE, `flush`, `commit`, BackgroundJob и provider calls.
- Нет endpoint публикации, оплаты, изменения ГПР или внешнего действия.
- GET-router не зарегистрирован в общем FastAPI app в этой ветке.
- UI не подключён к `App.tsx`. Callback ознакомления получает exact `forecast_id`, но модуль его не сохраняет.

## Файлы

- `backend/app/execution_forecast/contracts.py`
- `backend/app/execution_forecast/engine.py`
- `backend/app/execution_forecast/repository.py`
- `backend/app/execution_forecast/api.py`
- `backend/tests/test_mvp4_explainable_forecast.py`
- `frontend/src/modules/forecast/types.ts`
- `frontend/src/modules/forecast/ForecastPanel.tsx`
- `frontend/src/modules/forecast/forecast.css`
- `frontend/src/modules/forecast/ForecastPanel.test.tsx`
- `frontend/src/modules/forecast/index.ts`

## Точка интеграции

1. После объединения Budget/DDS и supply/acts адаптировать repository к их точным evidence/document-version FK.
2. Подключить `app.execution_forecast.api.router` к FastAPI.
3. Загружать GET `/execution/forecast/{project_id}` через проектный controller и валидировать `parseForecastReport`.
4. Вставить `ForecastPanel` в финансовый раздел.
5. Для ручного подтверждения нужен отдельный DB-backed immutable acknowledgement, привязанный к `forecast_id`, user, project и authority snapshot. Это не подменено локальным UI-state.

## Проверки

- Backend forecast + соседние finance/GPR regression: `33 passed`.
- Frontend targeted: `11 passed`.
- Frontend полный Vitest: `155 passed`.
- TypeScript `check`: PASS.
- Production frontend build: PASS; сгенерированные `react_dist` не включены в коммит.
- `git diff --check`: PASS.
- Первый полный backend-прогон: `1028 passed, 15 skipped`, затем 235 setup errors из-за отказа Windows sandbox в доступе к system `%TEMP%/pytest-of-dpush`; ошибки не дошли до продуктового кода.
- Повтор с writable `--basetemp` прошёл 56% набора без setup errors, но был остановлен ради быстрой передачи интегратору; на 33% был один неидентифицированный failure, поэтому full backend PASS не заявляется. Целевой набор и затронутые регрессии полностью зелёные.

## Ограничения

- Нет миграции и персистентного подтверждения: это сознательная граница изолированного потока.
- Нет входного банковского остатка; ДДС показывает net position только по записям PU Workspace.
- Строки ГПР и бюджета в текущей схеме не имеют exact DocumentVersion FK; их confidence понижается, а доказательство не выдумывается.
- Прогноз дат линейный и не учитывает календари рабочих дней, зависимости этапов и resource loading: этих данных нет в текущей модели.
- Реальные клиентские данные, provider APIs, production, push, merge и deploy не использовались.
