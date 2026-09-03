# Изолированный API smoke

Контракт проверен по `ab9f5db357fa45c9759a8ff276a36ba478a443d7`.
Скрипт `scripts/ci/smoke_api.py` автономен: Python 3.11+ (образ использует
3.12), стандартная библиотека и `httpx` из backend-зависимостей. Он передаётся
через stdin, не импортирует Core и не читает соседние файлы или `.env`.

Compose и workflow включены в интегрированный набор. Команды ниже
предназначены для Bash на Linux CI runner после интеграции standalone
`docker-compose.ci.yml`. Они не используют production Compose.

## Контракт Compose для интегратора

- Ровно `db` и `backend`, без host ports, `container_name`, внешних volumes
  и сетей. Собственная сеть проекта, желательно `internal: true`.
- `db`: PostgreSQL 16, отдельные `puw_ci` database/user, пароль из тестового
  `POSTGRES_PASSWORD`, собственный именованный volume, healthcheck через
  `pg_isready -U puw_ci -d puw_ci`.
- `backend`: build из `./backend`, рабочая директория `/app`, порт 8000
  внутри сети; `DATABASE_URL` указывает только на сервис `db` данного project.
- Передать в backend `APP_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`,
  `BOOTSTRAP_TOKEN`, `PU_RELEASE_REVISION`. Не передавать credentials провайдеров.
- `PU_BACKGROUND_EXECUTION=in_process`, `GMAIL_AUTO_SYNC_ENABLED=false`,
  `AI_SECRETARY_AUTOMATION_ENABLED=false`. Не добавлять scheduler/worker/relay.
- Миграции выполняются отдельным одноразовым контейнером до запуска API.
  Команда backend: `python -m scripts.preflight && exec uvicorn
  app.main:app --host 0.0.0.0 --port 8000`. Preflight обязателен.
- Backend healthcheck проверяет `/health`, HTTP 200 и `status=healthy`.
  Workflow и smoke отдельно проверяют `/api/readiness` и **JSON `ready=true`**:
  этот endpoint возвращает 200 даже при неготовности.

`in_process` smoke проверяет API, auth, CSRF и операции с БД. Он **не
проверяет durable-очередь**, workers, scheduler, OCR или внешние интеграции.
Google, Яндекс, Telegram, Gmail и внешний AI в сценарии не вызываются.

## Переменные и одноразовые тестовые значения

| Переменная | Требование |
|---|---|
| `CI_SMOKE_BASE_URL` | Необязательная, по умолчанию `http://backend:8000`; только адрес тестового API |
| `CI_SMOKE_PASSWORD` | Обязательная, 12–256 символов; передаётся в `exec -e` |
| `BOOTSTRAP_TOKEN` | Обязательная, минимум 24 символа; совпадает с backend |
| `PU_RELEASE_REVISION` | Обязательная; release проверяемого checkout, совпадает с backend |
| `APP_SECRET_KEY` | Минимум 32 символа, только тестовое значение |
| `TOKEN_ENCRYPTION_KEY` | Валидный Fernet key, только тестовое значение |
| `POSTGRES_PASSWORD` | Новый пароль отдельной тестовой БД |

Каждый запуск получает новую БД: повторный bootstrap в уже инициализированной
БД возвращает 409 и считается ошибкой. Не подменять его login и не очищать
общую БД. Повторять весь запуск с новым project name.

В чистом CI checkout из корня репозитория:

```bash
set -euo pipefail
set +x
umask 077
: "${GITHUB_RUN_ID:?run ID required}"
: "${GITHUB_RUN_ATTEMPT:?run attempt required}"
project="puw-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
[[ "$project" =~ ^puw-ci-[0-9]+-[0-9]+$ ]]
compose="$PWD/docker-compose.ci.yml"
test_dir="$(mktemp -d)"
ci_env="$test_dir/ci.env"
export PU_RELEASE_REVISION="$(git rev-parse HEAD)"

# Файл создаётся с правами 0600; генератор не печатает секреты.
python3 - "$ci_env" <<'PY'
import base64
import os
import secrets
import sys
values = {
    "POSTGRES_PASSWORD": secrets.token_hex(24),
    "APP_SECRET_KEY": secrets.token_hex(32),
    "TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "BOOTSTRAP_TOKEN": secrets.token_hex(32),
    "CI_SMOKE_PASSWORD": secrets.token_hex(24),
    "PU_RELEASE_REVISION": os.environ["PU_RELEASE_REVISION"],
    "PU_BACKGROUND_EXECUTION": "in_process",
    "GMAIL_AUTO_SYNC_ENABLED": "false",
    "AI_SECRETARY_AUTOMATION_ENABLED": "false",
}
with open(sys.argv[1], "x", encoding="utf-8") as out:
    for key, value in values.items():
        out.write(f"{key}={value}\n")
PY

# Источник — только созданный выше тестовый файл, не production .env.
set -a
source "$ci_env"
set +a
export COMPOSE_DISABLE_ENV_FILE=1

cleanup() {
  rc=$?
  trap - EXIT
  set +e
  docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" \
    down --volumes --remove-orphans --timeout 20
  cleanup_rc=$?
  rm -f -- "$ci_env"
  rmdir -- "$test_dir"
  if [ "$rc" -eq 0 ] && [ "$cleanup_rc" -ne 0 ]; then rc=$cleanup_rc; fi
  exit "$rc"
}
trap cleanup EXIT

docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" \
  config --quiet
docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" \
  build backend
docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" \
  up -d --wait --wait-timeout 90 db

# Одноразовый контейнер существующего сервиса backend, не третий сервис.
docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" \
  run --rm --no-deps backend alembic -c alembic.ini upgrade head
docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" \
  run --rm --no-deps backend python -m scripts.preflight
docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" \
  up -d --wait --wait-timeout 120 backend

docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" \
  exec -T -e CI_SMOKE_PASSWORD -e CI_SMOKE_BASE_URL=http://backend:8000 \
  backend python - < scripts/ci/smoke_api.py
```

`--env-file` задаётся явно каждой команде. Он управляет Compose interpolation;
сам по себе не передаёт все значения в контейнер. `BOOTSTRAP_TOKEN` и
`PU_RELEASE_REVISION` должны быть явно переданы backend через Compose environment.
Пароль smoke экспортируется в runner и передаётся через `exec -e`, без значения
в аргументах команды. Не запускать `env`, `printenv` или неотредактированный
`docker compose config` в публикуемом логе. Не загружать `ci.env` в артефакты.

## Сценарий и результат

1. `/health`: 200, `status=healthy`.
2. `/api/status`: 200, `status=ok`, точное совпадение `release`.
3. `/api/readiness`: 200 и булево `ready=true`.
4. `/auth/me` без cookies: 401.
5. Bootstrap `smoke@example.test`: 200, `token_type=cookie`, `is_admin=true`,
   сохранение `pu_session` и `pu_csrf`.
6. `/auth/me`: 200 и email тестового пользователя.
7. Создание `CI Organization`: 200, положительный числовой ID.
8. Создание `CI Project` с этим organization ID: 200, проверка полей.
9. Чтение проекта: 200, совпадают ID, имя и organization ID.
10. Список проектов: созданный проект присутствует.
11. Logout с CSRF: 200, `status=logged_out`.
12. `/auth/me` после logout: 401.

Изменяющие запросы после bootstrap отправляются с cookies и `X-CSRF-Token`.
Автоматические redirects отключены; системные HTTP proxy не используются.
На весь этап ожидания health/status/readiness действует единый deadline 60 секунд,
включая сетевые запросы. Повторяются только стартовые GET при сетевой ошибке,
502/503/504 или `ready=false`. Некорректный JSON, иной release и неправильные
типы полей завершают проверку ошибкой. Записи не повторяются даже при timeout.

Успех: отдельные строки `PASS step=... http=...`, затем `PASS smoke-api`, exit 0.
Ошибка: `FAIL step=... http=... reason=...`, ненулевой exit. Ответы сервера,
пароли, cookies и тексты сетевых исключений не выводятся. Если результата POST
нельзя установить из-за сбоя, скрипт останавливается: состояние тестовой БД
может уже измениться; cleanup удалит только БД этого project.

## Диагностика и границы проверки

Проверка состояния (с теми же переменными до cleanup):

```bash
docker compose --project-name "$project" --file "$compose" --env-file "$ci_env" ps --all
```

`bootstrap` 409 означает повторное использование БД; 403 — несовпадение bootstrap
token. Ошибки записи 403 требуют проверки cookies/CSRF, а не отключения auth.
`release revision mismatch` указывает на несовпадение образа или environment.
Readiness timeout требует проверки миграций, тестовых ключей и `in_process`.
Сырые контейнерные логи проверять на секреты перед публикацией.
Workflow сохраняет `diagnostics.json` и `compose.sanitized.log`: состояние
контейнеров, счётчики и упорядоченные разрешённые события логов, включая
шаг и HTTP-код ошибки smoke. Произвольные строки, сообщения исключений
и причины из response body в artifact не попадают. Это сокращённые
выдержки, а не полный журнал. Ошибка на неизвестном этапе сборки может
потребовать отдельного локального воспроизведения.
Workflow экспортирует новые тестовые значения через `GITHUB_ENV`, чтобы
унаследованные shell variables не перекрывали значения `--env-file`.
Cleanup выполняется trap с явным project/compose/env-file; не применять
`docker system prune`, глобальное удаление volumes или остановку чужих проектов.

Локальные проверки скрипта:

Для тестов требуются `pytest`, `httpx`, `PyYAML` и Bash (на Windows — Git Bash).
`PyYAML` нужен только инструментам проверки workflow, не runtime backend.

```bash
python -m pytest scripts/ci/tests/test_smoke_api.py -q
python -m pytest scripts/ci/tests/test_smoke_workflow.py -q
```

Тесты используют httpx MockTransport: проверяют порядок запросов, cookie/CSRF,
ошибки контрактов, общий deadline, отсутствие повторных POST и безопасную
диагностику. Отдельный subprocess запускает скрипт через stdin вне репозитория.
Это не заменяет Docker + PostgreSQL + реальный HTTP smoke после интеграции.

## Намеренный сбой и проверка cleanup

Аварийный прогон включается только для события `push` в ветку
`codex/ci-smoke-integration`, если сообщение проверяемого HEAD-коммита
содержит точную метку `[ci-smoke-fault]`. Обычные push/PR без этой комбинации
сохраняют успешный сценарий. Дополнительный сервер и изменение main не нужны.
Ручной workflow_dispatch не используется: этот workflow пока отсутствует
в default branch репозитория.

Последовательность аварийного прогона:

1. Создать новый Compose project `puw-ci-<run_id>-<attempt>` и чистую БД.
2. Выполнить обычный полный API smoke успешно.
3. Повторить тот же скрипт на инициализированной тестовой БД.
   Ожидается HTTP 409 на bootstrap и exit code 1.
4. Отдельный шаг с `always()` подтверждает одновременно: outcome failure,
   exit 1 и единственную ожидаемую строку ошибки bootstrap. Непредвиденный
   успех, HTTP 403, таймаут и любая другая ошибка не считаются успешной проверкой.
5. Сохранить безопасную диагностику до cleanup.
6. Выполнить штатный `down --volumes --remove-orphans` через `always()`.
7. Независимо от статуса предыдущих шагов проверить отсутствие контейнеров,
   сетей и volumes по `com.docker.compose.project=<точное имя>` и удаление
   временных env/raw-log файлов. Ошибка/таймаут Docker дают непроверенный
   счётчик `null` и провал, а не ноль ресурсов.
8. Загрузить подтверждение cleanup отдельным artifact.

Job намеренно остаётся **красным** из-за реального exit 1 в шаге инъекции;
`continue-on-error` не используется. Это не зелёный CI и не разрешение на
merge. Для принятия аварийного испытания должны одновременно выполняться:

- первый API smoke — success;
- единственная ожидаемая ошибка — шаг инъекции bootstrap conflict;
- `Verify exact injected failure` — success;
- сбор и загрузка диагностики — success;
- cleanup, проверка отсутствия ресурсов и загрузка её результата — success.

Артефакты:

- `docker-smoke-<run_id>-<attempt>`: `diagnostics.json`,
  `compose.sanitized.log`, `fault-assertion.json` (для fault-прогона);
- `docker-smoke-cleanup-<run_id>-<attempt>`:
  `cleanup-verification.json`, также для обычных успешных прогонов.

В fault assertion требуется `expected_bootstrap_conflict_confirmed=true`.
В cleanup report требуются три нулевых счётчика,
`temporary_files_removed=true` и `cleanup_confirmed=true`.
Отсутствие artifact или иной сбой оставляет испытание незакрытым.
После проверки следующий обычный коммит без метки вернёт стандартный
успешный сценарий; публиковать его следует только с разрешения пользователя.
