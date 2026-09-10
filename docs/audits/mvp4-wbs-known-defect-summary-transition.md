# MVP-4 WBS — known defect: summary transition

Дата фиксации: 2026-09-10

Статус: **OPEN / PRE-EXISTING / OUTSIDE MVP-1 SCOPE**

Источник реализации: commit `027880f47159d1668dbd87edcb8139c7ba4b5e2b`
(`feat(schedule): add durable WBS hierarchy`). Эта реализация предшествует
MVP-1 Phase 1b (`3d62af1`), Phase 1c (`e1bf219`) и Phase 2 (`cd42576`).

Runtime evidence: [GitHub Actions run #36](https://github.com/bigbrotherdmitriy-prog/pu-workspace/actions/runs/34437416295),
job `runtime`, test
`test_pg_wbs_summary_constraint_rejects_leaf_intent`.

## Наблюдаемое поведение

Constraint `ck_schedule_summary_intent` допускает `UPDATE schedule_items SET
is_summary=true`, если у строки уже отсутствуют duration, milestone,
dependencies и ограничения. Legacy-строка с таким состоянием после изменения
неотличима на уровне row-level CHECK от корректной summary-строки. Поэтому
PostgreSQL не выдаёт ожидаемый `IntegrityError`.

Текущий constraint:

```sql
is_summary = false OR (
    duration_days IS NULL AND is_milestone IS NULL
    AND predecessor_ids IS NULL AND constraint_type IS NULL
    AND constraint_date IS NULL AND not_before_date IS NULL
)
```

## Отклонённые минимальные варианты

1. **Ужесточить CHECK обязательными `planned_start/planned_finish`.** Это
   блокирует summary без рассчитанных дат, может не пройти на существующих
   данных и меняет текущую модель пустых/ещё не рассчитанных summaries.
2. **Запретить переход `is_summary: false -> true` PostgreSQL-trigger.** Это
   требует новой последовательной миграции после `a54f001c0a24` (предлагаемый
   revision `a54f001c0a25`) и ломает существующую app-level возможность
   преобразовать legacy row в summary. Несколько текущих WBS-тестов используют
   именно этот переход.

Оба варианта требуют отдельного решения владельца MVP-4 о допустимой семантике
`leaf -> summary`, проверки существующих данных и отдельной regression/runtime
приёмки. Миграция `a54f001c0a21` не переписывается.

## Влияние на MVP-1

Миграция WBS `a54f001c0a21` находится в общей линейной истории перед MVP-1 OAuth
`a54f001c0a22` и Phase 2 `a54f001c0a23/a54f001c0a24`. Upgrade до head
технически проходит, но общий historical migration/runtime suite остаётся
красным из-за этого MVP-4 invariant test. Это не является регрессией MVP-1 и
не изменяет результат отдельного `postgres_mvp1_storage` gate.

Дальнейшие изменения WBS/MVP-4 запрещены без отдельного запроса.
