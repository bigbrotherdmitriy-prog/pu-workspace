# Docker Compose smoke CI: интеграция

Дата: 2026-09-03. Решение: **CONDITIONAL**.

Ветка: `codex/ci-smoke-integration`.
База: `83774aac726acd4e27b349e9194f30783158bde8`.
Изменения собраны в отдельной чистой worktree; итоговый коммит включает
исправление версии схемы и три параллельных результата.

## Входящие коммиты

| Часть | SHA |
|---|---|
| Версия схемы / regression | `ab9f5db357fa45c9759a8ff276a36ba478a443d7` |
| Compose | `666da57f5e466ad10cae72732751b6bd6533f806` |
| Workflow | `cf4a53144e0cb0c4d0d89669c85be18eb9791a42` |
| API smoke / инструкция | `e104a666889ba63b7816a594a0bbb484fc77693e` |

Все применены без текстовых конфликтов. Исходные ветки сохранены.

## Содержательные исправления интеграции

1. Workflow экспортировал только пароль smoke. Shell environment мог
   перекрыть `--env-file` для пароля БД, ключей и release SHA. Теперь все
   сгенерированные тестовые значения экспортируются через `GITHUB_ENV`.
2. Диагностика содержала только счётчики. Добавлен `compose.sanitized.log`
   с упорядоченными разрешёнными событиями и шагами/HTTP-кодами smoke.
   Произвольный текст логов не публикуется. Artifact включает JSON и журнал.
3. Инструкция приведена к фактическому Compose: отдельные миграции,
   `/health` в container healthcheck, отдельная проверка `ready=true`.

Пункты 1 и 2 сначала воспроизведены тестами: **2 failed, 1 passed** до
исправления workflow. Затем тесты прошли без ослабления assertions.
Дополнительный продуктовый код при интеграции не менялся.

## Файлы поставки

- `.github/workflows/docker-smoke.yml`
- `docker-compose.ci.yml`
- `scripts/ci/smoke_api.py`
- `scripts/ci/tests/test_smoke_api.py`
- `scripts/ci/tests/test_smoke_workflow.py`
- `docs/ci/docker-compose-smoke.md`
- `docs/audits/ci-smoke-integration-result.md`
- `backend/app/schema.py` — из входящего исправления
- `backend/tests/test_schema_revision.py` — из входящего исправления
- `backend/tests/test_outgoing_email_completion.py` — из входящего исправления

## Выполненные проверки

| Проверка | Результат |
|---|---|
| `python -m pytest scripts/ci/tests -q -p no:cacheprovider` | **54 passed**, 2.40 s |
| `python -m pytest tests -q -p no:cacheprovider` из backend | **381 passed, 1 skipped**, 8.23 s |
| `python -m alembic heads` из backend | Единственная head `f360a1b2c3d4` |
| Workflow YAML, все Bash run-блоки (`bash -n`), встроенный Python | PASS, включены в тесты workflow |
| Генератор тестового env и защита от унаследованных значений | PASS, исполнение Python-блока workflow |
| Диагностика с синтетическими секретами/ошибками | PASS, маркеры отсутствуют в обоих artifacts |
| Cleanup shell при exit 0 и exit 9 команды down | PASS с mock Docker; проверены точные аргументы, удаление временных файлов и сохранение ошибки |
| Smoke через stdin вне репозитория | PASS, входит в тесты smoke |
| `git diff --check`, `git diff --cached --check` | PASS |

Backend запускался локально с `DATABASE_URL=sqlite+pysqlite:///:memory:`,
`PYTHONPATH` на backend этой worktree, `PU_BACKGROUND_EXECUTION=in_process`,
`GMAIL_AUTO_SYNC_ENABLED=false`, `AI_SECRETARY_AUTOMATION_ENABLED=false`.
Единственный skip — существующий PostgreSQL integration fixture:
`PU_TEST_POSTGRES` не включён, чистая PostgreSQL недоступна. Новых skip нет.
Два предупреждения Alembic касаются отсутствующего `path_separator` в
существующей конфигурации и не подавлялись.

Frontend не изменён; повторный frontend test/build не выполнялся.

## Изоляция и остающиеся проверки

Compose содержит только `db` и `backend`, внутреннюю сеть и собственный
volume. Нет host ports, `container_name`, external ресурсов, mounts исходной
worktree и production env-файла. Project name содержит run ID и attempt.
Явный `--env-file` применяется во всех Compose-командах.
Cleanup имеет `if: always()` и использует тот же project/file/env-file.

Docker, PostgreSQL CLI и actionlint не найдены в локальном окружении.
Не выполнены:

- реальный `docker compose config --quiet`;
- сборка и запуск контейнеров;
- `alembic upgrade head` на чистой PostgreSQL;
- реальный health/readiness и авторизованный API smoke;
- фактическое удаление контейнеров/network/volume после успеха и сбоя;
- запуск GitHub Actions и доставка artifact;
- actionlint.

Mock cleanup подтверждает shell-логику, но не поведение Docker daemon.
Сокращённый журнал намеренно исключает неизвестные строки, поэтому может
не содержать полной причины нестандартного сбоя сборки.

Точные Bash-команды генерации env, запуска, миграций, smoke и cleanup:
[инструкция](../ci/docker-compose-smoke.md).
Для проверки аварийной ветви на чистом тестовом runner после первого
успешного smoke повторить smoke на той же тестовой БД: bootstrap должен
вернуть 409; job должен завершиться ошибкой, сохранить очищенную диагностику
и выполнить cleanup. Не применять сценарий к существующим пользовательским БД.

Статус может стать PASS только после реального контейнерного прогона и
подтверждения cleanup. Этот двухсервисный `in_process` smoke не проверяет
durable-очередь, workers, scheduler, OCR и внешние интеграции.

## Сохранность исходной работы

Работа велась только в `pu-workspace-ci-smoke-integration`.
Основная `pu-workspace-commercial-p2-yandex360` осталась на `83774aac…`
с исходными пользовательскими изменениями auth.py, local_upload.py,
workspace.py, schema.py, static/app.js, docker-compose.yml и frontend/index.html.
Production, production secrets и базы не использовались.
Merge в основную ветку, push и deploy не выполнялись.
