# Final CI runtime review

Дата: 2026-09-03. Ветка `codex/final-ci-runtime-review`, чистая отдельная
worktree `pu-workspace-final-ci-runtime-review`, база
`62b939db82167c51e3fd1b9959c9e904d0d3cede`.

**Решение: FAIL для обязательной durable runtime-приёмки исходного SHA;
CONDITIONAL для исправленного harness до повторного CI.** Дефект production
очереди по этому запуску не установлен. Сбой самой сборки пока не устранён:
недостаточно сохранённой диагностики для установления его первопричины.

## Изоляция

На старте целевая ветка/worktree отсутствовала, создана от точного SHA.
AGENTS.md в репозитории и применимых родительских каталогах не найдены.
Прочитаны parallel-validation-final.md и queue-recovery-validation.md.
Основная worktree сохраняет HEAD `83774aac726acd4e27b349e9194f30783158bde8`,
ветку codex/commercial-p2-yandex360 и семь исходных изменённых файлов:
backend/app/api/auth.py, local_upload.py, workspace.py; backend/app/schema.py;
backend/app/static/app.js; docker-compose.yml; frontend/index.html.
Пользовательские изменения не переносились. Frontend, его workflow, product
backend и production не изменялись. VPS/production .env/credential manager
не использовались. Доступ к CI — штатный GitHub connector, GET logs/artifacts.
Push/PR/dispatch/merge/deploy не выполнялись.

## Фактические результаты трёх запусков

Все три завершились, ожидание и дублирующий запуск не понадобились.
Artifact metadata и checkout logs подтверждают один head_sha:
`62b939db82167c51e3fd1b9959c9e904d0d3cede`, ветка codex/parallel-validation-final.

| Run | Job | Результат и границы |
|---|---|---|
| [33748739619](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33748739619) | 100627156864, test-and-build | SUCCESS: 477 backend tests, 44 frontend tests, check/build |
| [33748739642](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33748739642) | 100627156635, smoke | SUCCESS: чистая БД, миграции, readiness, authenticated API smoke, cleanup |
| [33748739817](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33748739817) | 100627157217, recovery | FAILURE: первая docker build, до старта PostgreSQL/worker topology |

Backend CI: журнал `11:16:28.9521836Z` показывает upgrade
c83d0a24b512 → **f360a1b2c3d4**; PostgreSQL 16.15.
`11:16:59.3820924Z`: **477 passed, 3 warnings, 28.45 s**, без skip.
PU_TEST_POSTGRES=1 и тестовая pu_workspace_test включены. Это включает реальный
PostgreSQL schema test, но остальные тесты с собственными SQLite fixtures
не становятся PostgreSQL concurrency tests автоматически.
Frontend artifact: **8 файлов / 44 passed, 7.88 s**, tsc и Vite build успешны,
1616 modules, JS 441.15 kB / gzip 128.26 kB. Браузерной E2E здесь нет.

Docker smoke: реальные steps миграции/readiness/API smoke success. В workflow
проверяется ready=true, checks.schema.ok=true, message=f360a1b2c3d4, иначе
команда завершается ошибкой. Auth smoke проверяет bootstrap/login, cookies/CSRF,
organization/project и logout внутри тестовой сети. Это in_process backend,
не два API/worker topology. CI_FAULT_MODE=false: два bootstrap-conflict fault
шага **skipped по режиму запуска**, они не считаются проверенными.

## Artifacts: содержимое, целостность, безопасность

Скачаны ZIP через connector, прочитаны все entry без распаковки в репозиторий.
SHA-256 ZIP совпадают с GitHub digest:

| Artifact ID | Содержимое | SHA-256 |
|---|---|---|
| 9890702797 | pytest.log, frontend-check.log, frontend-test.log, frontend-build.log | 796b8f3578d176b69ef4cfbefe997eaf3b9bc0ac1c1fd011cadca025c7127621 |
| 9890684158 | cleanup-verification.json | 2b29177677d6d4ecdc8c1ae0b6a68a203c234b0d40a527fe6800d9d5015e2348 |
| 9890673313 | protocol.json | 70a9ed81b9ae11eaaf2f994a736c5795b5ab82c1f1d4fe08e8a69817ef5ae21b |

Verification logs содержат результаты/имена тестов, пути runner и предупреждения,
не письма/документы. Regex credential markers (GitHub tokens/private key/Bearer)
не найдены; дополнительно прочитано всё текстовое содержимое. Два JSON содержат
синтетические project names, команды, временный путь env (НЕ его содержимое),
числа/статусы, без credentials и документов. Никаких SQL backups/cookies/raw
provider logs в этих ZIP нет. Это проверка этих трёх artifacts, не универсальная
аттестация production logs или гарантия обнаружения любого произвольного секрета.
Первый HTTP-клиент получил 403/1010; обычный requests с User-Agent загрузил
те же connector URLs. Секреты для доступа не извлекались и не сохранялись.

## Durable: точная граница выполнения

Runtime step начался `2026-09-03T11:16:34.827Z`, завершился exit 1
`11:16:36.0758625Z`. protocol.json result=FAIL:

1. Начальный inventory по label puw-queue-33748739817-1: три команды exit 0.
2. git rev-parse и Compose config --quiet: exit 0 (config 0.28 s).
3. git ls-files backend: exit 0.
4. `docker build -t puw-queue-33748739817-1:base -`: **exit 1, 0.55 s**.
5. logs --no-color: exit 0; diagnostics.secret_free=true, raw_published=false.
6. down --volumes --remove-orphans --timeout 10: exit 0 (0.09 s).
7. docker ps -aq, network ls -q, volume ls -q с точным label: exit 0,
   затем cleanup=PASS. В коде это событие добавляется только после проверки
   отсутствия всех трёх типов ресурсов. failure_type=RuntimeError.

Первопричина docker build **неизвестна**: command() отбросил stdout/stderr,
а workflow вывел только exit code. Из 0.55 s нельзя заключать registry rate
limit, сломанный Dockerfile или продуктовый дефект. Это подтверждённый сбой
build-stage и подтверждённый пробел наблюдаемости harness, не доказанная ошибка
queue. Исправление продукта наугад не вносилось.

## Матрица runtime-доказательств

| Требование | Что реально доказано |
|---|---|
| Чистая PostgreSQL + upgrade head | PASS в backend CI / Docker smoke, не durable |
| Два API, два workers, scheduler | НЕ ВЫПОЛНЕНО |
| Concurrent claim, crash, lease expiry, другой owner | НЕ ВЫПОЛНЕНО |
| Stale-owner rejection, idempotency HTTP | НЕ ВЫПОЛНЕНО в runtime |
| Retry/backoff/failed/dead-letter/redrive/cancel | НЕ ВЫПОЛНЕНО в runtime |
| Restart API/Compose, сохранение jobs | НЕ ВЫПОЛНЕНО |
| Heartbeat/metrics | НЕ ВЫПОЛНЕНО для worker topology |
| Queue backup/restore/sequence | НЕ ВЫПОЛНЕНО, dump не создавался |
| Manual/canonical recovery и safe-copy probes | НЕ ВЫПОЛНЕНО |
| Durable cleanup после раннего build failure | PASS: down + три inventory assertions |
| Docker smoke cleanup после реального окружения | PASS: counts containers=0, networks=0, volumes=0, temporary_files_removed=true |

У durable нет фактических job_id/owner/lease/state transitions: задания не
создавались. Время/команды выше относятся к build/cleanup, не fault recovery.
GitHub always cleanup success в durable — fallback no-op, поскольку основной
finally уже удалил state file. Доказательство cleanup берётся из protocol,
а не только из зелёного fallback step.
Docker smoke log `11:16:54.0305860Z`: CI_CLEANUP_CONFIRMED, artifact повторяет
нулевые counts проекта **puw-ci-33748739642-1**.

## Минимальное исправление

Добавлена allowlist-диагностика failed subprocess в run.py:
category (dockerfile_missing, registry_rate_limit, registry_access_denied,
daemon_unavailable, buildx_unavailable, network_failure, disk_full,
permission_denied, unclassified), размеры stdout/stderr, raw_published=false.
Публикации raw/tail исключений, stdout или stderr нет. Категория — подсказка по
известной сигнатуре, не самостоятельное доказательство причины.
Если ошибка не совпадает с сигнатурами, останется unclassified; потребуется
отдельное безопасное расширение диагностики, не раскрытие сырых логов.

Regression test_run.py воспроизводит четыре failed-build сценария через mock
subprocess и реальное main()/finally harness. До fix: **4 failed**, KeyError
failure. После fix: проходят классификация, отсутствие synthetic-secret и
DOCUMENT_BODY в events, обязательный down/volume cleanup и удаление state.
Новый файл включён в regression step durable workflow. Старые assertions,
topology, timeout, permissions и trigger не ослаблены. Skip не добавлен.

Доступные локальные проверки после patch:

```text
DATABASE_URL=sqlite+pysqlite:///:memory: PYTHONPATH=backend
pytest backend/tests/test_durable_jobs.py backend/tests/test_worker_topology.py
       backend/tests/test_job_hardening_contract.py scripts/ci/durable_queue
       scripts/ci/tests -q -p no:cacheprovider
99 passed in 3.91s
actionlint 1.7.12 .github/workflows/durable-queue.yml: exit 0
git diff --check: PASS
```

Это локальные SQLite/mock/contract tests, не повтор CI и не Docker runtime.
Изменений БД/миграций, backend business logic, frontend нет.

## Передача и необходимое разрешение

Один исправляющий commit содержит run.py, test_run.py, durable-queue.yml и этот
отчёт. Полный SHA выдаётся в итоговом ответе. Исправлена наблюдаемость, но не
заявляется устранение неизвестной причины первой сборки.

Для повторного CI нужно отдельное разрешение **отправить новый итоговый SHA
в remote ветку codex/parallel-validation-final** (без force, после проверки
fast-forward). Push-trigger этой ветки уже существует. Предыдущее разрешение
на SHA 62b939d не используется. Если другой исполнитель продвинул remote,
интегратор переносит этот commit, затем согласует получившийся точный SHA.
Push review-ветки сам по себе durable workflow не запускает: её нет в trigger.

После нового run проверить category, установить build root cause, затем пройти
весь fault/backup/restore protocol. До этого durable gate остаётся незакрытым.
Exactly-once Gmail/Drive не заявляется даже при будущем синтетическом AuditLog
PASS. Права, mailbox identity, connection version guard, legacy bindings и
внешние эффекты сохраняют ограничения исходного отчёта.
