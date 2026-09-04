# PU Workspace — единый кандидат MVP1–MVP5

Дата: 2026-09-04  
Ветка: `codex/mvp1-5-unified`  
База production: `df665097517deb05e57a7f5ca66a6c05fea11ed6`  
Alembic head: `d04e8a6c31f2`

## Решение

Кодовые и синтетические приёмочные критерии MVP1–MVP5 сведены в один кандидат.
Ни один внешний эффект не выполняется без подтверждения человека либо узкой
версионированной серверной политики. Production и реальные provider-аккаунты
этой проверкой не изменялись.

| Этап | Статус кандидата | Закрытый результат |
|---|---|---|
| MVP1 | CODE / OFFLINE PASS | Точный provider locator, metadata snapshot без копирования, отдельный safe-copy, идемпотентные rename/move/rollback, защита удаления договоров и stale picker |
| MVP2 | CODE / OFFLINE PASS | Почта → подтверждённый контекст → предложения задач/рисков/ответа; редактирование аннулирует approval; фильтрация no-reply; повтор не создаёт дубли |
| MVP3 | CODE / OFFLINE PASS | CAS для управленческих сущностей, append-only история, атомарный протокол встречи, конфликты контактов, пагинация, IANA/DST/quiet-hours и безопасные предложения эскалации |
| MVP4 | CODE / OFFLINE PASS | Decimal(18,2), строгие валюты, раздельные валютные итоги, append-only payment ledger, version/SHA evidence pin, CPM FS/SS/FF/SF+lag и ограничения ALAP/SNLT/FNLT |
| MVP5 | CODE / CONTRACT / ISOLATED RUNTIME PASS | Evidence/Context/Trust/Action Ledger, CONFIRM, узкий policy-bound AUTO, durable queue, idempotency, reconciliation, encrypted staging и fail-closed source access |

## Текущие автоматические доказательства

- полный backend: `1391 passed, 24 skipped` (после добавления двух новых
  PostgreSQL-only finance gates; исходный полный прогон до их добавления —
  `1391 passed, 22 skipped`);
- полный frontend unit: `182 passed` в `31` файле;
- browser E2E: `20 passed`;
- схемные и миграционные проверки: `100 passed, 5 skipped`;
- целевые MVP3/MVP4: `29 passed, 2 skipped`;
- workflow и release-контракты: `34 passed`;
- TypeScript application и E2E: PASS;
- production frontend build: PASS;
- Alembic: один head `d04e8a6c31f2`, offline PostgreSQL SQL generation PASS;
- `git diff --check`: PASS.

Пропуски включают только явно environment-gated PostgreSQL, platform и live
provider сценарии. Четыре конкурентных PostgreSQL-теста MVP3/MVP4 добавлены в
изолированный `Docker smoke` и должны пройти на Linux/PostgreSQL до выпуска.

## Релизные границы

Кандидат нельзя автоматически приравнивать к live-provider или production PASS.
Перед включением production обязательны:

1. зелёный GitHub `Docker smoke` для точного SHA, включая четыре новых
   PostgreSQL concurrency gate;
2. отдельная приёмка Google/Gmail/Tasks/Calendar и других подключённых
   провайдеров на тестовых аккаунтах;
3. координационное разрешение на production deployment после подготовки
   EU-primary;
4. миграция, smoke, backup/restore и rollback-проверка в изолированном контуре.

До выполнения этих gates статус единого кандидата: **MVP1–MVP5 CODE PASS;
PRODUCTION ENABLE HELD**.
