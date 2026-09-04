# PU Workspace v5.4 — DB-backed authority (внутренний отчёт)

Дата: 2026-09-03

Ветка: `codex/v54-authority`

BASE_SHA: `4db9d51496e25d7916ecc75a5dfdf61a930c8637`

Статус: реализовано только для явно внедряемого synthetic CONFIRM-пилота; production и AUTO не включены.

## Краткий аудит

До изменения `ActionApproval.authority_epoch` уже сохранялся, а Trust facade повторно
проверял epoch в T2. Но источником epoch и разрешений был неизменяемый Python-объект
`SyntheticPolicy`. Он не переживал restart как authority, не был связан с
`ProjectMember` и не мог линеаризовать отзыв роли против dispatch.

Переиспользовано:

- `User`, `Project`, `ProjectMember` и существующие integer PK;
- `ActionApproval.authority_epoch`, T1/T2 и `PendingDispatch`;
- единый `BackgroundJob`, receipt и audit ledger;
- существующий `append_audit` (новый writer не создан);
- существующий порядок с блокировкой Project до action/message/Task.

Добавлено минимально:

- `v54_authority_states`: один текущий явный mandate на
  tenant/project/principal/scope;
- immutable DTO `AuthoritySnapshot`, монотонные `authority_epoch` и
  `record_version`, TTL, state и точный список разрешений;
- `AuthorityResolver` с Project → AuthorityState row-lock, CAS изменения,
  проверкой текущего `ProjectMember.role` и fail-closed поведением;
- audit-событие `AUTHORITY_CHANGED` без role, permissions, source content и
  секретов;
- подключение resolver к synthetic composition, Source/Evidence resolution и
  live approval validation.

Ни одна authority-строка не создаётся миграцией. Реальной политике и production
defaults значения не назначались.

## Threat model и результат

| Угроза | Контроль | Результат |
|---|---|---|
| Роль отозвана до T2 | live DB check; approval epoch повторно сверяется; revoked/unknown deny | Покрыто regression |
| Роль меняется одновременно с dispatch | общий lock order Project → AuthorityState; change и T2 держат lock до caller commit | PostgreSQL-тест подготовлен, runtime CONDITIONAL |
| Approval выдан при старом epoch | grant хранит epoch approver; `_grant` разрешает approver заново и сравнивает epoch | Покрыто integrated regression |
| Другой tenant/project | составной scope и tenant/project guards; нет active-project fallback | Покрыто |
| Actor подменён payload | `RequestScope` только user; worker scope строится из sealed DB `requested_by`; payload имеет закрытую схему | Покрыто |
| Service actor пытается approve | human-only операции запрещены service principal независимо от permissions | Покрыто |
| Global admin получает неявное право | `User.is_admin` не участвует; нужна явная tenant/project authority row + membership | Покрыто |
| Два dispatch | существующие command key, pending binding, single receipt и replay сохраняются | Покрыто integrated regression |
| Ошибка audit/rollback | authority change, membership, epoch и audit принадлежат caller transaction; T2 уже атомарен | Покрыто |
| Restart | authority state и epoch читаются из БД новой Session | Покрыто |
| Unknown ACL/role/TTL/state/operation | единая content-free ошибка `resource_unavailable` | Покрыто |

Worker не становится approver: он только предъявляет lease binding существующего
BackgroundJob, а T2 работает с requester из sealed revision и отдельно повторно
проверяет человеческого approver. Глобальный admin не получает AUTO и не обходит
tenant mandate. CONFIRM, отдельные context confirmation, claim review и action
approval сохранены.

## Модель и миграция

Новая таблица: `v54_authority_states`.

Ключевой уникальный scope:

`(organization_id, project_id, principal_kind, principal_id, scope)`.

Поля authority:

- `membership_role`, `permissions`, `state`;
- `authority_epoch`, `record_version`;
- `valid_until`, `updated_at`, `updated_by_user_id`.

Миграция: `a54f001c0a02_v54_db_authority.py`

Down revision: `a54f001c0a01`

Новая единственная Alembic head: `a54f001c0a02`.

Миграция additive, не содержит seed/backfill и не меняет исторические миграции.
Downgrade отказывается удалять непустую authority-таблицу. Readiness/CI schema
expectations обновлены до новой head.

## Transaction contract

- Session и transaction принадлежат caller.
- Resolver/change не вызывают commit, rollback, close, enqueue или provider I/O.
- Все проверки authority идут под Project → AuthorityState locks.
- T2 удерживает Project guard до Task/receipt/audit commit или rollback.
- `change` использует expected epoch, увеличивает epoch ровно на 1 и обновляет
  `ProjectMember.role` в той же транзакции.
- Прямое legacy-изменение membership обнаруживается сравнением роли и приводит к
  deny на следующей проверке; для строгой конкурентной гарантии rollout обязан
  перевести pilot membership writers на `change`.

## Проверки

Выполнено локально с явным `DATABASE_URL=sqlite+pysqlite:///:memory:`:

- authority + pilot integration + schema: **30 passed, 2 skipped**;
- foundation/schema/offline PostgreSQL SQL: **89 passed, 1 skipped**;
- полный backend suite: **749 passed, 9 skipped**, 4 Alembic deprecation warnings;
- `git diff --check`: PASS (только предупреждения Git о локальной CRLF-конверсии).

PostgreSQL runtime: **CONDITIONAL, не выполнен**. В среде отсутствуют
`PUW_V54_AUTHORITY_DATABASE_URL`, `PUW_V54_AUTHORITY_MIGRATION_DATABASE_URL`,
`PUW_V54_TEST_DATABASE_URL`, `PUW_V54_INTEGRATION_DATABASE_URL`; команда Docker
недоступна. Подготовлены два opt-in теста:

1. реальная гонка revoke/change против dispatch check на row locks;
2. Alembic upgrade → head, проверка таблицы/head, downgrade последней миграции и
   повторный upgrade на отдельной пустой PostgreSQL БД.

SQLite результаты не считаются доказательством concurrency.

## Interface requests интегратору

1. До реального rollout перевести все writer-операции membership/role пилота на
   `AuthorityResolver.change` либо эквивалентный writer с тем же Project-first
   lock и epoch invalidation. Legacy API в этом потоке намеренно не менялся.
2. При создании composition передавать `AuthorityResolver`; без него
   `roles_known=False`, пилот не исполняется. Production loader не добавлен.
3. Audit/экспортерам разрешить новое безопасное событие
   `v54.AUTHORITY_CHANGED`; details остаётся `NULL`.
4. Перед интеграцией выполнить оба PostgreSQL-теста на отдельных БД с именем
   `puw_v54_test_*`, затем обычный upgrade/readiness smoke.
5. Если в будущем появится отдельный typed service RequestScope, сохранить запрет
   human-only операций. Текущий RequestScope намеренно допускает только user.

## Оставшиеся blockers

- PostgreSQL lock/migration runtime не доказан в текущем окружении.
- Нет production policy assignment, tenant mandate UI/API или backfill — это
  сознательная граница задачи, а не разрешение угадывать роли.
- Прямые legacy writers membership не участвуют в authority lock protocol;
  production cutover запрещён до их маршрутизации через единый writer.
- AUTO, внешнее выполнение, Gmail/storage/OCR/staging и production feature flags
  не изменялись и не включались.

## Изменённые файлы

- `.github/workflows/docker-smoke.yml`
- `backend/app/core/v54_authority.py`
- `backend/app/core/v54_interfaces.py`
- `backend/app/core/v54_permissions.py`
- `backend/app/models/__init__.py`
- `backend/app/models/v54_authority.py`
- `backend/app/pilot_composition.py`
- `backend/app/schema.py`
- `backend/app/source_evidence/facade.py`
- `backend/migrations/versions/a54f001c0a02_v54_db_authority.py`
- `backend/tests/test_v54_authority.py`
- `backend/tests/test_v54_authority_postgres.py`
- `backend/tests/test_v54_pilot_foundation.py`
- `backend/tests/test_v54_pilot_integration.py`
- `scripts/ci/durable_queue/run.py`
