# Parallel validation: release candidate

Дата: 2026-09-03. Решение: **CONDITIONAL**.
Локальные unit/component/contract проверки — PASS. Browser E2E и реальный
PostgreSQL fault runtime — НЕ ВЫПОЛНЕНЫ. Это не production release/deploy.

## Состав и изоляция

Worktree: `pu-workspace-parallel-validation-final`.
Ветка: `codex/parallel-validation-final`.
Точная backend-база: `f6654e617a5b6f8d5b2ece10fdeff40606729e4f`.
Она уже включает queue, storage, Gmail и backend-интеграционные исправления.
Эти потоки повторно НЕ переносились.

Единственный cherry-pick:
`7fac0d7b3558de9803e12384e94d80fb54add8a3` →
`d11a1bfbc06446a442979bdac2ea808a1fafd8a1`.
Следующий отдельный commit содержит финальные корректировки и этот отчёт;
полный итоговый SHA выдаётся в ответе (`git rev-parse HEAD`). Конфликтов нет.

На старте целевые ветка/каталог отсутствовали; созданы от точной базы.
Применимых AGENTS.md в репозитории/родительских каталогах не найдено.
Изучены отчёты backend integration, storage picker UI, queue recovery,
storage binding и Gmail project validation, соответствующие handlers и тесты.

Основная worktree осталась на `codex/commercial-p2-yandex360`,
`83774aac726acd4e27b349e9194f30783158bde8`. Её исходные dirty-файлы не переносились:
backend/app/api/auth.py, local_upload.py, workspace.py; backend/app/schema.py;
backend/app/static/app.js; docker-compose.yml; frontend/index.html.
Production .env, credentials, документы и реальные интеграции не читались.
Push, PR, workflow dispatch, merge и deploy не выполнялись.

## Найденные несовпадения и исправления

1. Первый полный backend pytest после UI cherry-pick: **2 failed, 474 passed,
   1 skipped**. Два статических frontend contract теста требовали старый код:
   принудительный `openSources("root")` и `load(targetProjectId)` после выбора.
   Проверки содержательно обновлены: восстановление выбранной папки, encoded
   locator, pinned project и отбрасывание позднего ответа. Остальные assertions
   сохранены; нет skip/ослабления backend readiness.
2. Реальный `/standardize` возвращает `already_queued/status`, а App проверял
   `already_analyzed` и сообщал о новом запуске. Два новых App-теста с ready и
   retrying воспроизвели дефект; UI теперь показывает фактический статус и
   не объявляет queued/retrying завершением или новым выполнением.
3. Тест active-job 409 у анализа сначала failed: показывался технический
   английский текст. Добавлено понятное сообщение ожидания для активной очереди
   и отдельное сообщение для остальных конфликтов. Используется и retry-build.
   Автоматических повторов нет. Три новые App regression до fix: **3 failed,
   3 passed**; после fix зелёные. Дополнительно проверены retry-build 409 и
   `already_queued` анализа: ответ не трактуется как completed.
4. Durable workflow имел только pull_request/workflow_dispatch. Новый contract
   test сначала failed (нет push); добавлен push только для
   `codex/parallel-validation-final`, без path filter. Read-only permissions,
   временные secrets, timeout и always cleanup сохранены. Нет deploy step.

Backend product code, OCR evidence/confidence/manual review, cooperative
cancellation и Gmail routing/access не изменены этим финальным commit.
Их существующие regression-тесты включены в полный backend-набор.

## Проверка пользовательского контракта

Mock responses сверены с реальными discovery/snapshot-queue/snapshots/analyze/
standardize handlers, не только с TypeScript типами. Несовпадение standardize
выше обнаружено именно этой сверкой.

| Сценарий | Выполненное доказательство |
|---|---|
| Новый проект рядом с Persistent Project, отсутствие fallback | App + project selection tests |
| Вложенные Google/Яндекс папки, сохранение project/provider/connection/folder | Backend synthetic HTTP и picker hook tests |
| Кириллица, пробелы, #, ?, %, вложенные пути | encodeURIComponent/URLSearchParams hook tests, backend locator tests |
| Discovery в обратном порядке, смена проекта до ответа/confirm | Hook tests с deferred promises |
| Поздний confirm не возвращает старый проект | Настоящий App в JSDOM, cache только исходного проекта |
| Reload/выбранная папка | Remount/sessionStorage и backend discovery saved root; не browser reload |
| 409 discovery/confirmation | Контекст сбрасывается, требуется переоткрытие, без auto retry |
| 409 retry-build/analyze | App показывает ожидание, один запрос, без ложного success |
| already_queued и ready confirmation | Не обещает пересканирование; реальный status, не completed по флагу |
| Анализ/прогресс исходного проекта | Project guards, stale response tests; нет выдуманных 5/10% |

Это раздельные backend/mock UI проверки, не единый живой browser → API →
PostgreSQL → worker прогон. Некоторые общие действия legacy App за пределами
picker (например, позднее уведомление retry-build) не имеют универсального
request-epoch fencing; вся программа не объявляется свободной от гонок.

## Фактические проверки

Python: существующий `.venv-pu-workspace-tests`; `PYTHONPATH=backend`,
`DATABASE_URL=sqlite+pysqlite:///:memory:`. Storage fixtures используют временные
SQLite. Чистая PostgreSQL для тестов не была подключена.

| Команда | Результат |
|---|---|
| `pytest backend/tests scripts/ci/tests scripts/ci/durable_queue/test_contract.py -q -rs --tb=short -p no:cacheprovider` | **551 passed, 1 skipped**, 118.12 s (476 backend + 75 smoke/harness) |
| `pnpm install --offline --frozen-lockfile` | PASS, 162 пакета из cache; без скачивания/изменения lockfile |
| `pnpm run check` | PASS |
| `pnpm run test` | **8 файлов, 44 passed**, 7.44 s |
| `pnpm run build --outDir ../../tmp/parallel-validation-final-build-verified` | PASS, 1616 модулей, JS 441.15 kB / gzip 128.26 kB |
| actionlint 1.7.12 `.github/workflows/durable-queue.yml` | PASS, exit 0 |
| `docker compose --env-file NUL -p puw-queue-config-check -f docker-compose.queue-ci.yml config --quiet` | PASS, exit 0, stderr пуст; новые synthetic env только процесса |
| `git diff --check` | PASS |
| из backend: `alembic -c alembic.ini heads` | одна head **f360a1b2c3d4** |
| CURRENT_SCHEMA_REVISION | **f360a1b2c3d4**, совпадает |

Один существующий skip: integration/test_postgres_schema.py требует
PU_TEST_POSTGRES=1. Не скрыт, не выдаётся за PASS. Два существующих warning:
Alembic path_separator отсутствует. Миграции/константа не менялись.
Первый Vitest запуск заблокирован sandbox/esbuild; разрешённый повтор прошёл.
Build направлен вне tracked react_dist, production bundle не перезаписывался.
Локальные build outputs находятся в соседнем tmp, не входят в commit.

## Docker, browser и решение о runtime

Read-only диагностика через полный путь Docker CLI, timeout 10 s на команду:

- Docker CLI **29.7.2**, Compose **v5.5.0**, доступны.
- `docker info --format '{{.ServerVersion}}'`: **TIMEOUT 10 s**.
- Свободная physical RAM при проверке **479236 KiB (~468 MiB)** из
  16492280 KiB; диск C: **33117433856 bytes (~30.84 GiB)** свободно.
- Docker/WSL не перезапускались, чужие процессы/контейнеры не останавливались.
  Повторяющихся попыток подключения к daemon нет, VPS не использовался.
- Browser tool: `failed to write kernel assets`, `os error 3`.
  Browser E2E недоступна. **JSDOM/Vitest не заменяют браузер.**

Топология требует db 512 MiB + пять процессов по лимиту 768 MiB и ресурсы
сборки. Контейнерные лимиты не ограничивают build. При недоступном daemon и
таком запасе RAM локальный runtime небезопасен; запуск не предпринят.

## Подготовленный runtime и непроверенное

Используются существующие docker-compose.queue-ci.yml и run.py. Никаких
production .env, host ports, external volumes/networks или bind mounts.
Project `puw-queue-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`, отдельный pgdata,
internal network, БД puw_queue_test, новые runtime secrets. Build context
из git-tracked backend/scripts, без локальных незакоммиченных secrets.

Добавлен workspace_checks.py перед стартом workers/scheduler. Отказывается
работать не на PostgreSQL/db/puw_queue_test. Реальные workspace/queue методы,
синтетические entities, resolver внешнего storage запрещён mock-ом:

- manual retry против queued/running/retrying и reuse recovery job;
- manual analysis key против canonical recovery key;
- сбой enqueue safe-copy → rollback session → восстановление;
- замена analysis_result → повторный вызов без второй session/job.

Эти проверки подготовлены, AST/contract проверены, **не исполнены на PostgreSQL**.
Workspace probes пока последовательные; реальную одновременную manual/recovery
гонку двух соединений нужно дополнительно подтвердить. Существующий
postgres_checks.py проверяет конкурентный claim двумя процессами.

После отдельного разрешения CI должен выполнить:
чистый upgrade, два API/two workers/scheduler, claim/crash/lease/recovery/stale
owner, Idempotency-Key, retry/backoff/failed/dead-letter/redrive/cancel,
restart API/Compose, persisted jobs, heartbeat/metrics и безопасные logs.
Backup/restore в отдельную puw_queue_restore_test сравнивает строки queue и
heartbeat, sequence; это не backup всей бизнес-БД.

Fault job_id/состояний/времени сейчас **нет** — runtime не запускался.
В будущем run.py сохраняет их в queue-artifacts/protocol.json. Сырые logs,
backup, cookies и secrets не публикуются. Cleanup в finally и workflow always:
down --volumes --remove-orphans только точного project, затем label inventory
containers/networks/volumes. Ошибка daemon не считается пустым inventory.
На этом этапе Docker-ресурсы не создавались: фактического teardown/backup/
restore PASS нет. Локальные config tests не доказывают runtime cleanup.

## Не закрытые ограничения

- Независимый выбор provider/account до изменения root отсутствует.
- Server-side connection version guard отсутствует, connection_id=null и
  reauth внутри той же credential row не полностью защищены.
- Job ID на другом устройстве не восстанавливается через snapshots API.
- Точного промежуточного процента нет там, где backend его не выдаёт.
- Legacy snapshots без binding, длина locator 255, ancestry 100 остаются.
- Gmail mailbox identity/global dedup, pagination (25 за 7 дней), SENT/archive,
  thread/RFC correlation, OAuth после ручного переноса требуют отдельного решения.
- At-least-once не равно exactly-once: synthetic AuditLog не доказывает
  идемпотентность Gmail send, Drive copy/rename и частичных бизнес-эффектов.
- Queue fencing защищает queue rows, но не отменяет уже начатый внешний вызов;
  legacy organizer/standardize конкуренция и все logs глобально не аттестованы.

## Следующий шаг и перенос

Только поверх backend-базы f6654e6:

```sh
git cherry-pick d11a1bfbc06446a442979bdac2ea808a1fafd8a1
git cherry-pick <полный итоговый SHA из ответа>
```

После отдельного разрешения пользователя:
`git push origin codex/parallel-validation-final` запустит branch-scoped durable
workflow без merge в default branch. Затем проверить фактический SHA run,
protocol.json и cleanup; ошибки исправлять, не переименовывать в PASS.
До этого общий статус **CONDITIONAL**, не готовность production.
