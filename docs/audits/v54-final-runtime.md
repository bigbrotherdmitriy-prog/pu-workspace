# PU Workspace v5.4 — final runtime acceptance

Дата проверки: 2026-09-03  
Ветка: `codex/v54-final-runtime`  
База: `4db9d51496e25d7916ecc75a5dfdf61a930c8637`  
Статус: **CONDITIONAL**

## Итог

Изолированный PostgreSQL workflow подготовлен, но фактический PostgreSQL/runtime прогон не выполнен: локальный Docker daemon не ответил за 10 секунд, а разрешения на push/workflow dispatch нет. Статические проверки, полный SQLite regression-suite, корпус и тесты orchestration-контракта прошли. Это не подменяет PostgreSQL, multi-process или Docker Compose runtime.

## Переносы

- `0dd9fd5b36ac73dad825e90453f9e87bcb283b2d` перенесён отдельным документационным cherry-pick как `ed14ab90d03f7c9ccef6444992f353f482f04232`.
- `536c043d3a24b42fcb6f78ecf45b255dbc54b35a` перенесён отдельным документационным cherry-pick как `bc70d484385d4d8ffabd105f8c87837316d94596`. Макет не подключался к `App.tsx`.
- `531bd25a918248f97f20fd04bbb5eac25688935f` целиком не переносился. Вручную перенесены только gzip-режим build context (`w:gz`), его regression-тест и безопасные счётчики диагностики. Старое ожидание `f360a1b2c3d4` не перенесено.

Жёстко заданные revision в `.github`, `scripts` и `backend/app/schema.py` проверены: актуальная единственная head — `a54f001c0a01`. `f360a1b2c3d4` встречается только как исторический predecessor миграции/проверка истории.

## Реализованный runtime-контракт

`.github/workflows/v54-pilot-runtime.yml`:

- запускается только для push ветки `codex/v54-final-runtime` и вручную;
- имеет `contents: read`, checkout с `persist-credentials: false`;
- использует job container и PostgreSQL 16 service без host ports;
- создаёт только тестовые application secrets;
- проверяет единственную Alembic head и upgrade до `a54f001c0a01`;
- запускает полный backend, PostgreSQL A/B/C/integration, корпус и process-fault probe;
- публикует только allowlisted `v54-runtime-artifacts/protocol.json`;
- в `finally` удаляет только три заранее именованные свежие тестовые БД и отказывается работать при их наличии до запуска.

`scripts/ci/v54_pilot_runtime.py` проверяет двумя отдельными процессами: конкурентный claim, блокировку второго владельца, принудительное завершение первого тестового процесса, lease recovery, отказ stale owner, одну Task, один receipt, один Context projection и один success audit. Интеграционный suite дополнительно проверяет transactional rollback.

## Независимый corpus

Структурный валидатор: `PASS`, 28 cases, 14 assets, 52 excerpts, 31 negative checks.

Неизменяемые ожидания минимально исполняются для `P02`, `P06`, `S06`, `S09`. Expected gaps, не скрытые skip:

- `C01`: extraction/due-date input корпуса не подключены к synthetic fixture;
- `C07`: time-of-day claim не поддержан;
- `S02`: legacy global Message identity cutover не завершён;
- `S07`: kill точно между T1 и enqueue не воспроизведён;
- `S08`: kill внутри T2 до commit не воспроизведён, но rollback транзакции проверяется;
- `S10`: только fake external contract;
- `P04`: finance вне пилота;
- AUTO остаётся запрещён.

Ожидаемые результаты корпуса не изменялись под реализацию.

## Фактические проверки

| Проверка | Результат |
|---|---|
| Полный backend (`pytest backend/tests`) | PASS — 744 passed, 7 skipped, 4 warnings |
| CONFIRM/corpus/durable/workflow regression | PASS — 37 passed |
| Corpus validator `--self-test` | PASS |
| Integration documentation validator | PASS — 37 records, 2 actions, 4 mutation checks, 68 local links, 8 legacy hashes |
| UX state validator | PASS — 18/18 |
| Alembic heads | PASS — одна head `a54f001c0a01` |
| actionlint | PASS |
| `docker compose -f docker-compose.queue-ci.yml config` | PASS, только статическая проверка |
| Python compile runtime scripts | PASS |
| `git diff --check` | PASS |
| PostgreSQL A/B/C/integration | NOT RUN |
| Process crash/lease/stale owner | NOT RUN |
| Durable Compose: 2 API/2 workers/scheduler | NOT RUN |
| Durable retry/dead-letter/redrive/cancel | NOT RUN |
| Durable backup/restore/cleanup | NOT RUN |

Пропуски полного backend: 1 общий PostgreSQL schema test, 4 context concurrency tests, 1 foundation PostgreSQL test и 1 source/evidence PostgreSQL test. Они требуют явно выделенную PostgreSQL БД и не засчитаны как PASS.

## Docker и ограничения

Docker CLI установлен, но daemon не ответил на read-only `docker version` за 10 секунд; зависший диагностический процесс остановлен. Docker Desktop/WSL не перезапускались. На момент проверки свободно примерно 1.48 GiB RAM и 30.62 GiB диска. Такой запас памяти недостаточно безопасен для одновременной локальной сборки, PostgreSQL и multi-process harness. Поэтому локальные Docker-ресурсы не создавались.

Gzip regression подтверждает формат build-context на уровне unit-теста. Он ещё не доказывает успешную Buildx/Compose сборку. Новый v5.4 workflow не является Compose-проверкой durable topology; это отдельный PostgreSQL service runtime.

## Безопасность протокола

Subprocess stdout/stderr удерживаются в памяти. Протокол содержит только статусы, длительности, размеры вывода и счётчики. DSN, документы, письма и raw stderr не публикуются. Перед записью выполняется проверка отсутствия значений четырёх тестовых секретов. Artifact path указывает на один JSON-файл, а не каталог.

## Команда следующего шага

После отдельного разрешения:

```powershell
git push -u origin codex/v54-final-runtime
gh workflow run v54-pilot-runtime.yml --ref codex/v54-final-runtime
```

Для закрытия `PASS` нужны зелёный runtime artifact с `result=PASS`, `cleanup=PASS`, head `a54f001c0a01`, успешные PostgreSQL phases и отдельный успешный durable queue workflow после gzip. До этого итог остаётся **CONDITIONAL**.
