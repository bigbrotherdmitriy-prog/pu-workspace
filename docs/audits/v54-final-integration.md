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
