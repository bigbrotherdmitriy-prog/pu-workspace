# Docker Compose smoke: аварийное испытание

Дата: 2026-09-03. **Результат испытания: PASS**.
Область PASS: изолированный Docker/PostgreSQL API smoke, намеренный
bootstrap conflict, диагностика, доставка artifacts и проверка cleanup.
Это не результат полного commercial/runtime hardening продукта.

Проверенный SHA: `4bb4fc8bea4f539884eb9c169519cc423036351c`.
Ветка: `codex/ci-smoke-integration`.
Push выполнен после явного разрешения пользователя на этот SHA и ветку.

## GitHub Actions

- [Fault run 33743153374](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33743153374),
  job `100609497598`: **failure, намеренный**. Единственный failed step:
  `Inject bootstrap conflict on initialized test database`.
- [Обычный CI 33743153358](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33743153358):
  **success**, 382 backend-теста, 17 frontend-тестов, typecheck и build.
- [Предыдущий обычный Compose smoke 33742467119](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/33742467119):
  **success** на SHA `1ac10e2f37cd948c0e8fc1532f58d560ca015409`.

В новом fault run также сначала полностью прошёл обычный API smoke.
После него тот же скрипт повторён на той же синтетической БД и получил
ожидаемый отказ повторного bootstrap. Его exit 1 не подавлялся.

## Протокол и время

GitHub job: 10:13:40–10:14:30 UTC, 50 секунд.
Compose project: `puw-ci-33743153374-1`.

| Этап | Результат | Длительность по GitHub steps |
|---|---|---|
| Compose config | success | <1 s |
| Сборка backend | success | 28 s |
| PostgreSQL + healthcheck | success | 9 s |
| Alembic upgrade | success | 2 s |
| Backend / preflight | success | <1 s |
| Readiness и head f360a1b2c3d4 | success | 2 s |
| Полный авторизованный API smoke | success | 1 s |
| Повторный smoke | ожидаемые HTTP 409 и exit 1 | <1 s |
| Проверка точной причины | success | <1 s |
| Сбор/загрузка диагностических artifacts | success | 1 s |
| Compose down | success | <1 s |
| Проверка остатков ресурсов | success | 1 s |
| Загрузка cleanup artifact | success | <1 s |

Команда инъекции совпадает с обычным smoke; повторный POST автоматически
не повторяется самим клиентом:

```bash
docker compose --project-name "$CI_COMPOSE_PROJECT" --file docker-compose.ci.yml --env-file "$CI_ENV_FILE" exec -T \
  -e CI_SMOKE_PASSWORD -e CI_SMOKE_BASE_URL=http://backend:8000 \
  backend python - < scripts/ci/smoke_api.py
```

Безопасные фактические выдержки:

```text
PASS smoke-api
FAIL step=bootstrap http=409
CI_FAULT_CONFIRMED
CI_CLEANUP_CONFIRMED
```

## Артефакты и целостность

Оба архива скачаны через GitHub connector; SHA-256 локальных ZIP совпал
с `digest` GitHub API. ZIP читался без извлечения путей на диск.

| Artifact | ID | SHA-256 ZIP |
|---|---|---|
| docker-smoke-33743153374-1 | 9888555401 | `cadebdb8b7734706e2755dd2eb1328c1cfd78eb9235851e05a4fc98f1a23cfec` |
| docker-smoke-cleanup-33743153374-1 | 9888556195 | `3484879a19ccc0fdd4cb593ba1f93292209780a4e8c2bf9dbe5fd434f1db7b9e` |

GitHub expiry: 2026-09-10. Для сохранения доказательств тексты скопированы
в репозиторий (переводы строк могут нормализоваться Git; выше хеши исходных ZIP):

- [fault-assertion.json](evidence/ci-smoke-33743153374/fault-assertion.json):
  `expected_bootstrap_conflict_confirmed=true`, ожидаемые HTTP 409 / exit 1.
- [cleanup-verification.json](evidence/ci-smoke-33743153374/cleanup-verification.json):
  containers=0, networks=0, volumes=0, temporary_files_removed=true,
  cleanup_confirmed=true.
- [diagnostics.json](evidence/ci-smoke-33743153374/diagnostics.json):
  compose ps/logs exit=0; перед cleanup оба сервиса running.
- [compose.sanitized.log.txt](evidence/ci-smoke-33743153374/compose.sanitized.log.txt):
  исходный очищенный журнал с дополнительным расширением `.txt` для Git.

В опубликованных файлах только разрешённые статусы, счётчики, HTTP-коды,
имя синтетического Compose project и события smoke. Токенов, cookies,
OAuth credentials, env-файла, raw logs и содержимого документов нет.
Полный сырой лог не публиковался, поэтому его независимый повторный
security scan после удаления не заявляется.

## Решение и ограничения

Для текущей задачи Docker smoke блокер CONDITIONAL снят: нормальный
сценарий и реальный fault/cleanup протокол подтверждены.
Красный GitHub job ожидаем и **не переименовывается в зелёный**. PASS относится
к аварийному испытанию, поскольку причина и последствия ошибки проверены
независимыми успешными шагами и скачанными artifacts.

Версии Docker/Compose, PostgreSQL minor и image digest отдельно не
сохранялись текущим workflow; PostgreSQL image задан `postgres:16-alpine`.
Проверка не покрывает durable workers/scheduler, lease recovery, crash,
backup/restore очереди, OCR и внешние интеграции.

Следующий обычный commit без `[ci-smoke-fault]` запустит штатный smoke;
новый push требует отдельного разрешения. Этот отчёт сохранён локально.
Основная грязная worktree, параллельные задачи, production и локальные
Docker/WSL не изменялись. Merge и production-deploy не выполнялись.
