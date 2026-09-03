# PU Workspace v5.4 — final integration result

Дата: 2026-09-03

Ветка: `codex/v54-final-integration`

База: `4db9d51496e25d7916ecc75a5dfdf61a930c8637`
Решение: **CONDITIONAL**

## Итог

Runtime/corpus/UX, DB-backed Authority, mailbox cutover inventory и encrypted
staging assessment объединены в отдельной чистой worktree. Authority остаётся
CONFIRM-only и synthetic-only; AUTO, внешнее исполнение, production policy и
production feature flags не включались. Старый staging fork не переносился.

Функциональные ожидания schema/readiness/runtime обновлены до единственной
Alembic head `a54f001c0a02`. Ссылки на `a54f001c0a01` сохранены только там, где
это предыдущая миграция или цель безопасного downgrade Authority.

## История переноса

Коммиты перенесены строго в таком порядке:

1. `ed14ab90d03f7c9ccef6444992f353f482f04232` → `914ba4d` — corpus;
2. `bc70d484385d4d8ffabd105f8c87837316d94596` → `90fb551` — UX mock/spec;
3. `4a1abfdf708fe6534a8e51cf63ad125c0b1cc492` → `51ae2b2` — runtime workflow;
4. `a1773113c48645528db2da846fbf68a46db7f96d` → `740b059` — DB Authority;
5. `5416224f6f1be45dcff7cffa7dcb8ec0b2768e45` → `c6c13d6` — mailbox inventory;
6. `016968d7539d8bd65614565e9c55d5de03906878` → `856a4f3` — staging assessment.

Все шесть исходных коммитов имеют merge-base `4db9d514...`, и база является их
предком. Исходные A/B/C, исходные corpus/UX и старый staging implementation
повторно не переносились.

## Разрешение пересечений

Текстовых конфликтов cherry-pick не возникло. Git автоматически объединил три
общих файла Runtime и Authority; результат проверен содержательно:

- `backend/tests/test_v54_pilot_foundation.py` сохраняет runtime-проверки и
  ожидает новую head;
- `backend/tests/test_v54_pilot_integration.py` сохраняет интеграционный сценарий
  и использует DB-backed Authority fixtures;
- `scripts/ci/durable_queue/run.py` сохраняет gzip build context и безопасную
  диагностику, а migration check ожидает `a54f001c0a02`.

Дополнительно обновлены:

- branch scope `.github/workflows/v54-pilot-runtime.yml` на
  `codex/v54-final-integration`;
- runtime orchestrator и его regression-тест на `a54f001c0a02`;
- read-only mailbox inventory и его документация на `a54f001c0a02`.

## Проверки

| Проверка | Фактический результат |
|---|---|
| Target Source/Context/Trust/Authority/integration/CI/inventory | PASS — 303 passed, 1 PostgreSQL skip |
| Полный backend | PASS — 753 passed, 9 PostgreSQL skips, 4 warnings |
| Durable queue contract harness | PASS — 10 passed |
| Acceptance corpus validator | PASS — 28 cases, 31 negative checks |
| Исполняемый corpus subset | PASS в составе target suite |
| UX state tests | PASS — 18/18 |
| Frontend unit tests | PASS — 44/44 |
| Frontend TypeScript check | PASS |
| Frontend production build | PASS |
| Integration documentation validator | PASS — 37 records, 2 actions, 4 mutation checks, 77 local links, 8 legacy hashes |
| Alembic heads | PASS — одна head `a54f001c0a02` |
| `CURRENT_SCHEMA_REVISION` | PASS — `a54f001c0a02` |
| actionlint для всех workflow | PASS |
| Python compile runtime/inventory scripts | PASS |
| `git diff --check` | PASS |
| Secret filename/pattern scan новых файлов | PASS — 0 matches |
| Docker Compose config в текущем окружении | NOT RUN — Docker CLI недоступен в shell |
| PostgreSQL migration/concurrency/process fault | NOT RUN |
| Durable Compose 2 API/2 workers/scheduler | NOT RUN |

Frontend после базы не изменялся (`git diff ... -- frontend` возвращает 0),
поэтому test/check/build выполнены на идентичном frontend-дереве с уже
установленными зависимостями. Созданные build-артефакты восстановлены; исходная
worktree `pu-workspace-v54-pilot-integration` осталась чистой.

PostgreSQL skips относятся к schema integration, Authority migration/locking,
Context concurrency, foundation и Source/Evidence concurrency. SQLite и offline
SQL не считаются доказательством PostgreSQL-конкурентности.

## Безопасность и границы

- Gzip build context сохранён: `tarfile.open(..., mode="w:gz")`.
- Workflow публикует только allowlisted JSON protocol и не публикует raw output.
- Scan не нашёл private keys, GitHub/Google/Telegram/AWS tokens и чувствительные
  имена файлов в добавленном наборе.
- Job payload с содержимым документов или писем не добавлялся.
- Mailbox inventory остаётся read-only, PII-free и production-refusing.
- Staging assessment остаётся документацией; несовместимый staging-код и его
  вторая Alembic head не переносились.

## Непроверенные сценарии и блокеры

До решения PASS обязательны:

1. Alembic upgrade до `a54f001c0a02` на чистой PostgreSQL;
2. Authority revoke/change против T2 под реальными row locks;
3. Source/Context CAS concurrency;
4. process crash, lease recovery и stale-owner rejection;
5. единственность Task/receipt/audit/Context projection;
6. durable Compose topology, retry/dead-letter/redrive/cancel и backup/restore;
7. успешная Buildx-сборка с gzip-контекстом.

Отдельные продуктовые блокеры после runtime: `MBX-CUTOVER-01`, единый legacy
membership writer для Authority и новая policy-gated реализация encrypted
staging/no-copy. Они не должны исправляться внутри runtime-проверки.

## Следующий шаг

После отдельного разрешения:

```powershell
git push -u origin codex/v54-final-integration
gh workflow run v54-pilot-runtime.yml --ref codex/v54-final-integration
```

Push ветки также соответствует branch trigger workflow. Перед выдачей PASS нужно
проверить safe artifact и отдельно выполнить durable queue workflow.

На момент локальной интеграции production, основная dirty worktree, merge, PR и
deploy не затрагивались; публикация ветки описана ниже.

## Первый GitHub runtime — 33787031282

После публикации `006543310eeefb7a205103a0eb029f8cdb61fe65` выполнен
изолированный GitHub Actions run `33787031282`.

- migration: PASS, `a54f001c0a02`;
- backend full: PASS, 753 passed / 9 skipped;
- `postgres_abc_integration`: FAIL после 273 passed;
- cleanup: PASS;
- raw output не опубликован;
- artifact SHA-256:
  `fa1c1a652b472554288fbda16bd7fd2e48c0672ec3cb2d2a5c7bae40bb81ddb8`.

Протокол v1 безопасно указывает фазу, но не сохраняет pytest node ID, поэтому
точный упавший сценарий из artifact определить невозможно. Добавлена
регрессионная проверка и allowlisted диагностика: при ошибке протокол сохраняет
не более 20 node ID только из `backend/` или `scripts/`, без параметров теста,
assertion text, stdout, stderr, DSN, документов и секретов. Повторный runtime
обязателен; статус остаётся **CONDITIONAL**.

## Второй GitHub runtime — 33792231596

Повторный run для `f77e40f712c0b80c100e8b1d613d603527dfdf46` подтвердил:

- migration: PASS, `a54f001c0a02`;
- backend full: PASS, 753 passed / 9 skipped;
- `postgres_abc_integration`: FAIL после 273 passed;
- cleanup: PASS;
- raw output не опубликован;
- artifact SHA-256:
  `b7ff1db0d9a229fa158db8031607c9b57cbf270f2da6a3d6086044bd14769b9e`.

Поле `failed_nodeids` отсутствовало, потому что PostgreSQL-фаза запускала
pytest с `-rs`: этот report flag выводит только skipped и исключает безопасные
строки `FAILED`/`ERROR`, которые читает allowlist-парсер. Добавлен regression-
тест и минимальная замена на `-rfsE`. Она раскрывает только node ID теста;
traceback, assertion text, stdout, stderr, DSN и данные по-прежнему не
публикуются. Локальная проверка `scripts/ci`: 87 passed. До следующего runtime
решение остаётся **CONDITIONAL**.

## Четвёртый GitHub runtime — 33796098612

Safe artifact для `78c90e3efd2faade6812177108efc04cc53c1f30` локализовал
единственный сбой:

- node ID: `backend/tests/test_v54_pilot_foundation.py::test_postgresql_upgrade_downgrade_only_on_explicit_empty_test_db`;
- locations: `backend/tests/test_v54_pilot_foundation.py:452` и
  `backend/tests/v54_pilot_fixture.py:54`;
- artifact SHA-256:
  `755ec1e0bc54cfdf6b2793a486109cab43c26624d0a6289ef367b7a6c591aaf3`;
- cleanup: PASS, raw output не опубликован.

Сбой происходил при первом `seed()`, до проверки downgrade. Историческая
миграция `a17c4d820e31` создаёт bootstrap-организацию с `id=1`, а синтетический
fixture пытался вставить тот же первичный ключ. Regression сначала воспроизвёл
`UNIQUE organizations.id`. Fixture теперь повторно использует существующую
bootstrap-организацию, не изменяя её данные; на чистом metadata-сценарии он
по-прежнему создаёт синтетическую организацию. Production-код и применённые
миграции не изменялись.

После исправления: foundation `90 passed, 1 skipped`, полный backend
`754 passed, 9 skipped`, `scripts/ci` — `87 passed`, `git diff --check` — PASS.
PostgreSQL runtime требует повторного GitHub run; статус пока **CONDITIONAL**.

## Пятый GitHub runtime — 33798041223

Runtime для `96928ad8db72d1434a4a76d89d861a9ddd8a6772` подтвердил исправление
bootstrap-конфликта и прошёл все тестовые фазы до process-fault probe:

- migration: PASS, `a54f001c0a02`;
- backend: PASS, 754 passed / 9 skipped;
- PostgreSQL A/B/C/integration: PASS, 275 passed;
- corpus: PASS;
- durable gzip regression: PASS, 10 passed;
- `postgres_process_fault`: FAIL за 4.45 секунды;
- cleanup: PASS, raw output не опубликован;
- artifact SHA-256:
  `e2f811e858d8d84b79723866a9f7d606f0845d4aceeea30bb0fc07792cf641ee`.

Process-fault probe до изменения сообщал только allowlisted тип
`AssertionError`, поэтому точный fault checkpoint определить невозможно.
Добавлен закрытый список безопасных checkpoint-кодов и их перенос в protocol
только из валидного JSON failure record. Exception text, payload, DSN, stdout и
stderr не публикуются. Regression сначала упал из-за отсутствующего checkpoint;
после изменения `scripts/ci` — 88 passed, отдельный safe-failure contract —
PASS. Статус до следующего runtime остаётся **CONDITIONAL**.

## Шестой GitHub runtime — 33799138740

Run для `09789f4f14f7910008996fb779b73534b5fc3336` снова прошёл migration,
backend, PostgreSQL A/B/C, corpus и durable regression, после чего process-fault
probe завершился `AssertionError`. Artifact SHA-256:
`ecce575b164dca57d88ef716677cf85c4d97bb27d22faf60b9fad54f8771620a`.

Checkpoint ошибочно показывал `cleanup`: безусловный вызов `checkpoint` в
`finally` перезаписывал стадию исходного сбоя даже при успешной очистке. Новый
regression сначала воспроизвёл потерю checkpoint. Cleanup вынесен в helper,
который ставит `cleanup` только при собственной ошибке и сохраняет исходную
стадию во всех остальных случаях. После исправления `scripts/ci` — 89 passed,
safe-failure contract — PASS. Runtime остаётся **CONDITIONAL** до следующего
run с достоверным checkpoint.

## Точка остановки — GitHub runtime 33801015730

Run №7 для `114e01f10a6a9e761b6433f1b21d893925412f2c` завершён и artifact
проверен локально:

- migration: PASS;
- backend: PASS, 754 passed / 9 skipped;
- PostgreSQL A/B/C/integration: PASS, 275 passed;
- corpus: PASS;
- durable gzip regression: PASS, 10 passed;
- process-fault: FAIL, `AssertionError`, достоверный checkpoint `cleanup`;
- общий cleanup тестовых БД: PASS;
- artifact SHA-256:
  `2a89c569b3a494a9fe1693960e2cd2289a7fc11df72815b05ba0a8813702538a`.

Это означает, что ошибка возникает внутри `cleanup_probe`: либо при завершении/
join созданного дочернего процесса, либо в `fixture.close()` при удалении
изолированной schema. Основные PostgreSQL-функции до process-fault probe
подтверждены. Первая задача следующей сессии: добавить regression с отдельными
allowlisted checkpoint `cleanup_child` и `cleanup_fixture`, затем минимально
исправить обнаруженный путь и повторить workflow. До этого решение остаётся
**CONDITIONAL**. Merge и production deploy не выполнять.

## Локализация process-fault после run 33801015730

Локальная проверка установила точную последовательность сбоя:

1. `SyntheticPolicy` передавался в дочерний процесс вместе с DB-backed
   `AuthorityResolver`;
2. resolver содержал тестовые `lambda`-часы и не сериализовался механизмом
   `multiprocessing` с методом `spawn`;
3. `Process.start()` завершался до присвоения PID;
4. объект процесса уже находился в списке cleanup;
5. `join()` незапущенного процесса выбрасывал `AssertionError` и маскировал
   исходную ошибку сериализации checkpoint-значением `cleanup`.

Regression-тесты сначала воспроизвели оба дефекта. Минимальное исправление:

- перед `spawn` из policy удаляется только runtime-объект resolver; все
  неизменяемые policy-факты сохраняются;
- дочерний процесс восстанавливает DB-backed `AuthorityResolver` и проверяет
  полномочия по той же изолированной PostgreSQL schema;
- процесс добавляется в cleanup только после успешного `start()`;
- cleanup безопасно пропускает объект без PID, чтобы не маскировать исходный
  failure.

Проверки после исправления:

- сериализация фактического policy из synthetic composition: PASS,
  1346 bytes;
- `scripts/ci`: 91 passed;
- целевой v5.4 regression: 261 passed / 1 PostgreSQL skip;
- полный backend: 754 passed / 9 skipped;
- `git diff --check`: PASS.

Production-код, миграции и продуктовые данные не менялись. Исправление ещё не
проверено в GitHub PostgreSQL runtime, поэтому общий статус остаётся
**CONDITIONAL** до отдельного разрешённого push и повторного workflow.
