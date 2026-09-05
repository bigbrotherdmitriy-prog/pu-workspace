# Интеграция MVP1 + MVP2

Дата: 2026-09-05

Ветка: `codex/mvp12-integration`

База: `a19fffde54e51aee0b42220c83f6c19b1d3b9055`

## Результат

Готовые локальные срезы MVP1 и MVP2 перенесены последовательно:

1. `f815e79223b6693803f8367216709718fef70bfa` — MVP1;
2. `ab4357ab3e3bc0c1615a6cccbff6a5340ecb1003` — MVP2.

Оба cherry-pick завершились без конфликтов. Единственный пересекающийся файл —
`frontend/src/App.tsx`; Git выполнил построчное объединение, после чего совместное
поведение проверено полным frontend-набором и production build.

Интеграция сохраняет:

- отдельные действия read-only анализа и safe-copy со стандартизацией;
- точную привязку проекта, провайдера, подключения и папки;
- измеряемый прогресс существующего `BackgroundJob`;
- безопасное редактирование, архивирование и удаление карточек договоров;
- отложенный анализ неоднозначных писем до подтверждения контекста человеком;
- редактируемый точный envelope ответа и отзыв approval после изменения;
- явные состояния `requires_action`, `awaiting_reply` и `completed`.

## Инварианты интеграции

- В ORM существует одна модель и одна таблица `BackgroundJob`.
- Новая очередь, scheduler или ledger не добавлялись.
- Интегрированные изменения не создают новых job payload. Уже используемые
  workspace jobs содержат идентификаторы проекта/snapshot/source, а импорт
  Gmail attachment сохраняет в payload только opaque `staging_id`.
- Текст документов и писем, base64, OAuth credentials, ключи и секреты в job
  payload не добавлены.
- Alembic имеет одну head: `a54f001c0a09`.
- `backend/app/schema.py`, readiness, Docker smoke и runtime harness ожидают
  `a54f001c0a09`.

## Проверки

| Проверка | Результат |
|---|---|
| MVP1 + MVP2 + schema targeted | `9 passed` |
| Полный backend pytest | `1153 passed, 19 skipped` |
| Полный frontend Vitest | `102 passed` |
| TypeScript check | PASS |
| Production frontend build | PASS |
| Alembic heads | одна: `a54f001c0a09` |
| `git diff --check` | PASS |

Полный backend-набор выполнен с отдельным `--basetemp` внутри worktree, потому
что системный `%TEMP%/pytest-of-dpush` недоступен в файловом sandbox. Первый
прогон с системным Temp завершился инфраструктурными `PermissionError`; после
смены только временного каталога продуктовые тесты прошли.

19 пропусков относятся к существующим environment-gated PostgreSQL, POSIX и
live-provider сценариям. Они не заменялись моками и не скрывались новыми skip.

## Оставшиеся ограничения

Статус кандидата: **OFFLINE PASS / LIVE CONDITIONAL**.

- Не проверялись живые Google Drive и Яндекс Диск test accounts: provider-native
  revision, rename/move/rollback, 1 000/10 000 objects и live picker.
- Не проверялись Gmail ingress/history и отправка через выделенный тестовый
  mailbox, а также Google Tasks/Calendar.
- Legacy внешние маршруты ещё не полностью переведены на durable provider-action
  outbox; timeout-after-effect и reconciliation требуют отдельного runtime gate.
- PostgreSQL lease/concurrency и restart attachment import не выполнялись в этой
  worktree.
- Durable company entity для многопроектных контактов остаётся продуктовым
  решением; сейчас используется проверяемая organization-wide contact hint.

Production, DNS, production БД и реальные пользовательские данные не
изменялись. Push, merge и deploy не выполнялись.
