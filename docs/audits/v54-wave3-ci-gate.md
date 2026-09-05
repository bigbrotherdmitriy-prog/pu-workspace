# PU Workspace v5.4 Wave3 CI gate

Дата проверки: 2026-09-04. База: `d55f84152bbdb8e6ce71a277911715ac5dec8609`.

## Результат

Wave3 получает два независимых обязательных runtime-контура при push ветки
`codex/v54-wave3-integration`:

- `v54-pilot-runtime.yml` — чистая PostgreSQL, Alembic, A/B/C, acceptance corpus
  и process-fault probe;
- `durable-queue.yml` — два API, два worker, scheduler, lease recovery,
  idempotency, retry/dead-letter/redrive/cancel и backup/restore.

Оба контура имеют только `contents: read`, используют синтетические секреты и
публикуют лишь заранее перечисленные JSON-протоколы. Маска
`queue-artifacts/*.json` удалена. Durable harness больше не записывает аргументы
команд в протокол: сохраняется только грубая allowlisted-категория операции,
exit code и длительность. Raw stdout/stderr остаются внутри процесса и не
публикуются.

Cleanup durable Compose выполняется с `if: always()` до публикации artifact;
orchestrator v5.4 удаляет только созданные им базы в `finally` и фиксирует
результат cleanup в протоколе.

## Schema contract

Единственная ожидаемая Alembic head: `a54f001c0a07`. Она одинаково закреплена в
v5.4 orchestrator, durable harness и Docker smoke readiness.

Если интеграция компенсационного контура создаст последовательную migration
`a54f001c0a08`, нужен один отдельный точечный commit: обновить эти три pin,
schema/readiness pin продукта, соответствующие contract tests и этот отчёт.
Нельзя заранее принимать `a08`, создавать merge head или ослаблять проверку до
`upgrade head` без точного сравнения версии.

## Проверки

- contract/regression suite: `26 passed`;
- actionlint `v1.7.12` по всем пяти workflow: PASS;
- standalone Docker Compose `v5.5.1` config для
  `docker-compose.queue-ci.yml`: PASS;
- standalone Docker Compose `v5.5.1` config для `docker-compose.ci.yml`: PASS;
- Alembic graph: единственная head `a54f001c0a07`;
- `git diff --check`: PASS.

Docker engine для `config --quiet` не требуется; контейнерный runtime этим
результатом не подтверждается.

Команды для воспроизведения:

```text
python -m pytest scripts/ci/test_v54_pilot_workflow.py scripts/ci/test_v54_wave3_ci_gate.py scripts/ci/durable_queue/test_contract.py scripts/ci/durable_queue/test_run.py -q
actionlint .github/workflows/*.yml
docker compose --file docker-compose.queue-ci.yml config --quiet
docker compose --file docker-compose.ci.yml config --quiet
```

Runtime Docker/PostgreSQL этим локальным аудитом не подменяется: он считается
PASS только после зелёных GitHub Actions на итоговом Wave3 SHA.

## Остаточный риск вне границ коммита

Общий `.github/workflows/ci.yml` по историческому контракту публикует сырые
pytest/frontend logs. В тестовом CI они не должны содержать пользовательские
данные, однако это не allowlisted-протокол. Для коммерческого hardening нужен
отдельный поток, который заменит эти artifacts на агрегированный безопасный
JSON; данный Wave3 runtime gate не расширяет свою область на общий CI.
