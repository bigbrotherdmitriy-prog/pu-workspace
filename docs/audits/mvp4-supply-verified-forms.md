# MVP4 Supply verified forms — результат

Дата: 2026-09-05

База: `4edc248ba380d5a13b31c31d766f41d9cf52e65a`

Ветка: `codex/mvp4-supply-verified-forms`

## Результат

SupplyCenter получил специализированные формы для `review`, `prepare_order`,
`record_order`, `record_delivery`, `resolve_discrepancy` и `propose_act`.
Формы сохраняют fail-closed контракт: доказательство пользователь выбирает заново
для конкретной команды, а имя файла и доказательство предыдущего шага не
используются как подтверждение.

Поскольку безопасного каталога Evidence для выбора не было, добавлен минимальный
read-only endpoint `GET /api/v54/evidence?project_id=...`. Он проверяет роль в
проекте и возвращает только exact-current, fresh и неистёкшие pins без имени,
содержимого документа, provider locator или абсолютного пути.

## Проверенные свойства

- quantity принимает не более трёх знаков после запятой;
- money принимает не более двух знаков после запятой;
- расхождение поставки требует явных code и note;
- исправление review требует явного решения и исправленных значений;
- mutation отправляет `expected_version` из текущего `recordVersion`;
- один ключ передаётся одновременно как `Idempotency-Key` и `command_key`;
- backend отклоняет несовпадающие ключи с 409;
- перед командой повторно проверяются активные project и organization;
- 409 приводит к обновлению списка и понятному сообщению о конфликте;
- Evidence payload содержит `evidence_id`, `revision`, `source_version_id` и
  `document_version_id` выбранной записи;
- неподтверждённое Evidence нельзя выбрать для mutation;
- внешние и финансовые AUTO-действия не включены.

## Проверки

- backend acceptance/evidence/supply: `44 passed` до финального локального
  idempotency guard; новый guard покрыт отдельным regression-тестом;
- финальный backend rerun не выполнен из-за отсутствующего локального Python 3.13
  runtime/native `libpq`; это ограничение среды, не засчитано как PASS;
- финальный targeted frontend: `14 passed`;
- полный frontend Vitest: `182 passed`;
- TypeScript check: PASS;
- production frontend build: PASS (только существующее предупреждение о размере chunk);
- `git diff --check`: PASS.

PostgreSQL, browser E2E и живые провайдеры не запускались. Эти сценарии остаются
`CONDITIONAL` и должны быть повторены интегратором в изолированном CI.

## Ограничения

- каталог предназначен только для выбора pins и не является просмотрщиком
  содержимого; просмотр фрагмента остаётся в отдельном Evidence UI;
- создание первоначальной supply request не расширялось;
- конкурентный CAS на PostgreSQL не подтверждён локально;
- реальные документы, Google, Gmail, Telegram и production не использовались;
- модели, миграции, schema pins, очередь и `App.tsx` не менялись.
