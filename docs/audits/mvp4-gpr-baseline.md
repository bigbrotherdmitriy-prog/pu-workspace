# MVP4 — версии ГПР, план и факт

Дата проверки: 2026-09-05

Ветка: `codex/mvp4-gpr-baseline`

База: `6452fdac986cefef84748e2afa33962f4fc3e401`

## Результат

Foundation-срез M4-03/M4-04 реализован поверх существующих `ScheduleBaseline` и
`ScheduleItem`. Новая таблица и миграция не потребовались; единственная Alembic
head осталась `a54f001c0a11`.

Закрыты следующие инварианты:

- состав и плановые поля утверждённой или исторической версии нельзя менять;
- новая редакция создаётся клонированием текущей утверждённой версии в `draft`;
- фактическое исполнение при клонировании не переносится;
- утверждение доступно только manager и атомарно переводит прежнюю текущую
  версию в `superseded`;
- прямой произвольный перевод версии в `superseded` закрыт;
- факт принимается только для этапа текущей утверждённой версии;
- плановые поля при записи факта не меняются;
- запись факта и утверждение используют expected-state/CAS, повтор идентичного
  запроса идемпотентен, stale-запрос получает HTTP 409;
- PostgreSQL-операции создания версии сериализуются advisory transaction lock,
  а изменяемые строки выбираются `FOR UPDATE`;
- безопасный opaque `evidence_ref`, если он передан, сохраняется в audit;
- обзор помечает точный current approved baseline, а просрочка считается только
  по его этапам;
- UI раздельно показывает current approved, draft, history, план и факт; clone,
  approval и запись факта требуют явного действия пользователя.

## Regression-first

До реализации новый тестовый модуль не собирался из-за отсутствующего
`BaselineClone`, то есть новый контракт отсутствовал. Затем тесты зафиксировали
clone/version, immutability, manager-only approval, controlled supersede,
plan/fact separation, current-only fact, CAS, replay и evidence audit.

## Проверки

- Целевой backend: `40 passed` (`22` API/baseline + `18` structured/UI contract).
- Полный backend после финальных изменений: `1225 passed, 19 skipped` за 876.08 s.
- Новый frontend-компонентный набор: `3 passed`.
- Полный frontend: `105 passed`.
- TypeScript `tsc --noEmit`: PASS.
- Production build: PASS (`1622 modules transformed`).
- `git diff --check`: PASS.
- Alembic heads: ровно одна, `a54f001c0a11`.

Пропуски полного backend относятся к уже условным PostgreSQL/окружным
сценариям. Отдельный PostgreSQL DSN в этой worktree не предоставлен, поэтому
реальная конкурентная гонка двух транзакций остаётся `CONDITIONAL`; её защита
зафиксирована advisory lock, row locks и тестируемым expected-state контрактом.

## Изменённые области

- `backend/app/api/execution_finance.py`;
- `backend/tests/test_mvp4_gpr_baseline.py`;
- `frontend/src/App.tsx`;
- `frontend/src/modules/finance/FinanceOperations.tsx`;
- `frontend/src/modules/finance/FinanceOperations.test.tsx`;
- `frontend/src/modules/finance/types.ts`;
- `frontend/src/modules/finance/useFinanceController.ts`;
- `frontend/src/source.css`.

## Не входит в foundation

- импорт и синхронизация с внешними планировщиками;
- автоматические внешние действия;
- второй ledger или очередь;
- изменение production, DNS или production database.
