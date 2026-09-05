# MVP2: операторский контроль ProviderAction

Дата проверки: 2026-09-05  
База: `a6fb152c46fa458f204e086499742661e081d7aa`  
Ветка: `codex/mvp2-provider-controls`

## Результат

Добавлена read-only проекция существующего durable ProviderAction outbox. Новая
очередь, новый ledger и новый provider runtime не создавались. Исполнение
провайдера из HTTP API не добавлялось.

Доступны:

- `GET /provider-actions?project_id=<id>` — список действий проекта;
- `GET /provider-actions/{action_id}/revisions/{revision}?project_id=<id>` —
  точная ревизия;
- `GET /provider-actions/{action_id}/revisions/{revision}/status?project_id=<id>` —
  тот же безопасный оперативный статус;
- существующий `POST .../reconcile` сохранён и по-прежнему только ставит
  reconciliation в durable очередь.

## Гарантии

- сначала проверяется роль `viewer` в указанном проекте;
- действие выбирается одновременно по `organization_id`, `project_id`,
  `action_id` и точной `revision`;
- предыдущая ревизия не подменяется текущей, а получает
  `is_current_revision=false`;
- dispatch/reconciliation job отображается только при точном совпадении kind и
  content-free binding `{organization_id, action_id, revision}`;
- наружу не выдаются payload, mailbox key, адреса, command/idempotency keys,
  evidence pins, provider external ref, raw result и `last_error`;
- `safe_code` проходит allowlist; неизвестное значение заменяется общим
  `outcome_unknown`;
- ответы помечены `Cache-Control: no-store`.

Отображаемые состояния включают business status, effective approval status,
retry/dead-letter, UNKNOWN/reconciliation, безопасный receipt ID (локальный ID
append-only observation), outcome и признак late receipt.

## Проверки

Команды выполнялись из `backend`:

```powershell
python -m pytest -q tests/test_mvp2_provider_action_controls.py `
  tests/test_mvp2_provider_outbox.py tests/test_v54_provider_action_runtime.py
python -m pytest -q
git diff --check
```

Результаты:

- целевые тесты после финального hardening: `40 passed`;
- полный backend regression до финального локального binding-hardening:
  `1328 passed, 19 skipped`;
- финальный binding-hardening дополнительно покрыт целевым набором;
- `git diff --check`: PASS.

Пропуски полного набора — существующие conditional PostgreSQL/runtime сценарии;
этот поток не меняет модели или конкурентное исполнение.

## Изменённые файлы

- `backend/app/api/provider_actions.py`;
- `backend/tests/test_mvp2_provider_action_controls.py`;
- `docs/audits/mvp2-provider-controls.md`.

## Ограничения

- UI-компонент намеренно не добавлялся и `App.tsx` не менялся;
- project-scoped cancel не добавлен: отмена UNKNOWN небезопасна, а существующая
  отмена BackgroundJob остаётся административной операцией;
- endpoint reconciliation использует существующую manager/owner проверку и
  существующую очередь; обход approval отсутствует;
- живые Google API, production и внешние данные не использовались;
- PostgreSQL runtime отдельно не запускался, поскольку изменения read-only и
  не затрагивают схему или claim/lease механику.
