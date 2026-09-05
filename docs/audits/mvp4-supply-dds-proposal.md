# MVP4 Supply → ДДС proposal

Дата: 2026-09-05

База: `5c631cc91eea2202369019ddc078cefcce489bdb`

Ветка: `codex/mvp4-supply-dds-proposal`

## Результат

Добавлен только управляемый пользователем переход из подтверждённого размещения
заказа снабжения в предложение расходной записи ДДС. Операция не создаёт оплату,
банковскую проводку, provider action или BackgroundJob.

Предложение содержит точные contract/stage/budget, amount/currency и exact-current
Evidence pins. Backend повторно проверяет текущую SourceVersion, свежий human-reviewed
assessment, confidence, проект, tenant, договор, утверждённую строку бюджета и этап.
Низкая уверенность, устаревшая версия, чужой scope и неподтверждённый бюджет закрывают
операцию fail-closed.

Пользователь видит рассчитанную сумму заказа, явно задаёт сумму и плановую дату,
выбирает строку бюджета и заново выбирает Evidence. Создаётся `CashFlowEntry` только
со статусом `proposed`, `actual_amount=0`, без `actual_date`. Подтверждение плана и
подтверждение факта оплаты остаются отдельными существующими manager-командами.
Коррекция факта остаётся отдельным immutable `CashFlowFactHistory` event.

Идемпотентность обеспечивается существующим immutable `SupplyCommandReceipt`:
одинаковые header/body key и payload возвращают тот же `cash_flow_id`; конфликтующий
payload и stale `recordVersion` отклоняются. В SupplyCase добавляется immutable
`dds_proposed` history snapshot без изменения бизнес-статуса заказа.

## Проверки

- Python syntax compile: PASS;
- TypeScript check: PASS;
- targeted frontend Supply tests: `21 passed`;
- `git diff --check`: PASS;
- backend regression-сценарии добавлены, но runtime pytest не выполнен: после
  перезагрузки в локальной среде отсутствует Python 3.13/native libpq;
- PostgreSQL CAS/concurrency: CONDITIONAL.

## Ограничения и решения

- OWNER: определить, разрешены ли несколько отдельных ДДС-предложений для одного
  заказа (текущий контракт разрешает их только как разные явные команды).
- OWNER: решить, следует ли вместо ручного выбора ID строки бюджета добавить
  отдельный безопасный selector UI.
- LEGAL/FINANCE: утвердить правила НДС, аванса, удержаний и допустимого расхождения
  суммы счёта с суммой заказа; текущая реализация ничего не вычисляет юридически.
- PostgreSQL: повторить конкурентный CAS/idempotency и rollback в изолированном CI.
- Живые документы, провайдеры, production, платежи и проводки не использовались.

Новая модель, миграция, очередь и schema pin не понадобились. Использованы текущие
`CashFlowEntry`, `SupplyCaseVersion`, `SupplyCommandReceipt` и Evidence contracts.
