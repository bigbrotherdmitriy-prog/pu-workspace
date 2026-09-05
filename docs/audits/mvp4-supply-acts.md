# MVP4 M4-07/M4-08: закупки, поставки и акты

Дата проверки: 2026-09-05

База: `45d7ef80e9b7569e94d76be559c4100aef14ad4e`

Ветка: `codex/mvp4-supply-acts`

## Решение

Реализован изолированный additive-модуль управления цепочкой:

`заявка → ручная проверка (если нужна) → согласование заявки → черновик заказа → согласование заказа → ручная фиксация размещения → поставка → проект акта → внутреннее согласование акта`.

Модуль не размещает заказ у поставщика, не подписывает акт, не проводит оплату, не создаёт внешнее действие и не вызывает provider API. Каждая операция возвращает `external_action_created=false`; в БД действует CHECK `external_action_status='not_created'`.

## Что переиспользовано

- существующие `Project`, `Contract`, `ScheduleBaseline`, `ScheduleItem`, `Task`;
- `DocumentVersion → SourceVersion → Evidence` как точная цепочка происхождения;
- `EvidenceAssessment` для freshness/availability/human verification;
- существующий `AuditLog`, но только с безопасными кодами событий и `details=NULL`;
- обычная транзакция SQLAlchemy, row lock и проектный lock — без второй очереди и без отдельного action ledger.

Legacy `ProcurementItem` и `AcceptanceAct` не изменялись: у них нет CAS, immutable history и exact evidence links. Автоматического backfill между legacy и новым модулем нет, чтобы не создать неподтверждённые юридические или финансовые факты.

## Инварианты

- все ссылки принадлежат одному `organization_id/project_id`;
- договор принадлежит проекту;
- этап принадлежит указанному утверждённому baseline, версия baseline совпадает точно;
- задача принадлежит проекту;
- Evidence revision равен `1`, совпадает с точным SourceVersion;
- SourceVersion указывает на тот же `DocumentVersion`, а Document принадлежит проекту;
- пустой locator, stale/unavailable evidence и несовпадение версии дают fail-closed;
- низкая confidence создаёт только `needs_review`; продолжение требует человека;
- редактор не может согласовать заявку, заказ, расхождение или акт даже при прямом вызове service;
- CAS проверяет `expected_version` под row lock;
- один `command_key` с тем же payload возвращает immutable receipt; другой payload даёт conflict;
- история и receipts append-only;
- поставка сверх заказа без явного discrepancy запрещена;
- discrepancy блокирует акт; превышение заказа требует отдельной корректировки заказа;
- принять больше фактически поставленного нельзя;
- денежное/юридическое согласование является только внутренним статусом и не создаёт внешний эффект.

## Изменённые файлы

- `backend/app/mvp4/__init__.py`
- `backend/app/mvp4/supply/__init__.py`
- `backend/app/mvp4/supply/contracts.py`
- `backend/app/mvp4/supply/models.py`
- `backend/app/mvp4/supply/service.py`
- `backend/app/mvp4/supply/router.py`
- `backend/tests/test_mvp4_supply_acts.py`
- `frontend/src/modules/supply/supplyReadModel.ts`
- `frontend/src/modules/supply/SupplyChainPanel.tsx`
- `frontend/src/modules/supply/SupplyChainPanel.css`
- `frontend/src/modules/supply/SupplyChainPanel.test.tsx`
- `docs/audits/mvp4-supply-acts.md`

`backend/app/api/execution_finance.py`, `frontend/src/App.tsx`, jobs, Gmail, storage, schema pins и Alembic не менялись.

## Schema-owner request

Эта ветка намеренно не содержит Alembic migration. Интегратору после определения актуальной единственной head нужно создать **одну последовательную** миграцию со следующими таблицами:

1. `mvp4_supply_cases` — текущая CAS-проекция, exact links, quantities, review/approval fields и запрет внешнего действия;
2. `mvp4_supply_case_versions` — immutable snapshots, UNIQUE `(supply_case_id, sequence)`;
3. `mvp4_supply_command_receipts` — immutable idempotency receipts, UNIQUE `(supply_case_id, command_key)`.

Полная схема, типы, FK, CHECK и имена constraints заданы декларативно в `backend/app/mvp4/supply/models.py`. `down_revision` должен быть равен head интеграционной ветки на момент переноса; существующие миграции переписывать нельзя. Downgrade допустим только для пустых новых таблиц либо после отдельного сохранения их истории.

После миграции интегратору нужно:

1. добавить явный import трёх моделей в `backend/app/models/__init__.py`;
2. подключить `app.mvp4.supply.router.router` через `include_router` в `backend/app/main.py`;
3. обновить единственный schema/readiness pin;
4. встроить `SupplyChainPanel` в выбранный экран, не копируя логику действий в `App.tsx`;
5. выполнить Alembic upgrade на чистой PostgreSQL и конкурентный replay/CAS тест двумя sessions.

## Покрытие

Синтетические тесты проверяют:

- полный и частичный цикл поставки/приёмки;
- exact project/contract/GPR/task/DocumentVersion/Evidence links;
- low confidence и human review;
- stale CAS, повтор запроса и collision idempotency key;
- невозможность согласования редактором;
- неверную/устаревшую evidence;
- превышение заказа и поставки;
- discrepancy и блокировку акта;
- immutable history/receipt;
- отсутствие business content в AuditLog;
- UI-состояния, manager-only actions и видимую границу внешних действий.

Результаты:

- новые backend-тесты: `21 passed`;
- связанный Contract/GPR/DDS/Source-Evidence regression: `98 passed`;
- полный backend: `1269 passed, 19 skipped` (skip — существующие conditional PostgreSQL/окружение);
- новые UI-тесты: `5 passed`;
- полный frontend: `149 passed`;
- TypeScript check: PASS;
- production frontend build: PASS;
- сгенерированные `react_dist` artifacts восстановлены после проверки и в коммит не входят.

## Остаточные ограничения

- PostgreSQL migration и конкурентность не проверяются до работы schema-owner;
- router и UI намеренно не активированы в приложении;
- интеграция со складом, ЭДО, подписью, оплатой и поставщиком отсутствует;
- исправление утверждённого количества заказа должно быть отдельным versioned процессом и сейчас fail-closed;
- legacy-записи требуют отдельного human-reviewed reconciliation, не автоматического backfill;
- документные fixtures полностью синтетические; реальные документы и production не использовались.
