# PostgreSQL BackgroundJob: recovery validation

Дата: 2026-09-03. Решение: **CONDITIONAL**.
Точная база: `814ff77b79bd3a6d1382c345783946a7b9b7898e`.
Ветка: `codex/queue-recovery-validation`.
Отдельная worktree: `pu-workspace-queue-recovery-validation`.

## Аудит до изменений

База найдена локально; новая ветка и worktree созданы от неё, без reset.
Применимых AGENTS.md не найдено. Основная worktree на `83774aac…` содержала
изменения auth.py, local_upload.py, workspace.py, schema.py, static/app.js,
docker-compose.yml, frontend/index.html; они не переносились и не менялись.
Изучены background-job-hardening-result.md, commercial-p0-p1-integration-result.md,
ci-smoke-integration-result.md, jobs queue/worker/scheduler/handlers, модель и API,
тесты durable jobs/topology/hardening, существующие smoke и backup/restore.

Существующая очередь уже имеет PostgreSQL SKIP LOCKED, unique idempotency key,
lease recovery, retry/backoff, dead-letter, admin cancel/retry/redrive,
heartbeat и graceful shutdown. Второй очереди не требуется.
Выявлено:

- heartbeat/progress/succeed/fail проверяли статус и owner, но не истечение lease;
  старый worker мог продлить уже истёкшую аренду или завершить задание до recovery;
- safe_error сохранял произвольный текст ValueError/TimeoutError/ConnectionError
  и строковых ошибок; regex не защищал содержимое документов и неразмеченные секреты;
- cooperative progress не имел привязки к текущему исполнителю;
- заданный PU_WORKER_ID повторялся после рестарта процесса;
- admin API имеет операторские операции, но не универсальное создание задания;
- существующие тесты преимущественно SQLite/проверка исходников, а fault-протокол
  hardening был описанием, не исполняемым доказательством.

## Минимальные исправления

1. Все четыре owner-операции требуют живой lease. SQLAlchemy UPDATE использует
   synchronize_session=fetch (иначе SQLite evaluate сравнивает naive/aware datetime).
2. safe_error сохраняет только тип исключения, строковые ошибки — JobError.
3. Worker задаёт execution context для cooperative progress; прежний worker не
   обновляет прогресс нового владельца. Вызовы вне worker-context сохраняют
   прежний контракт для совместимости. Это не универсальный capability token.
4. Worker ID включает новый случайный идентификатор процесса даже при PU_WORKER_ID.
   Heartbeat-thread фиксирует свой job/event в аргументах, не захватывает меняющуюся
   переменную следующей итерации.

API бизнес-функций, handlers.py, OCR, staging, frontend, migrations, production
Compose, существующие smoke Compose/workflow и backup/restore scripts не изменены.
Scheduler также не изменён. Его log.exception остаётся отдельным риском при
ошибках вне синтетического сценария; данный проход не заявляет глобальный аудит логов.

## Regression и выполненные проверки

| Команда / проверка | Фактический результат |
|---|---|
| pytest tests/test_durable_jobs.py до fix | **5 failed, 6 passed**: четыре expired-owner mutation и утечка текста ошибки |
| Целевые durable/topology/hardening + harness tests после fix | **23 passed**, 0.74 s |
| Полный backend после первого fix | 386 passed, 1 failed, 1 skipped; обнаружена совместимость legacy OCR progress |
| pytest backend/tests scripts/ci/durable_queue/test_contract.py -q -p no:cacheprovider после корректировки | **390 passed, 1 skipped**, 7.22 s |
| pytest scripts/ci/tests scripts/ci/durable_queue/test_contract.py -q -p no:cacheprovider | **73 passed**, 2.84 s; существующий smoke не сломан |
| actionlint 1.7.12 .github/workflows/durable-queue.yml | Exit 0, замечаний нет |
| Реальный Docker Compose v5.5.0 config --quiet для docker-compose.queue-ci.yml | Exit 0, stderr пуст; новые случайные значения только в процессе, --env-file NUL |
| Docker CLI --version | Docker 29.7.2 |
| Docker info с timeout 15 s | Не ответил; daemon недоступен для runtime |

Backend tests использовали `DATABASE_URL=sqlite+pysqlite:///:memory:` и
`PYTHONPATH=backend`; один skip — существующий PostgreSQL integration gate.
Два предупреждения Alembic path_separator сохранены. Это не PostgreSQL runtime.
actionlint взят из уже имеющегося локального tmp/puw-actionlint-1.7.12;
workflow также запускает actionlint, а не только YAML parsing.
На production VPS не подключались. Локальные Docker/WSL не перезапускались.

## Запускаемый runtime harness

Добавлены самостоятельные `docker-compose.queue-ci.yml` и
`.github/workflows/durable-queue.yml`; запуск на чистом Linux GitHub runner:

```sh
python scripts/ci/durable_queue/run.py
```

Нужны Docker Engine/Compose, Python 3, чистый committed checkout. Workflow
устанавливает test dependencies и actionlint, выполняет regression, протокол,
unconditional cleanup и публикует только `queue-artifacts/*.json`.
Этот проход workflow не запускал: push/merge/deploy не выполнялись.

Топология: PostgreSQL 16 + api1/api2 + worker1/worker2 + scheduler.
Runtime image наследует сборку существующего backend/Dockerfile. Build contexts
составляются только из git-tracked backend/scripts, без .git и локального .env.
Тестовый fixture не включается в production image. Создание задания через HTTP
предоставляет только fixture API `/ci/jobs`, с настоящими auth/admin/CSRF и
существующим enqueue. Production API не расширен. Handler fixture подменяет
только dispatcher в тестовом worker entrypoint; worker.main и очередь реальные.
Fixture отклоняет дополнительные payload-поля и принимает только bounded
hold/failures/permanent/max_attempts/delay, не документы и provider credentials.

Project `puw-queue-<run_id>-<attempt>` сначала проверяется на отсутствие
контейнеров/network/volumes по Compose labels. Внутренняя сеть, отдельный pgdata,
нет host ports, production env, внешних ресурсов или bind mounts.
Новые app/bootstrap/Fernet/DB/smoke secrets, временный env с mode 0600,
очищенное окружение; PU_RELEASE_REVISION берётся из фактического HEAD.
Runtime memory limits: db 512 MiB, каждый API/worker/scheduler 768 MiB.
Они не ограничивают Docker build. Harness предназначен для отдельного runner,
не для малоресурсного production VPS из соседней задачи.

## Fault-протокол: запланированное исполнение

Ниже перечислены assertions harness, **не результаты реального запуска**.
Фактических runtime job_id пока нет. После запуска protocol.json содержит
команды, exit codes, время, job_id, owner, attempts, lease, progress, error type
и итоговые состояния. Сырые ответы/логи/cookies/secrets не публикуются.

Все команды используют один префикс:
`docker compose --project-name <unique> --file docker-compose.queue-ci.yml --env-file <temporary>`.

| Этап | Команда / доказательство |
|---|---|
| Миграции | run --rm --no-deps api1 alembic -c alembic.ini upgrade head; SELECT alembic_version = f360a1b2c3d4 |
| Конкурентный claim | run api1 python /queue_ci/postgres_checks.py: два spawned процесса стартуют одновременно, ровно один claim, attempts=1 |
| Fencing | В PostgreSQL probe просроченный lease, запрет heartbeat/succeed до recovery, новый owner, запрет старого progress/fail |
| Полная топология | Два реальных worker.main и scheduler, heartbeat до API preflight, readiness=true обоих API |
| Базовый HTTP | Переиспользуется существующий scripts/ci/smoke_api.py через stdin, auth/cookies/CSRF/organization/project/logout |
| Idempotency | POST /ci/jobs в api1, повтор ключа в api2, тот же job_id |
| Progress/heartbeat | Первый job hold=90, effect=1, progress>=25, наблюдение продления lease до kill |
| Авария worker | kill -s SIGKILL только сервиса-владельца в уникальном project |
| Recovery | Ожидание реального lease expiry 60 s, второй worker завершает, attempts=2, другой owner |
| Stale owner | heartbeat/progress/succeed/fail старого owner отклонены через реальные queue функции |
| Side effect | Синтетический AuditLog effect сохраняется ДО kill, после recovery count=1 |
| Permissions | cancel/retry/redrive без auth → 401; синтетический authenticated non-admin → 403 |
| Restart API | restart api1, затем api1/api2: delayed job остаётся queued |
| Restart Compose | down без --volumes, запуск той же топологии: delayed job сохранён |
| Cancel | cancel delayed job, attempts остаётся 0 |
| Retry/backoff | TimeoutError → retrying, available_at в будущем, далее completed |
| Operator retry | ValueError → failed, admin retry → completed, attempts=2 |
| Dead-letter/redrive | Две неудачи → dead_letter; redrive → снова две попытки, effects=1 |
| Метрики | /admin/jobs/metrics, workers>=2 после возврата убитого worker |
| Безопасность | Секреты запуска и два sentinel текста исключений отсутствуют в backup SQL и контейнерных логах |

## Backup/restore-протокол

После fault-сценариев останавливаются только тестовые workers/scheduler.
Существующие scripts/backup-job-queue.sh и restore-job-queue.sh копируются
в тестовый db-контейнер с PostgreSQL tools. Production БД не используется.

```sh
# Внутри тестового db-контейнера; libpq URI без пароля, local socket.
DATABASE_URL='postgresql:///puw_queue_test?user=puw_ci' \
  sh /tmp/backup-job-queue.sh /tmp/queue.dump
pg_restore -f - /tmp/queue.dump  # stdout захватывается, не публикуется
# Новая отдельная БД на том же изолированном PostgreSQL instance:
psql -U puw_ci -d puw_queue_test -c 'CREATE DATABASE puw_queue_restore_test'
DATABASE_URL='postgresql:///puw_queue_restore_test?user=puw_ci' \
  PU_CONFIRM_QUEUE_RESTORE=RESTORE_QUEUE_TABLES \
  sh /tmp/restore-job-queue.sh /tmp/queue.dump
```

Сравниваются полные упорядоченные JSON rows background_jobs и service_heartbeats,
а не только counts. Проверяется sequence новым INSERT с id больше прежнего max.
Backup остаётся в тестовом volume и не загружается в artifact. После удаления
volume исчезает. Restore queue-only: это НЕ восстановление всей приложения/БД.
AuditLog синтетического эффекта не входит в queue backup, поэтому повторный
запуск handlers после такого изолированного restore не доказывает дедупликацию
реальных бизнес-эффектов. Restore-протокол проверяет данные очереди и sequence.

## Диагностика и cleanup

В finally до down считываются тестовые container logs для проверки sentinel/secrets;
в artifact только безопасный булев результат и структурированный protocol.
Cleanup: `down --volumes --remove-orphans --timeout 10`, затем три label-проверки
отсутствия контейнеров, networks и volumes. Ошибки daemon не считаются пустым списком.
При неудачном teardown env сохраняется для повторной очистки, не публикуется.
Workflow `if: always()` вызывает cleanup.py, который проверяет точное совпадение
project с run ID/attempt и расположение временного env перед удалением ресурсов.
Не удаляются образы/build cache глобальным prune. На ephemeral runner они уходят
вместе с runner; при локальном использовании остаются и требуют отдельного решения.

## Гарантии, ограничения и изменения за границами потока

- Модель доставки **at-least-once**, не exactly-once. Lease/fencing защищают записи
  самой очереди, но не могут отменить уже начавшийся внешний HTTP side effect.
- Синтетический DB side effect защищён row lock/live owner и проверкой AuditLog
  в той же транзакции. Это образец теста, не реализация idempotency для Gmail/Drive.
- Необходим отдельный аудит idempotency/fencing бизнес-эффектов Gmail/storage
  handlers за пределами этого потока; их код не менялся.
- Generic enqueue/payload/result не имеют универсального запрета document bytes
  или secrets. Проверяется синтетический corpus; production data compliance не доказан.
- Cooperative progress вне worker execution context сохраняет legacy-поведение;
  потоки, самостоятельно создаваемые handler, не наследуют ContextVar автоматически.
- Service heartbeat отражает окно 90 s: недавно погибший процесс может временно
  учитываться. Runtime harness дополнительно проверяет фактическую смену owner.
- Универсального production enqueue endpoint нет; тестовый API не должен быть
  опубликован или перенесён в основной образ.
- Изменений схемы/миграций не требуется. Если дальнейшая защита внешних эффектов
  потребует execution-token/outbox, модель/миграция согласуется с интегратором отдельно.
- Реальные PostgreSQL fault/backup/restore, runtime job_id/времена/логи,
  доставка GitHub artifact и действие always() ещё **не проверены**.

**Итог: CONDITIONAL.** Unit/regression/actionlint/Compose config прошли;
локальный daemon не ответил. Подготовлен запускаемый изолированный workflow,
но runtime PASS не заявляется. Push, merge, deploy и обращения к VPS отсутствуют.

## Изменённые файлы

- backend/app/jobs/queue.py
- backend/app/jobs/worker.py
- backend/tests/test_durable_jobs.py
- docker-compose.queue-ci.yml
- .github/workflows/durable-queue.yml
- scripts/ci/durable_queue/Dockerfile
- scripts/ci/durable_queue/fixture.py
- scripts/ci/durable_queue/client.py
- scripts/ci/durable_queue/postgres_checks.py
- scripts/ci/durable_queue/run.py
- scripts/ci/durable_queue/cleanup.py
- scripts/ci/durable_queue/test_contract.py
- docs/audits/queue-recovery-validation.md
