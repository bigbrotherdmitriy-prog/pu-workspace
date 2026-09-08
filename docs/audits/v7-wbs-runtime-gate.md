# WBS PostgreSQL runtime gate

Дата: 2026-09-08

Ветка: `codex/v7-wbs-runtime-gate`

База: `06bf2cc2c4d21238702ea16474282606f8627140`
Область: только CI, тесты и этот отчёт; Product Core не изменялся.

## Решение

В существующий изолированный PostgreSQL workflow добавлена отдельная обязательная
фаза `postgres_v7_schedule_wbs`. Она использует уже создаваемую и удаляемую
runner-ом базу `puw_mvp4_test_runtime`; второй PostgreSQL runtime и новая очередь
не создаются. Пропуск, недобор или лишний тест делают фазу `SKIPPED`/`INCOMPLETE`,
а общий gate — ошибочным.

## Исполняемые доказательства

Фаза закрепляет ровно семь test node:

1. чистая head `a54f001c0a21` и реальный цикл `a20 -> a21` с сохранением плоских строк;
2. DB constraint запрещает отрицательный `wbs_order`;
3. DB constraint запрещает summary с leaf-полями;
4. service layer fail-closed отклоняет self-FK в другой baseline;
5. два конкурентных complete-graph PUT дают ровно одного CAS-победителя, а summary rollup сохраняется;
6. clone переносит родителей и predecessor links только на ID нового baseline;
7. downgrade с WBS intent отказывается с `schedule_wbs_downgrade_requires_verified_restore`.

Тесты используют только синтетические строки. URL допускается существующим guard
только для явно принадлежащей тестовой базы на localhost/CI service. Production
database, провайдеры и пользовательские документы не читаются.

## Безопасный протокол

Публикуется прежний allowlisted `v54-runtime-artifacts/protocol.json`. Для новой
фазы в нём допустимы только имя фазы, статус, exit code, длительность, счётчики,
размеры скрытого stdout/stderr и признак `raw_published=false`. Captured stdout и
stderr, DSN, SQL, названия WBS-строк, документы, письма и секреты в artifact не
публикуются. В `coverage_limits` добавлено только обезличенное описание WBS scope.

## Проверки в текущем окружении

- WBS/CI/migration regression: `64 passed`;
- семь PostgreSQL test node собраны: `7 skipped` без явно заданной owned PostgreSQL URL;
- Python compilation: PASS;
- YAML workflow contract: PASS (входит в 64 теста);
- полный `scripts/ci`: `366 passed` с Git Bash; первоначально найден и исправлен
  один устаревший агрегатный счётчик (после семи новых WBS proofs итог — 51);
- `git diff --check`: PASS;
- actionlint: NOT RUN — исполняемый файл и Go toolchain локально отсутствуют;
- реальный PostgreSQL runtime: NOT RUN локально; он является целью добавленного CI gate.

Статус: **CONDITIONAL** до выполнения workflow на GitHub runner. Offline-тесты и
SQLite не засчитываются как PostgreSQL runtime PASS.

## Команды проверки

```powershell
$env:PYTHONPATH='backend'
python -m pytest scripts/ci/test_v7_wbs_runtime_gate.py scripts/ci/test_v7_schedule_runtime_gate.py scripts/ci/test_mvp_runtime_coverage.py scripts/ci/test_v54_pilot_workflow.py backend/tests/test_v7_schedule_wbs_migration.py backend/tests/test_v7_schedule_wbs.py -q --tb=short
python -m pytest backend/tests/test_v7_schedule_wbs_postgres.py -q --tb=short -rfs
git diff --check
```

После отдельного разрешения на push workflow запускается автоматически для ветки
`codex/v7-wbs-runtime-gate`; также доступен ручной `workflow_dispatch`. Merge и
production deploy этим потоком не выполняются.
