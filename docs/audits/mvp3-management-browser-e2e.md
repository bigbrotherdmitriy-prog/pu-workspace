# MVP3 management browser E2E

Дата: 2026-09-05

Ветка: `codex/mvp3-management-browser-e2e`

База: `73a4126`

## Решение

Browser-часть `M3-11` на синтетическом изолированном контуре — **PASS**.
Это не live-provider acceptance и не PostgreSQL runtime PASS.

Тесты открывают реальный `frontend/src/App.tsx` в Chromium. HTTP и WebSocket
закрыты deny-by-default; разрешены только явно заданные синтетические ответы.
Письма, документы, OAuth, Google, Яндекс и Telegram не использовались.

## Воспроизведённые дефекты и исправления

1. Late response старого проекта мог заменить attention нового проекта.
   В `useManagementCenter` добавлены request sequence и точная проверка
   активного project для load, history, mutation, proposal и digest ответов.
2. Viewer видел активные кнопки изменения обязательств, рисков и решений.
   `ManagementCenter` теперь получает вычисленное серверным контекстом UI
   право и блокирует governed mutation controls для роли ниже manager.
   Backend authorization остаётся обязательной и не заменяется UI-проверкой.
3. Attention не имел пользовательского фильтра. Добавлен доступный фильтр по
   obligations, tasks, risks и decisions; смена проекта сбрасывает выбор.
4. Старый storage E2E искал устаревшее название кнопки анализа. Ожидание
   синхронизировано с реальным безопасным действием «Анализировать без копии».
5. Playwright webServer через дополнительный `pnpm` child зависал при teardown
   Windows worktree. Harness использует pinned Vite entrypoint напрямую и
   поддерживает `PUW_E2E_EXTERNAL_SERVER=1` для явно управляемого локального
   preview; CI-поведение с собственным webServer сохранено.

## Сценарии

- attention load, filter и выбор exact entity;
- low-confidence obligation: human warning, блок `in_progress`, exact CAS и
  отсутствие автоматического retry после 409;
- evidence-backed risk/decision и отсутствие provider action;
- persisted digest preference: weekdays, quiet hours, in-app channel и CAS;
- durable digest enqueue с фактическим `job_id/status`, без выдуманного `%`;
- late initial response и late mutation response после project switch;
- viewer против manager controls;
- переход в «Исполнение и финансы» с Supply/Forecast и возврат без поломки
  центра управления.

## Результаты

```text
Новые management Chromium E2E:  6 passed
Полный существующий Chromium:   25 passed
Management unit tests:          20 passed
Полный frontend Vitest:         177 passed
TypeScript application check:   PASS
TypeScript E2E check:           PASS
Synthetic production build:     PASS
```

Полный Chromium прогон был выполнен после UI/fixture изменений. После
дополнительного расширения stale guard отдельно повторены все 6 management
сценариев и type checks.

## Ограничения

- Fixtures доказывают browser contract, но не работу live Google/Telegram или
  внешнего provider action.
- PostgreSQL concurrency и реальный scheduler/worker данным потоком не
  проверялись.
- Digest проверен только для `in_app`; внешние каналы остаются отключёнными.
- UI role guard использует уже загруженные `/auth/me` и project membership;
  окончательное решение всегда остаётся у backend authorization.
- Browser artifacts находятся под `frontend/node_modules/.cache/` и намеренно
  не входят в git commit; workflow публикует их только как временный artifact.

Production, backend models, migrations, schema pins и provider effects не
изменялись. Push, merge и deploy не выполнялись.
