# PU Workspace v5.4 — process-fault gaps S07/S08

Дата: 2026-09-04  
База: `f869319e226d0563d9c95eec408adcf716ed7e9f`  
Ветка: `codex/v54-wave3-fault-gaps`

## Результат

Добавлен исполняемый PostgreSQL process-fault harness для двух оставшихся
сценариев acceptance corpus. Product Core, схема БД и production-конфигурация не
изменены.

Базовый commit `f869319...` уже имеет успешный runtime CI. Эта работа не меняет
его статус. Новый расширенный harness требует отдельного повторного запуска того
же изолированного workflow на итоговом интеграционном commit.

## Аудит до изменения

- `PendingDispatch` уже является durable T1 recovery index; `recover()` повторно
  использует единственный `BackgroundJob.idempotency_key`.
- Task, TaskHistory, ActionReceipt и business audit уже принадлежат одной T2
  транзакции.
- Повтор T2 уже возвращает существующий receipt до вызова Task mutation.
- Предыдущий process probe завершал worker после claim, но не в точных окнах
  S07/S08. Unit-тесты моделировали эти окна исключением/ручным состоянием, а не
  настоящим завершением процесса.

## Реализованные fault-сценарии

### S07 — T1 commit → process kill → enqueue recovery

1. Отдельный spawn-процесс создаёт и коммитит exact pending intent.
2. Он сообщает только безопасные IDs и останавливается до enqueue.
3. Родитель завершает только созданный тестовый процесс.
4. PostgreSQL проверяется на один pending intent и ноль transport jobs.
5. Новый reconciler вызывает `recover()`, создаёт один job со стабильным ключом.
6. Второй worker выполняет intent; повторный reconcile возвращает ноль.
7. Проверяются ровно одна Task, history, receipt, projection и success audit.

### T2 pre-commit — process kill → полный rollback

SQLAlchemy `after_flush` используется только в spawn-процессе тестового harness.
Процесс останавливается после flush Task/history/receipt, но до выхода из
`sessions.begin()`. После kill PostgreSQL обязан показать ноль бизнес-сущностей.
После lease recovery второй worker создаёт ровно один согласованный результат.

### S08 — T2 commit → process kill → receipt replay

1. Первый worker коммитит T2, но намеренно не вызывает `queue.succeed`.
2. После безопасного checkpoint процесс завершается.
3. PostgreSQL должен содержать Task/history/receipt/audit, а job оставаться
   `running`.
4. После ускоренного истечения lease второй worker получает тот же job.
5. Исполнение возвращает существующий receipt; Task mutation и business audit не
   повторяются; затем job становится `completed`.

## Безопасность протокола

Публикуются только: имя probe/case, PASS/FAIL, числовые IDs и счётчики, статусы
job и факт process kill. DSN, subprocess arguments, raw stdout/stderr, envelope,
payload, содержимое писем/документов и секреты не публикуются. Ошибка дочернего
процесса представляется только именем класса и allowlisted checkpoint.

## Изменённые файлы

- `scripts/ci/v54_pilot_runtime.py`
- `scripts/ci/v54_pilot_workflow.py`
- `scripts/ci/test_v54_pilot_workflow.py`
- `docs/audits/v54-wave3-fault-gaps.md`

Миграция не требуется. Alembic head остаётся `a54f001c0a08`.

## Проверки

- `test_v54_pilot_integration.py` + CI contract tests: **36 passed**.
- Целевые CI contract tests: **16 passed**.
- Python compilation: PASS.
- `git diff --check`: PASS.
- Полный `scripts/ci`: **118 passed**, 3 существующих Windows/MSYS path-encoding
  failures из-за кириллицы в пути worktree; они не относятся к изменённому
  harness и ранее не воспроизводились в ASCII/Linux CI.
- Локальный PostgreSQL/Docker отсутствует, поэтому новые реальные process kills
  локально не засчитываются. Их выполняет `v54-pilot-runtime.yml` на GitHub.

## Интеграция

Перенести единственный commit этой ветки поверх `f869319...`, затем запустить:

```text
git cherry-pick <commit>
git push origin <integration-branch>
gh workflow run v54-pilot-runtime.yml --ref <integration-branch>
```

Runtime PASS для расширения фиксируется только если safe `protocol.json` содержит
`s07_intent_recovery`, `t2_precommit_rollback`, `s08_receipt_replay` со статусом
`PASS`, финальный cleanup и общий `result: PASS`.

Production, merge, deploy, реальные провайдеры и пользовательские данные не
затрагивались.
