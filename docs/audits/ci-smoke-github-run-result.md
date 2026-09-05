# GitHub Actions: первый запуск и успешная проверка исправления

Обновление: аварийное испытание на `4bb4fc8…` завершено; [итоговый
протокол](ci-smoke-fault-result.md) подтверждает диагностику и cleanup.
Разделы ниже сохранены как история предыдущих запусков.

## Текущий результат после исправления

Проверен SHA `1ac10e2f37cd948c0e8fc1532f58d560ca015409`, отправленный в
`codex/ci-smoke-integration` после отдельного явного разрешения пользователя.

- [Docker Compose smoke — SUCCESS, run 33742467119](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33742467119).
- [PU Workspace CI — SUCCESS, run 33742467112](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33742467112).

GitHub-hosted runner: Ubuntu 24.04, image version `20260823.283.1`.
Smoke job `100607293891`: 2026-09-03 10:06:00–10:06:57 UTC, 57 секунд.
Тестовый Compose project: `puw-ci-33742467119-1`.

| Этап Docker smoke | Результат | Время по GitHub steps |
|---|---|---|
| Generate isolated test environment | SUCCESS | <1 s |
| Compose configuration | SUCCESS | <1 s |
| Build backend | SUCCESS | 32 s |
| PostgreSQL start + healthcheck | SUCCESS | 10 s |
| Alembic upgrade head | SUCCESS | 2 s |
| Backend start | SUCCESS | <1 s |
| Readiness + schema f360a1b2c3d4 | SUCCESS | 4 s |
| Authenticated API smoke | SUCCESS | 1 s |
| Compose down --volumes --remove-orphans | SUCCESS | <1 s |
| Failure diagnostics / artifact | Штатно пропущены | Запуск успешный |

Успех API-скрипта подтверждает health, release SHA, readiness, bootstrap,
cookie/CSRF, создание/чтение организации и проекта, logout и последующий 401.
В обычном CI: **382 backend-теста**, **17 frontend-тестов (6 файлов)**,
typecheck и build прошли; frontend build — 2.29 s.

Ограничения доказательств:

- `down` вернул успех; отдельного запроса проверки отсутствия ресурсов по
  labels после удаления в текущем workflow нет.
- Реальные failure diagnostics, доставка очищенного artifact и cleanup
  после намеренной ошибки ещё не испытаны.
- Сырые Compose/build logs успешного прогона направляются во временный
  файл и удаляются. Точные версии Docker/Compose/PostgreSQL и image digest
  этим workflow отдельно не сохраняются; не подменяем их версиями локальной машины.
- Durable-очередь, crash/recovery, workers, scheduler и внешние интеграции
  не входят в двухсервисный in_process smoke.

**Успешный Docker smoke и оба текущих CI: PASS. Полный протокол с аварийным
сценарием остаётся CONDITIONAL.** Production и локальные Docker/WSL не
изменялись. Merge и production-deploy не выполнялись. Этот раздел отчёта
добавлен локально после запуска; дополнительных push без разрешения нет.

## История первого запуска

Дата: 2026-09-03.
Ветка: `codex/ci-smoke-integration`.
Проверяемый SHA: `a3b53ef79ff5e828660e086bf47abe9720fa0c35`.
Push выполнен после явного разрешения пользователя на этот SHA и ветку.
Merge и production-deploy не выполнялись.

## Фактические результаты

| Проверка | Результат и доказательство |
|---|---|
| Обычный PU Workspace CI | [SUCCESS, run 33741920506](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33741920506) |
| PostgreSQL service и чистая миграция | SUCCESS, upgrade до `f360a1b2c3d4` |
| Backend | `382 passed, 3 warnings in 8.06s` |
| Frontend typecheck | SUCCESS |
| Frontend tests | `6 passed` файлов, `17 passed` тестов |
| Frontend build | SUCCESS, `built in 2.32s` |
| Загрузка verification logs и остановка service-контейнеров | SUCCESS по steps job `100605579212` |
| Docker Compose smoke | [FAILURE, run 33741918971](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33741918971), workflow отклонён до создания job |

Безопасная выдержка журнала миграций:

```text
Running upgrade c83d0a24b512 -> f360a1b2c3d4, add provider-neutral storage credentials and project locator metadata
382 passed, 3 warnings in 8.06s
```

GitHub сообщил для smoke: `Unrecognized named-value: 'runner'` в выражении
`runner.temp`, строки 22–24. В ответе jobs API: `total_count: 0`.
Это дефект определения workflow, не результат проверки Docker-приложения.
Изолированный Compose, health/readiness, API smoke, artifacts и cleanup
этого workflow ещё не выполнялись.

## Подготовленное исправление

Контекст `runner` недоступен в `jobs.smoke.env`. Три пути перенесены в
`steps[].env` шага генерации тестового окружения. Через `GITHUB_ENV` они
передаются остальным шагам, включая диагностику и `always()` cleanup.
Секреты, правила изоляции и продуктовый код не менялись.

Регрессионный тест сначала воспроизвёл ошибку: `1 failed`.
После исправления весь `scripts/ci/tests`: **55 passed, 2.27 s**.
Дополнительно тест проверяет передачу всех трёх путей последующим шагам.

Впервые выполнен actionlint **1.7.12**: до исправления он обнаружил те же
три недопустимых обращения к `runner`; после исправления оба workflow
проходят проверку. Использован официальный Windows amd64 archive с SHA-256
`6e7241b51e6817ea6a047693d8e6fed13b31819c9a0dd6c5a726e1592d22f6e9`,
сверенным с digest GitHub release asset. Бинарник находится во временном
каталоге вне репозитория и в поставку не включён.

Команды:

```text
python -m pytest scripts/ci/tests -q -p no:cacheprovider
actionlint -shellcheck= -pyflakes= .github/workflows/docker-smoke.yml .github/workflows/ci.yml
git diff --check
```

ShellCheck/Pyflakes здесь не запускались; Bash и embedded Python проверяются
также локальными тестами. YAML parsing не заменяет actionlint: именно это
ограничение предыдущего статического прогона позволило пропустить дефект.

## Статус и следующий шаг

Исходный smoke workflow: **FAIL**. Обычный CI: **PASS**.
Снятие общего CONDITIONAL пока невозможно: нужен новый разрешённый push
исправления и фактический Docker smoke, затем проверка аварийного сценария.
На момент подготовки отчёта разрешение на push относилось только к исходному
SHA; исправляющий коммит пока не опубликован.

Проверки локального Docker и VPS не повторялись. Основная пользовательская
worktree, production и исходные параллельные ветки не изменялись.
