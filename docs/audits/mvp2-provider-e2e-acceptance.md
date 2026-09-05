# MVP2 ProviderAction: synthetic end-to-end acceptance

Дата: 2026-09-05

База: `9c0fc2682b37bc8526cc27b78f2a125a8d45ff8b`

Ветка: `codex/mvp2-provider-e2e-acceptance`

## Результат

Синтетический offline-сценарий теперь проверяет целую продуктовую цепочку на
существующих `ProviderAction`, `ProviderDispatchOutbox` и `BackgroundJob`:

1. человек с ролью manager подтверждает подготовленное Gmail-действие;
2. создаётся одна content-free durable job;
3. worker выполняет её через offline provider double;
4. timeout после эффекта сохраняется как `UNKNOWN`, без повторной отправки;
5. API показывает безопасный `requires_reconciliation` и локальный receipt id;
6. HTTP reconcile создаёт существующую durable reconciliation job;
7. второй worker делает только lookup и получает позднюю `APPLIED`-квитанцию;
8. API показывает `completed`, `resolved`, завершённое задание и позднюю
   квитанцию;
9. UI автоматически опрашивает только активные задания и отображает итог, не
   повторяя provider action.

## Найденный дефект и исправление

Acceptance-прогон воспроизвёл два дефекта. После успешного reconciliation API
показывал `resolved`, но поле `reconciliation` становилось `null`. Worker
проверял собственный claim, однако созданная append-only observation не
сохраняла `job_id`, потому что базовый runtime всегда передавал `None`.

Исправление минимальное:

- базовый runtime получил защищённый `_reconcile_with_job`;
- обычный ручной `reconcile` сохраняет прежнее поведение без job binding;
- только product worker после полной проверки claim вызывает
  `reconcile_claimed(..., job_id=...)`;
- observation связывается с точной reconciliation job, и безопасная проекция
  показывает её завершённое состояние.

Повтор одинакового HTTP reconcile корректно переиспользовал idempotent job, но
ошибочно отвечал `already_queued=false`, пока она ещё имела статус `queued` и
нулевое число попыток. Теперь наличие exact idempotency key проверяется под уже
существующей блокировкой ProviderAction до штатного `enqueue`; повтор получает
тот же `job_id`, `already_queued=true`, а в БД остаётся одна reconciliation job.

Новая очередь, ledger, provider adapter и миграция не создавались.

## Безопасность

- fixture полностью синтетический, сеть не используется;
- после `UNKNOWN` нет blind resend: один provider effect и один lookup;
- payload обеих jobs содержит только `organization_id`, `action_id`, `revision`;
- API/UI не показывают body, адрес, mailbox key, external ref, raw response,
  result или last error;
- UI отправляет reconcile только для exact current Google action/revision и
  только в серверно допустимых состояниях;
- 409/403 отображаются без server detail;
- смена проекта отменяет применение позднего GET или mutation response;
- polling включён только для queued/running/retrying и прекращается после
  terminal/resolved состояния.

## Проверки

- regression до исправления: FAIL — завершённая reconciliation job не была
  видна в resolved-проекции, а повторный HTTP reconcile неверно сообщал, что
  создал новое задание;
- backend acceptance + provider regressions: `41 passed`;
- полный backend: `1357 passed, 20 skipped`;
- frontend targeted acceptance: `12 passed`;
- полный frontend: `187 passed`;
- TypeScript check и production build: PASS;
- `git diff --check`: PASS.

## Ограничения

- живые Gmail/Google APIs не вызывались и этим отчётом не подтверждаются;
- реальная PostgreSQL-конкурентность и multi-process worker topology должны
  пройти отдельно в изолированном runtime CI;
- 20 backend skip относятся к уже существующим conditional PostgreSQL/runtime
  сценариям; они не заменены статическими проверками;
- browser E2E против поднятого backend не выполнялся: UI-переходы проверены в
  jsdom с точными безопасными API envelopes;
- production, OAuth credentials, реальные письма и пользовательские данные не
  использовались.
