# MVP3 management UI — isolated frontend result

Дата проверки: 2026-09-05
Ветка: `codex/mvp3-management-ui`
База: `f300f258270a4ba9389d4a0bf0a05395cd2b61a6`

## Результат

Подготовлен независимый UI-модуль `frontend/src/modules/management/**`. Модуль не подключён к
`App.tsx` и не меняет существующую навигацию. Он использует только уже существующие management API
и не выводит вычисленные или фиктивные проценты выполнения.

Готовы следующие блоки:

- `AttentionPanel` — состояния loading/error/empty/ready, приоритет, срок и причина внимания;
- `ObligationDetailPanel` — точная версия записи, evidence pins, низкая уверенность,
  история и CAS-конфликт;
- `RiskDecisionPanel` — раздельные действия риска/решения, evidence gate, история и CAS-конфликт;
- `MeetingProposalPanel` — подтверждение одной версии предложения; при отсутствии transport API
  работает fail-closed;
- `DeadlineDigestPanel` — deadline policy, quiet-hours, фактическое состояние сводки и только
  подтверждённые in-app уведомления;
- `useManagementCenter` — project-scoped загрузка attention/obligations/notifications,
  загрузка истории, versioned PATCH для obligation/risk/decision, создание/подтверждение
  meeting proposals и постановка digest в durable job;
- строгие runtime parsers: неожиданный статус, версия, дата, evidence shape или утверждение о внешнем
  действии переводят UI в безопасную ошибку.

## Использованные действующие endpoints

| Назначение | Endpoint |
| --- | --- |
| Контрольный список | `GET /management/v2/attention?project_id={id}` |
| Обязательства | `GET /management/obligations?project_id={id}` |
| История обязательства | `GET /management/v2/obligations/{id}/history` |
| CAS обязательства | `PATCH /management/v2/obligations/{id}` |
| История риска/решения | `GET /management/v2/{risks|decisions}/{id}/history?project_id={id}` |
| CAS риска/решения | `PATCH /management/v2/{risks|decisions}/{id}` |
| In-app уведомления | `GET /management/notifications?project_id={id}` |
| Создание предложений встречи | `POST /management/v2/meetings/{meeting_id}/proposals` |
| Создание предложений сообщения | `POST /management/v2/messages/{message_id}/proposals` |
| Manager CAS подтверждение предложения | `POST /management/v2/proposals/{entity_type}/{entity_id}/confirm` |
| Durable digest | `POST /management/v2/digests` |

Последние четыре endpoint и публикация `deadline_policy` добавлены основным потоком в контрактном
коммите `1fe97926ef060bfee0e0339f4177502a98df037e`. Этот frontend-коммит не cherry-pick'ит и не меняет
backend, но уже совместим с указанным контрактом.

## Fail-closed правила

- low confidence или `review_state=needs_review` блокирует завершающее действие;
- отсутствие evidence pins блокирует подтверждающие/исполняющие действия;
- HTTP 409 не повторяет mutation: показывается требование обновить запись;
- mutation всегда передаёт `expected_version` выбранной записи;
- неизвестный server shape не отображается как достоверный факт;
- UI не утверждает отправку внешнего сообщения: digest DTO допускает только
  `external_actions_created=false`;
- отсутствие HTTP-контракта не замещается локальными mock-данными.

## Integration interface request

Для подключения в основной экран интегратору нужно:

1. Импортировать CSS и компоненты из `frontend/src/modules/management/index.ts`.
2. Передать `projectId` в `useManagementCenter(projectId, ready)` и маппить выбранный attention item
   на obligation либо risk/decision panel. `App.tsx` намеренно не менялся этим потоком.
3. После 409 не повторять команду автоматически: вызвать `reload()`, показать новую версию и запросить
   новое решение пользователя.
4. Использовать `deadline_policy`, который опубликован коммитом `1fe9792`; при старом backend
   передавать `configurationAvailable=false`, не подставляя локальные значения.
5. Для предложений передавать в `proposeMeetingActions()` только структурированные candidates с
   immutable evidence pins. Ответ POST хранить в состоянии родительского экрана и передавать в
   `MeetingProposalPanel`; подтверждение вызывает `confirmMeetingProposal()` и всегда использует
   `expected_version`.
6. Для digest передавать явные timezone, quiet start/end, channel и local date в `enqueueDigest()`.
   Панель показывает реальный `job_id/status`, затем фактически созданную сводку из notifications.
7. Не передавать в эти ответы raw протокол встречи, письмо, документ, токены, DSN или provider payload.

Оставшийся transport gap: backend не имеет GET списка уже созданных meeting proposals, постоянного
GET/PUT для предпочтений digest и отдельного read endpoint результата job. После перезагрузки UI
должен восстанавливать только факты из attention/obligations/notifications и не реконструировать
предложения или quiet-hours самостоятельно.

## Проверки

- целевые runtime/parser/RTL тесты: `36 passed`;
- проверены loading/error/empty, low-confidence, evidence, CAS conflict и недоступный API;
- полный frontend test: `144 passed`;
- TypeScript check: PASS;
- production build: PASS (`1623 modules transformed`);
- generated `react_dist` после build восстановлен и не входит в коммит;
- backend, миграции, `App.tsx`, shared global CSS и production не изменялись.

## Известные ограничения

- модуль не подключён к навигации и не является browser E2E основного приложения;
- POST transport digest/proposals готов в `1fe9792`, но постоянный read transport их настроек и
  списка предложений отсутствует;
- component contract не доказывает PostgreSQL concurrency, но никогда не скрывает 409;
- отображение evidence использует opaque pin label; полный fragment viewer остаётся отдельным модулем.
