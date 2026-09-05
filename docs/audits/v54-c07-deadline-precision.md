# PU Workspace v5.4 — C07 exact deadline precision

Дата: 2026-09-04

Ветка: `codex/v54-wave3-c07-deadline`

База: `f869319e226d0563d9c95eec408adcf716ed7e9f`

Статус: **IMPLEMENTED; PostgreSQL CI pending integration**

## Решение IR-03

`DeadlineClaim` получил обратно совместимое nullable-поле `due_time`.
Существующий date-only контракт остаётся без изменений: `due_time = NULL`,
`due_date` и прежние зоны `Europe/Moscow` / `UTC` продолжают приниматься.

Точный C07 claim хранится без усечения как три связанные части:

- `due_date = 2030-04-17`;
- `due_time = 18:30:00`;
- `timezone = UTC+03:00`.

Из них однозначно восстанавливается corpus timestamp
`2030-04-17T18:30:00+03:00`. Допустимы только каноническое время
`HH:MM:SS`, `UTC` или фиксированные UTC offsets в диапазоне до 14 часов.
Именованная `Europe/Moscow` остаётся разрешена для прежнего date-only
контракта, но timed claim требует явного offset: неоднозначность не угадывается.
Неполные и некорректные offsets отклоняются до записи.

## Safety

- Extraction всегда создаёт `unverified` claim: низкая или неизвестная
  confidence evidence не заменяет отдельное человеческое review.
- Текущий Task payload остаётся date-only. Sealing точного timed claim явно
  завершается `claim_precision_unsupported`, поэтому `18:30` нельзя молча
  отбросить и Task/receipt не создаются.
- AUTO и внешние действия не включались и не менялись.
- Миграция `a54f001c0a09` последовательна после `a54f001c0a08`; поле nullable,
  backfill отсутствует. Downgrade отказывается терять timed claims без их
  явного архивирования.
- Runtime protocol включает C07 в `executed_cases` и больше не объявляет его
  expected gap.

## Фактические проверки

- Полный backend: **1124 passed, 17 skipped**. Все skips — явно условные
  PostgreSQL URL / `PU_TEST_POSTGRES` или недоступное создание symlink локально.
- Финальный DeadlineClaim/corpus/schema/trust regression: **160 passed, 1 skipped**.
- CI/durable contract suite: **26 passed**.
- Corpus validator `--self-test`: **PASS**, 28 cases, 31 negative checks.
- Alembic: ровно одна head **`a54f001c0a09`**.
- Python compile и `git diff --check`: **PASS**.

Acceptance покрывает exact timestamp, numeric low confidence, обязательный
human review, отказ date-only action sealing, отсутствие Task/receipt и
обратную совместимость старого date-only DTO. Schema regression проверяет
nullable `TIME WITHOUT TIME ZONE` и downgrade guard.

## Интеграция

Cherry-pick единственного коммита этой ветки на актуальную integration-ветку.
После возможного разрешения конфликтов schema-head pins выполнить чистый
PostgreSQL upgrade до `a54f001c0a09`, полный v5.4 runtime и durable/Docker gates.
Локальный прогон не засчитывает эти PostgreSQL/Docker проверки и не трогал
production, реальные данные, provider, deploy или push.
