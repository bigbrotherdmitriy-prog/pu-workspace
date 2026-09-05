# MVP1 completion — synthetic acceptance result

Дата: 2026-09-04
Ветка: `codex/mvp1-completion`
База: `a19fffde54e51aee0b42220c83f6c19b1d3b9055`

## Решение

Завершён тестируемый без внешних аккаунтов P0-срез MVP1. Подключение папки
теперь создаёт только неизменяемый метаданный snapshot и не создаёт физическую
копию автоматически. После готовности snapshot пользователь отдельно выбирает:

1. read-only анализ без копии;
2. создание safe-copy с применением единого стандарта имён и структуры.

Оба действия остаются durable jobs существующей очереди. Реальные документы,
OAuth-данные и production не использовались.

## Закрытые критерии

- сохранение `project_id`, provider, `connection_id` и точного folder locator;
- папки любой глубины Google Drive и `disk:/` / `app:/` Яндекс Диска;
- защита от возврата в старый Persistent Project и stale picker response;
- идемпотентное подтверждение папки и восстановление enqueue;
- metadata snapshot без обязательной физической копии;
- отдельные явные действия «анализ без копии» и «safe-copy + стандарт»;
- единая структура папок, детерминированное и идемпотентное имя;
- dry-run и `conflict_source_changed` до изменения источника;
- идемпотентные rename/move и повторяемый rollback;
- отображение фактического durable-job progress, без вымышленных процентов;
- редактирование договора;
- архивирование договора с сохранением документов, ГПР, ДДС и прочих связей;
- deletion preview и запрет физического удаления при любых известных связях;
- физическое удаление пустой карточки только по точному номеру договора.

## Проверки

- целевой backend: `68 passed`;
- полный backend: `1140 passed, 19 skipped`;
- целевой frontend: `19 passed`;
- полный frontend: `96 passed`;
- TypeScript: PASS;
- production frontend build: PASS;
- `git diff --check`: PASS.

Пропуски полного backend-набора относятся к уже существующим platform- и
PostgreSQL-only сценариям. Новая миграция не требуется; Alembic head не менялся.

## Live gaps

Статус этого отчёта — `CONTRACT PASS / LIVE GATE OPEN`. Не выполнены:

- OAuth acceptance на изолированных Google Drive и Яндекс Диск test accounts;
- проверка provider-native revision после изменения реального файла;
- реальный rename/move/rollback на тестовой папке;
- latency и delta-scan на 1 000/10 000 provider objects;
- браузерный E2E с живыми provider picker/API вместо synthetic fixtures.

До этих проверок нельзя объявлять Google/Яндекс live acceptance или production
enable. Исходные provider-объекты не изменялись этой работой.
