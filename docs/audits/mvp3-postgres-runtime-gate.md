# MVP3 PostgreSQL runtime gate

Дата: 2026-09-05

Ветка: `codex/mvp3-postgres-runtime-gate`

База: `855a30df54ed7477c8180ad1c5ed920584f94656`

## Решение

Исполняемый gate для MVP3 подготовлен на существующих PostgreSQL и
Chromium workflows. Новая очередь, Compose-стек или дублирующий
workflow не создавались.

Итоговый статус: **CONDITIONAL**. Локально нет Docker, PostgreSQL не
слушает `127.0.0.1:5432` и `127.0.0.1:5433`. Поэтому PostgreSQL
race/restart/lease recovery не засчитываются как PASS.

## Аудит до изменений

- единственная Alembic head уже `a54f001c0a17`;
- `.github/workflows/v54-pilot-runtime.yml` уже даёт изолированную
  PostgreSQL 16 service, одноразовые secrets и безопасный JSON
  protocol;
- `scripts/ci/v54_pilot_workflow.py` уже создаёт/удаляет только
  точно именованные тестовые БД и не публикует raw output;
- `test_mvp3_management_acceptance_postgres.py` уже доказывает
  одного победителя optimistic CAS, но не был подключён к CI;
- полный Playwright suite уже имеет два stale-project сценария,
  но workflow не был включён для этой ветки;
- после более поздней интеграции provider control center общая
  deny-by-default browser fixture не отвечала на безопасный
  `GET /provider-actions`, из-за чего все шесть management E2E
  воспроизводимо падали до исправления fixture.

## Реализованный gate

1. Текущий v5.4 orchestrator создаёт отдельную
   `puw_mvp3_test_runtime`, передаёт только её URL через
   `PUW_MVP3_TEST_DATABASE_URL` и безусловно удаляет БД.
2. Новая фаза `postgres_mvp3_runtime` запускает:
   - две PostgreSQL-транзакции на одну версию Obligation,
     где ровно одна побеждает;
   - гонку двух digest scheduler sessions и одно durable job;
   - закрытие/повторное создание engine как границу restart;
   - истечшую lease, recovery, claim вторым worker и запрет
     `succeed` для stale owner;
   - replay handler с ровно одним `Notification`;
   - нуль `ProviderAction` и payload только из идентификаторов,
     даты и версии preference.
3. Существующий Chromium workflow запускает полный E2E на
   этой ветке. Он проверяет initial/mutation stale responses,
   viewer/manager, low-confidence, CAS conflict, digest settings и отсутствие
   неподтверждённых provider writes.
4. Browser fixture разрешает только empty status-read
   `GET /provider-actions`; любой неописанный write по-прежнему
   блокируется.

## Проверки

| Проверка | Результат |
| --- | --- |
| MVP3 + CI contract | `95 passed, 2 PostgreSQL skipped` |
| Точечный runtime/contract | `17 passed, 2 PostgreSQL skipped` |
| Chromium management | `6 passed` |
| Полный Chromium suite | `26 passed` |
| Frontend Vitest | `197 passed` |
| Frontend TypeScript app/E2E | PASS |
| Frontend synthetic production build | PASS |
| Python compile | PASS |
| Alembic heads | `a54f001c0a17` — ровно одна |
| `git diff --check` | PASS |
| Docker/PostgreSQL runtime | NOT RUN / CONDITIONAL |

Полный backend на исходной базе не зелёный:
`1347 passed, 21 skipped, 15 failed`. Все 15 падений изолировано
воспроизводятся в `backend/tests/test_mvp4_supply_acts.py` и не
вызваны этим gate: у теста устарел точный набор routes,
а supply fixtures не совмещены с новым exact Evidence validation.
Это отдельный blocker интеграционной базы MVP4; в рамках MVP3
не исправлялся.

## Обязательный внешний запуск

После отдельного разрешения на push этой ветки GitHub запустит
оба уже существующих workflow:

```powershell
git push -u origin codex/mvp3-postgres-runtime-gate
```

Без push ручной запуск возможен после публикации branch:

```powershell
gh workflow run v54-pilot-runtime.yml --ref codex/mvp3-postgres-runtime-gate
gh workflow run storage-picker-e2e.yml --ref codex/mvp3-postgres-runtime-gate
```

До PASS нужно:

1. получить `postgres_mvp3_runtime=PASS` на чистой БД;
2. получить зелёный Chromium job;
3. интегрировать свежую базу, где исправлен независимый
   MVP4 supply regression, или получить его отдельный минимальный fix.

Production, реальные провайдеры, данные, secrets, push, merge и deploy
не затрагивались.
